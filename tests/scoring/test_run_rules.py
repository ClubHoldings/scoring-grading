"""Rules run end to end on invented sample homes (DuckDB), through ``run_rules`` only."""

from __future__ import annotations

import datetime as dt

from scoring.engine import run_rules
from scoring.executor import DuckDbExecutor
from scoring.model import RunSettings
from scoring.ruleset_loader import parse_ruleset


def _exempt(photo_rule: str, exempt: str) -> str:
    return photo_rule.replace("version", f"coords_exempt_sources = {exempt}\nversion", 1)


def test_tracer_one_rule_scores_every_home_and_stores_only_the_failure(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str
) -> None:
    result = run_rules(
        executor,
        RunSettings(ruleset_version="v0.9", as_of=as_of),
        ruleset=parse_ruleset(photo_rule),
    )

    # Homes 6 (inactive) and 7 (retired) are outside the sellable population.
    assert result.homes_scored == 5
    failures = executor.fetch(
        "SELECT property_uid, rule_id, points_lost, found_value FROM results.rule_failure"
    )
    assert failures == [(2, "MED_PHOTO_1", 5, "photos=0")]
    scores = dict(executor.fetch("SELECT property_uid, score FROM results.home_score"))
    assert scores == {1: 100.0, 2: 0.0, 3: 100.0, 4: 100.0, 5: 100.0}


def test_not_applicable_rule_leaves_the_home_total_and_is_recorded(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str, coords_rule: str
) -> None:
    text = photo_rule.replace(
        'note = "test"', 'note = "test"\ncoords_exempt_sources = ["SRC_EXEMPT"]'
    )
    run_rules(
        executor,
        RunSettings(ruleset_version="v0.9", as_of=as_of),
        ruleset=parse_ruleset(text + coords_rule),
    )

    # Home 5 is exempt: 5 points available, 5 earned. Homes 3 and 4 fail coordinates only.
    scores = {
        r[0]: r[1:]
        for r in executor.fetch(
            "SELECT property_uid, points_available, points_earned, score, min_req_failed "
            "FROM results.home_score"
        )
    }
    assert scores == {
        1: (10, 10, 100.0, 0),
        2: (10, 0, 0.0, 1),
        3: (10, 5, 50.0, 1),
        4: (10, 5, 50.0, 1),
        5: (5, 5, 100.0, 0),
    }
    na = executor.fetch("SELECT property_uid, rule_id, points FROM results.rule_not_applicable")
    assert na == [(5, "LOC_COORDS", 5)]
    failed = executor.fetch(
        "SELECT property_uid, rule_id FROM results.rule_failure ORDER BY property_uid, rule_id"
    )
    assert failed == [
        (2, "LOC_COORDS"),
        (2, "MED_PHOTO_1"),
        (3, "LOC_COORDS"),
        (4, "LOC_COORDS"),
    ]


def test_home_with_no_applicable_points_gets_no_score(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str, coords_rule: str
) -> None:
    header = photo_rule.split("[[rule]]")[0].replace(
        'note = "test"', 'note = "test"\ncoords_exempt_sources = ["SRC_EXEMPT"]'
    )
    run_rules(
        executor,
        RunSettings(ruleset_version="v0.9", as_of=as_of),
        ruleset=parse_ruleset(header + coords_rule),
    )

    score = executor.fetch(
        "SELECT points_available, score FROM results.home_score WHERE property_uid = 5"
    )
    assert score == [(0, None)]


def test_run_row_records_how_the_run_was_made(
    executor: DuckDbExecutor, as_of: dt.date, photo_rule: str
) -> None:
    result = run_rules(
        executor,
        RunSettings(ruleset_version="v0.9", as_of=as_of, trigger="nightly"),
        ruleset=parse_ruleset(photo_rule),
    )

    row = executor.fetch(
        "SELECT run_id, ruleset_version, trigger, as_of, enabled_flags, homes_scored, "
        "points_available FROM results.run"
    )
    assert row == [(result.run_id, "v0.9", "nightly", as_of, "", 5, 5)]
