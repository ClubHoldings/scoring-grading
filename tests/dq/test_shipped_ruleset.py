"""The shipped v0.1 ruleset on the sample homes.

Expected scores were worked out by hand from the sample data in conftest, one home at a time,
not recomputed with the engine's own formulas.
"""

from __future__ import annotations

import datetime as dt

from dq.engine import run_rules
from dq.executor import DuckDbExecutor
from dq.model import RunSettings
from dq.ruleset_loader import load_ruleset, parse_ruleset

SCORES_FLAGS_OFF = {1: 100.0, 2: 23.75, 3: 28.75, 4: 87.5, 5: 56.25}
MIN_FAILED = {1: 0, 2: 6, 3: 6, 4: 2, 5: 1}


def _home_scores(ex: DuckDbExecutor) -> dict[int, float]:
    return dict(ex.fetch("SELECT property_uid, score FROM results.home_score"))


def test_v0_1_scores_sample_homes_with_default_flags(
    executor: DuckDbExecutor, as_of: dt.date
) -> None:
    result = run_rules(executor, RunSettings("v0.1", as_of))

    assert result.points_available == 80  # address and fee rules are off
    assert _home_scores(executor) == SCORES_FLAGS_OFF
    assert (
        dict(executor.fetch("SELECT property_uid, min_req_failed FROM results.home_score"))
        == MIN_FAILED
    )


def test_missing_data_fails_and_still_counts(executor: DuckDbExecutor, as_of: dt.date) -> None:
    run_rules(executor, RunSettings("v0.1", as_of))

    # Home 3 has no summary, no sleeps, no type: 0 points each, nothing skipped.
    failed = {
        r[0]
        for r in executor.fetch("SELECT rule_id FROM results.rule_failure WHERE property_uid = 3")
    }
    assert {"CON_SUMMARY", "OCC_SLEEPS", "CON_TYPE"} <= failed
    assert executor.fetch(
        "SELECT points_available FROM results.home_score WHERE property_uid = 3"
    ) == [(80,)]


def test_shared_slug_fails_both_homes(executor: DuckDbExecutor, as_of: dt.date) -> None:
    run_rules(executor, RunSettings("v0.1", as_of))

    rows = executor.fetch(
        "SELECT property_uid, found_value FROM results.rule_failure "
        "WHERE rule_id = 'CON_SLUG_UNIQUE' ORDER BY property_uid"
    )
    assert rows == [(3, "slug shared by 2 homes"), (4, "slug shared by 2 homes")]


def test_address_and_fee_flags_add_their_rules(executor: DuckDbExecutor, as_of: dt.date) -> None:
    result = run_rules(
        executor, RunSettings("v0.1", as_of, enabled_flags=frozenset({"address", "fees"}))
    )

    assert result.points_available == 100  # every dimension now adds up to its weight
    assert _home_scores(executor) == {1: 100.0, 2: 19.0, 3: 23.0, 4: 82.0, 5: 49.0}
    assert executor.fetch("SELECT enabled_flags FROM results.run") == [("address,fees",)]


def test_exempt_source_is_flagged_not_applicable_for_coordinates(
    executor: DuckDbExecutor, as_of: dt.date
) -> None:
    text = load_ruleset("v0.1") and __import__("importlib.resources", fromlist=["files"]).files(
        "dq.rulesets"
    ).joinpath("v0_1.toml").read_text(encoding="utf-8")
    exempt = parse_ruleset(
        text.replace("coords_exempt_sources = []", 'coords_exempt_sources = ["SRC_EXEMPT"]')
    )

    run_rules(executor, RunSettings("v0.1", as_of), ruleset=exempt)

    assert executor.fetch(
        "SELECT property_uid, rule_id, points FROM results.rule_not_applicable"
    ) == [(5, "LOC_COORDS", 5)]
    # Home 5: available 75, lost 30 (amenities 15, photos 10, bathrooms 5), no minimum failed.
    assert executor.fetch(
        "SELECT points_available, score, min_req_failed FROM results.home_score "
        "WHERE property_uid = 5"
    ) == [(75, 60.0, 0)]
