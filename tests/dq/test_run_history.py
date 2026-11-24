"""Runs are repeatable and append-only; rulesets are immutable; a failed run leaves no trace."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import duckdb
import pytest

from dq.engine import run_rules
from dq.errors import (
    ConfigurationError,
    ExecutionError,
    InvalidTriggerError,
    RulesetChangedError,
    UnknownRulesetError,
)
from dq.executor import DuckDbExecutor
from dq.model import RunSettings
from dq.ruleset_loader import parse_ruleset
from dq.schema import Dialect

RESULT_QUERIES = {
    "home_score": "SELECT * EXCLUDE (run_id) FROM results.home_score ORDER BY property_uid",
    "rule_failure": "SELECT * EXCLUDE (run_id) FROM results.rule_failure "
    "ORDER BY property_uid, rule_id",
    "rule_not_applicable": "SELECT * EXCLUDE (run_id) FROM results.rule_not_applicable "
    "ORDER BY property_uid, rule_id",
}


class RecordingExecutor:
    """Passes every statement to a real DuckDB executor and remembers it."""

    dialect: Dialect = "duckdb"

    def __init__(self, inner: DuckDbExecutor) -> None:
        self.inner = inner
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(sql)
        self.inner.execute(sql)

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        self.statements.append(sql)
        return self.inner.fetch(sql)

    def ensure_namespace(self, name: str) -> None:
        self.inner.ensure_namespace(name)

    def materialise(self, name: str, select_sql: str) -> None:
        self.statements.append(f"CREATE {name}")
        self.inner.materialise(name, select_sql)

    def drop(self, name: str) -> None:
        self.statements.append(f"DROP {name}")
        self.inner.drop(name)


def test_same_input_gives_same_results_and_both_runs_are_kept(
    executor: DuckDbExecutor, as_of: dt.date
) -> None:
    settings = RunSettings("v0.1", as_of)
    first = run_rules(executor, settings)
    first_rows = {k: executor.fetch(q) for k, q in RESULT_QUERIES.items()}

    second = run_rules(executor, settings)

    assert first.run_id != second.run_id
    for table, query in RESULT_QUERIES.items():
        per_run = [
            executor.fetch(query.replace("ORDER BY", f"WHERE run_id = '{rid}' ORDER BY"))
            for rid in (first.run_id, second.run_id)
        ]
        assert per_run[0] == per_run[1] == first_rows[table], table
    assert executor.fetch("SELECT count(*) FROM results.run") == [(2,)]


def test_engine_never_updates_or_deletes_result_rows(
    executor: DuckDbExecutor, as_of: dt.date
) -> None:
    rec = RecordingExecutor(executor)

    run_rules(rec, RunSettings("v0.1", as_of))
    run_rules(rec, RunSettings("v0.1", as_of))

    assert not [s for s in rec.statements if re.match(r"\s*(UPDATE|DELETE|TRUNCATE)", s, re.I)]
    drops = [s for s in rec.statements if s.startswith("DROP")]
    assert drops and all("stg_" in s for s in drops)


def test_rerun_with_new_data_does_not_change_the_earlier_run(
    executor: DuckDbExecutor, as_of: dt.date, con: duckdb.DuckDBPyConnection
) -> None:
    first = run_rules(executor, RunSettings("v0.1", as_of))
    before = executor.fetch(
        f"SELECT property_uid, score FROM results.home_score WHERE run_id = '{first.run_id}' "
        "ORDER BY property_uid"
    )

    con.execute("INSERT INTO silver.photo VALUES (2, 'new')")
    run_rules(executor, RunSettings("v0.1", as_of))

    after = executor.fetch(
        f"SELECT property_uid, score FROM results.home_score WHERE run_id = '{first.run_id}' "
        "ORDER BY property_uid"
    )
    assert after == before


def test_changed_content_under_same_version_is_refused(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str
) -> None:
    settings = RunSettings("v0.9", as_of)
    run_rules(executor, settings, ruleset=parse_ruleset(photo_rule))

    with pytest.raises(RulesetChangedError):
        run_rules(
            executor,
            settings,
            ruleset=parse_ruleset(photo_rule.replace("points = 5", "points = 6")),
        )

    # An identical file is fine and is not stored twice.
    run_rules(executor, settings, ruleset=parse_ruleset(photo_rule))
    assert executor.fetch("SELECT count(*) FROM results.ruleset") == [(1,)]
    assert executor.fetch("SELECT count(*) FROM results.rule") == [(1,)]


def test_registered_rules_keep_points_and_flags(executor: DuckDbExecutor, as_of: dt.date) -> None:
    run_rules(executor, RunSettings("v0.1", as_of))

    rows = executor.fetch(
        "SELECT points, is_minimum_requirement, requires_flag FROM results.rule "
        "WHERE rule_id IN ('FEE_TAX', 'LOC_COORDS') ORDER BY rule_id"
    )
    assert rows == [(3, False, "fees"), (5, True, None)]


def test_failed_run_leaves_no_run_row_and_no_staging_tables(
    executor: DuckDbExecutor, as_of: dt.date, con: duckdb.DuckDBPyConnection
) -> None:
    con.execute("DROP TABLE silver.room")

    with pytest.raises(ExecutionError):
        run_rules(executor, RunSettings("v0.1", as_of))

    assert executor.fetch("SELECT count(*) FROM results.run") == [(0,)]
    left = executor.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'stg_%'"
    )
    assert left == []


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        (RunSettings("v0.1", dt.date(2026, 11, 24), trigger="whenever"), InvalidTriggerError),
        (
            RunSettings("v0.1", dt.date(2026, 11, 24), enabled_flags=frozenset({"nope"})),
            ConfigurationError,
        ),
        (RunSettings("v9.9", dt.date(2026, 11, 24)), UnknownRulesetError),
        (RunSettings("v0.1", dt.date(2026, 11, 24), source_schema="x; drop"), ConfigurationError),
    ],
)
def test_bad_settings_are_rejected_before_anything_is_written(
    executor: DuckDbExecutor, settings: RunSettings, error: type[Exception]
) -> None:
    with pytest.raises(error):
        run_rules(executor, settings)


def test_flags_with_no_rules_left_are_rejected(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str
) -> None:
    only_flagged = photo_rule.replace("points = 5", 'points = 5\nflag = "fees"')

    with pytest.raises(ConfigurationError):
        run_rules(executor, RunSettings("v0.9", as_of), ruleset=parse_ruleset(only_flagged))


def test_ruleset_override_must_match_the_settings_version(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str
) -> None:
    with pytest.raises(ConfigurationError):
        run_rules(executor, RunSettings("v0.1", as_of), ruleset=parse_ruleset(photo_rule))


def test_duplicate_source_rows_are_refused_not_double_counted(
    executor: DuckDbExecutor, as_of: dt.date, con: duckdb.DuckDBPyConnection
) -> None:
    con.execute(
        "INSERT INTO silver.property_information SELECT * FROM silver.property_information "
        "WHERE property_uid = 1"
    )

    with pytest.raises(ConfigurationError, match="more than one row"):
        run_rules(executor, RunSettings("v0.1", as_of))

    assert executor.fetch("SELECT count(*) FROM results.run") == [(0,)]


def test_comment_only_edit_keeps_the_same_ruleset_identity(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str
) -> None:
    settings = RunSettings("v0.9", as_of)
    run_rules(executor, settings, ruleset=parse_ruleset(photo_rule))

    run_rules(executor, settings, ruleset=parse_ruleset("# a comment\n" + photo_rule))

    assert executor.fetch("SELECT count(*) FROM results.ruleset") == [(1,)]
