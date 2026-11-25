"""Rules engine: register a ruleset, run all rules in one set-based pass, append the results.

Flow of ``run_rules``:
    1. Register the ruleset version (immutable: a changed file under the same version is refused).
    2. Materialise one feature row per home, then one table of every failed or not-applicable
       (home, rule) pair. Each rule is a SELECT over the feature table, joined with UNION ALL, so
       the data is read once for all homes and never once per home.
    3. Append rule failures, not-applicable rows and home scores, then the ``run`` row last.

Nothing here updates or deletes a result row, and nothing depends on the clock or on random
state except the run id and the started-at stamp, so the same input gives the same scores.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from score.errors import (
    ConfigurationError,
    InvalidTriggerError,
    RulesetChangedError,
)
from score.executor import Executor
from score.features import features_sql
from score.model import KNOWN_FLAGS, TRIGGERS, Rule, Ruleset, RunSettings
from score.ruleset_loader import load_ruleset
from score.schema import TABLES, create_table_sql, insert_select

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RunResult:
    """Summary of one finished run.

    Attributes:
        run_id: Id of the run, the key of every result row.
        ruleset_version: Version that was applied.
        homes_scored: Homes in the scored population.
        points_available: Points of all active rules, before not-applicable rules are removed.
        failures: Rule failure rows written.
        not_applicable: Rule not-applicable rows written.
    """

    run_id: str
    ruleset_version: str
    homes_scored: int
    points_available: int
    failures: int
    not_applicable: int


def _q(value: str) -> str:
    """Quote a string literal, doubling embedded single quotes."""
    return "'" + value.replace("'", "''") + "'"


def _opt(value: str | None) -> str:
    """Quote a string literal, or NULL when there is no value."""
    return "NULL" if value is None else _q(value)


def init_schema(
    executor: Executor, results_schema: str, athena_location: str | None = None
) -> None:
    """Create the result schema and tables if missing. Safe to call on every run.

    Args:
        executor: Backend.
        results_schema: Schema (database) for the result tables.
        athena_location: Athena only. ``s3://bucket/prefix`` where Iceberg tables live.
    """
    executor.ensure_namespace(results_schema)
    for table in TABLES:
        executor.execute(create_table_sql(results_schema, table, executor.dialect, athena_location))


def register_ruleset(
    executor: Executor, ruleset: Ruleset, results_schema: str, now: dt.datetime
) -> bool:
    """Store the ruleset and its rules unless this exact version is already stored.

    Args:
        executor: Backend.
        ruleset: Ruleset to store.
        results_schema: Schema of the result tables.
        now: Registration time.

    Returns:
        True when the version was newly stored, False when it already existed unchanged.

    Raises:
        RulesetChangedError: the version exists with a different content hash.
    """
    rows = executor.fetch(
        f"SELECT content_hash FROM {results_schema}.ruleset "
        f"WHERE ruleset_version = {_q(ruleset.version)}"
    )
    if rows:
        if str(rows[0][0]) != ruleset.content_hash:
            raise RulesetChangedError(
                f"{ruleset.version} is registered with different content; use a new version"
            )
        return False
    executor.execute(
        insert_select(
            results_schema,
            "ruleset",
            {
                "ruleset_version": _q(ruleset.version),
                "note": _q(ruleset.note),
                "content_hash": _q(ruleset.content_hash),
                "sellable_statuses": _q(",".join(ruleset.sellable_statuses)),
                "coords_exempt_sources": _q(",".join(ruleset.coords_exempt_sources)),
                "created_at": _ts(now),
            },
            "",
            executor.dialect,
        )
    )
    for r in ruleset.rules:
        executor.execute(
            insert_select(
                results_schema,
                "rule",
                {
                    "ruleset_version": _q(ruleset.version),
                    "rule_id": _q(r.rule_id),
                    "dimension": _q(r.dimension.value),
                    "description": _q(r.description),
                    "points": str(r.points),
                    "is_minimum_requirement": "TRUE" if r.is_minimum_requirement else "FALSE",
                    "requires_flag": _opt(r.requires_flag),
                    "check_sql": _q(r.check_sql),
                    "found_sql": _q(r.found_sql),
                    "applies_sql": _opt(r.applies_sql),
                },
                "",
                executor.dialect,
            )
        )
    return True


def _ts(value: dt.datetime) -> str:
    """Render a timestamp literal."""
    return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"


def _rule_select(rule: Rule, features: str) -> str:
    """One branch of the evaluation query: the (home, rule) pairs that fail or do not apply."""
    applies = rule.applies_sql or "TRUE"
    return (
        f"SELECT h.property_uid, {_q(rule.rule_id)} AS rule_id, "
        f"{_q(rule.dimension.value)} AS dimension, "
        f"{rule.points} AS points, {1 if rule.is_minimum_requirement else 0} AS is_min, "
        f"CASE WHEN NOT COALESCE(({applies}), TRUE) THEN 'NA' ELSE 'FAIL' END AS outcome, "
        f"CASE WHEN COALESCE(({applies}), TRUE) "
        f"THEN CAST(({rule.found_sql}) AS VARCHAR) END AS found_value "
        f"FROM {features} h "
        f"WHERE NOT COALESCE(({applies}), TRUE) OR NOT COALESCE(({rule.check_sql}), FALSE)"
    )


def evaluation_sql(rules: tuple[Rule, ...], features: str) -> str:
    """Build the single query that finds every failed or not-applicable (home, rule) pair.

    Args:
        rules: Active rules, at least one.
        features: Name of the materialised feature table.

    Returns:
        SQL text. Passes are not returned: a pass is implied.

    Raises:
        ConfigurationError: no rules.
    """
    if not rules:
        raise ConfigurationError("no active rules for these flags")
    return "\nUNION ALL\n".join(_rule_select(r, features) for r in rules)


def _validate(settings: RunSettings) -> None:
    """Check trigger and flags before anything is written.

    Raises:
        InvalidTriggerError: unknown trigger.
        ConfigurationError: unknown flag.
    """
    if settings.trigger not in TRIGGERS:
        raise InvalidTriggerError(settings.trigger)
    unknown = settings.enabled_flags - KNOWN_FLAGS
    if unknown:
        raise ConfigurationError(f"unknown flags: {sorted(unknown)}")


def _count(executor: Executor, sql: str) -> int:
    """Run a count query and return the number."""
    return int(executor.fetch(sql)[0][0])


def run_rules(
    executor: Executor,
    settings: RunSettings,
    athena_location: str | None = None,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC).replace(tzinfo=None),
    ruleset: Ruleset | None = None,
) -> RunResult:
    """Score every home and append the results as one new run.

    Args:
        executor: Backend with the source tables loaded.
        settings: Ruleset version, as-of date, trigger, flags and schemas.
        athena_location: Athena only. S3 prefix for the Iceberg result tables.
        now: Clock, replaceable in tests.
        ruleset: Use this ruleset instead of loading ``settings.ruleset_version`` from the package.

    Returns:
        Summary of the run.

    Raises:
        InvalidTriggerError: bad trigger.
        ConfigurationError: bad flags, cutoff or no active rules.
        UnknownRulesetError: no such ruleset version.
        RulesetChangedError: the version exists with different content.
        ExecutionError: the backend failed. Result rows written so far stay unreferenced
            because the ``run`` row is written last.
    """
    _validate(settings)
    ruleset = ruleset or load_ruleset(settings.ruleset_version)
    if ruleset.version != settings.ruleset_version:
        raise ConfigurationError(
            f"settings ask for {settings.ruleset_version} but the ruleset is {ruleset.version}"
        )
    started = now()
    schema = settings.results_schema
    init_schema(executor, schema, athena_location)
    register_ruleset(executor, ruleset, schema, started)

    rules = ruleset.active_rules(settings.enabled_flags)
    run_id = uuid.uuid4().hex
    stage_features = f"{schema}.stg_features_{run_id[:12]}"
    stage_eval = f"{schema}.stg_eval_{run_id[:12]}"
    available = sum(r.points for r in rules)
    try:
        executor.materialise(
            stage_features,
            features_sql(ruleset, settings.source_schema, settings.as_of, settings.enabled_flags),
        )
        _refuse_duplicate_homes(executor, stage_features)
        executor.materialise(stage_eval, evaluation_sql(rules, stage_features))
        failures = _append_failures(executor, schema, run_id, stage_eval)
        na = _append_not_applicable(executor, schema, run_id, stage_eval)
        homes = _append_home_scores(executor, schema, run_id, stage_features, stage_eval, available)
        _append_run(executor, schema, run_id, ruleset.version, settings, started, homes, available)
    finally:
        executor.drop(stage_eval)
        executor.drop(stage_features)
    result = RunResult(run_id, ruleset.version, homes, available, failures, na)
    logger.info(
        "score_run_complete",
        run_id=run_id,
        ruleset=ruleset.version,
        trigger=settings.trigger,
        homes=homes,
        failures=failures,
        not_applicable=na,
        flags=sorted(settings.enabled_flags),
    )
    return result


def _refuse_duplicate_homes(executor: Executor, stage_features: str) -> None:
    """Stop a run whose source tables hold more than one row per home.

    A join fan-out would score a home twice and break repeatable results, so it is refused
    before any result row is written.

    Raises:
        ConfigurationError: the feature table has a repeated home.
    """
    dup = _count(executor, f"SELECT count(*) - count(DISTINCT property_uid) FROM {stage_features}")
    if dup:
        raise ConfigurationError(
            f"source tables have more than one row per home ({dup} extra rows); "
            "a latest-snapshot filter is needed upstream"
        )


def _append_failures(executor: Executor, schema: str, run_id: str, stage: str) -> int:
    executor.execute(
        insert_select(
            schema,
            "rule_failure",
            {
                "run_id": _q(run_id),
                "property_uid": "property_uid",
                "rule_id": "rule_id",
                "dimension": "dimension",
                "points_lost": "points",
                "found_value": "found_value",
            },
            f"FROM {stage} WHERE outcome = 'FAIL'",
            executor.dialect,
        )
    )
    return _count(
        executor, f"SELECT count(*) FROM {schema}.rule_failure WHERE run_id = {_q(run_id)}"
    )


def _append_not_applicable(executor: Executor, schema: str, run_id: str, stage: str) -> int:
    executor.execute(
        insert_select(
            schema,
            "rule_not_applicable",
            {
                "run_id": _q(run_id),
                "property_uid": "property_uid",
                "rule_id": "rule_id",
                "points": "points",
            },
            f"FROM {stage} WHERE outcome = 'NA'",
            executor.dialect,
        )
    )
    return _count(
        executor, f"SELECT count(*) FROM {schema}.rule_not_applicable WHERE run_id = {_q(run_id)}"
    )


def _append_home_scores(
    executor: Executor,
    schema: str,
    run_id: str,
    stage_features: str,
    stage_eval: str,
    available: int,
) -> int:
    pa = f"({available} - COALESCE(na.pts, 0))"
    earned = f"({pa} - COALESCE(lost.pts, 0))"
    score = f"ROUND(100.0 * {earned} / {pa}, 2)"
    executor.execute(
        insert_select(
            schema,
            "home_score",
            {
                "run_id": _q(run_id),
                "property_uid": "f.property_uid",
                "points_available": pa,
                "points_lost": "COALESCE(lost.pts, 0)",
                "score": f"CASE WHEN {pa} > 0 THEN {score} END",
                "min_req_failed": "COALESCE(lost.min_failed, 0)",
            },
            f"FROM {stage_features} f "
            f"LEFT JOIN (SELECT property_uid, SUM(points) AS pts FROM {stage_eval} "
            f"WHERE outcome = 'NA' GROUP BY property_uid) na ON na.property_uid = f.property_uid "
            f"LEFT JOIN (SELECT property_uid, SUM(points) AS pts, SUM(is_min) AS min_failed "
            f"FROM {stage_eval} WHERE outcome = 'FAIL' GROUP BY property_uid) lost "
            f"ON lost.property_uid = f.property_uid",
            executor.dialect,
        )
    )
    return _count(executor, f"SELECT count(*) FROM {schema}.home_score WHERE run_id = {_q(run_id)}")


def _append_run(
    executor: Executor,
    schema: str,
    run_id: str,
    version: str,
    settings: RunSettings,
    started: dt.datetime,
    homes: int,
    available: int,
) -> None:
    executor.execute(
        insert_select(
            schema,
            "run",
            {
                "run_id": _q(run_id),
                "ruleset_version": _q(version),
                "trigger": _q(settings.trigger),
                "as_of": f"DATE '{settings.as_of.isoformat()}'",
                "enabled_flags": _q(",".join(sorted(settings.enabled_flags))),
                "run_at": _ts(started),
                "homes_scored": str(homes),
                "points_available": str(available),
            },
            "",
            executor.dialect,
        )
    )
