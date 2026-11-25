"""The shipped v0.1 ruleset on the sample homes.

Expected scores were worked out by hand from the sample data in conftest, one home at a time,
not recomputed with the engine's own formulas.
"""

from __future__ import annotations

import datetime as dt

import duckdb

from score.engine import run_rules
from score.executor import DuckDbExecutor
from score.model import RunSettings
from score.ruleset_loader import load_ruleset

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


def test_shipped_ruleset_exempts_its_listed_source_from_coordinates(
    executor: DuckDbExecutor, as_of: dt.date, con: duckdb.DuckDBPyConnection
) -> None:
    exempt_codes = load_ruleset("v0.1").coords_exempt_sources
    assert exempt_codes  # the business decision: exempt for now, flagged
    # Home 5 has no coordinates. Give it the exempt partner source.
    con.execute("INSERT INTO silver.property_source VALUES (?)", [exempt_codes[0]])
    con.execute(
        "UPDATE silver.property SET property_source_ucode = ? WHERE property_uid = 5",
        [exempt_codes[0]],
    )

    run_rules(executor, RunSettings("v0.1", as_of))

    assert executor.fetch(
        "SELECT property_uid, rule_id, points FROM results.rule_not_applicable"
    ) == [(5, "LOC_COORDS", 5)]
    # Home 5: available 75, lost 30 (amenities 15, photos 10, bathrooms 5), no minimum failed.
    assert executor.fetch(
        "SELECT points_available, score, min_req_failed FROM results.home_score "
        "WHERE property_uid = 5"
    ) == [(75, 60.0, 0)]


def test_home_without_description_does_not_earn_the_description_rules(
    executor: DuckDbExecutor, as_of: dt.date, con: duckdb.DuckDBPyConnection
) -> None:
    con.execute("DELETE FROM silver.property_information WHERE property_uid = 4")

    run_rules(executor, RunSettings("v0.1", as_of))

    failed = {
        r[0]
        for r in executor.fetch("SELECT rule_id FROM results.rule_failure WHERE property_uid = 4")
    }
    # Missing data earns 0 on every description rule, minimum requirements included.
    assert {"CON_DESC_LEN", "CON_NO_HTML", "CON_NO_CONTACT"} <= failed


def test_bathroom_count_on_the_info_record_passes_without_a_room_row(
    executor: DuckDbExecutor, as_of: dt.date, con: duckdb.DuckDBPyConnection
) -> None:
    # Home 5 has no bathroom room row, home 3 has none either; neither has a count on file yet.
    con.execute("UPDATE silver.property_information SET max_full_baths = 2 WHERE property_uid = 5")
    con.execute("UPDATE silver.property_information SET max_half_baths = 1 WHERE property_uid = 3")

    run_rules(executor, RunSettings("v0.1", as_of))

    failed = {
        r[0]
        for r in executor.fetch(
            "SELECT property_uid FROM results.rule_failure WHERE rule_id = 'OCC_BATHROOMS'"
        )
    }
    # Home 2 has no room row and no count: still fails. Homes 3 and 5 now pass.
    assert failed == {2}
