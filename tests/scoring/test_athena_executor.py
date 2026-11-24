"""AthenaExecutor against a small in-memory stand-in for the Athena API.

The stand-in only plays the AWS side (start, status, results); the executor's own polling,
error handling and result parsing are the code under test. No AWS call is made.
"""

from __future__ import annotations

from typing import Any

import pytest

from scoring.errors import ConfigurationError, ExecutionError
from scoring.executor import AthenaExecutor


class FakeAthena:
    def __init__(self, states: list[str], rows: list[list[str | None]] | None = None) -> None:
        self.states = states
        self.rows = rows or []
        self.queries: list[str] = []
        self.polls = 0

    def start_query_execution(self, QueryString: str, WorkGroup: str) -> dict[str, str]:
        self.queries.append(f"{WorkGroup}: {QueryString}")
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId: str) -> dict[str, Any]:
        state = self.states[min(self.polls, len(self.states) - 1)]
        self.polls += 1
        return {"QueryExecution": {"Status": {"State": state, "StateChangeReason": "boom"}}}

    def get_paginator(self, name: str) -> FakeAthena:
        assert name == "get_query_results"
        return self

    def paginate(self, QueryExecutionId: str) -> list[dict[str, Any]]:
        data = [
            {"Data": [{"VarCharValue": v} if v is not None else {} for v in r]} for r in self.rows
        ]
        return [{"ResultSet": {"Rows": data}}]


def _executor(fake: FakeAthena, timeout: float = 5.0) -> AthenaExecutor:
    return AthenaExecutor(
        "wg", "s3://bucket/stage/", poll_seconds=0, timeout_seconds=timeout, client=fake
    )


def test_execute_waits_until_the_query_succeeds() -> None:
    fake = FakeAthena(["QUEUED", "RUNNING", "SUCCEEDED"])

    _executor(fake).execute("SELECT 1")

    assert fake.polls == 3 and fake.queries == ["wg: SELECT 1"]


def test_fetch_drops_the_header_row_and_keeps_nulls() -> None:
    fake = FakeAthena(["SUCCEEDED"], [["n", "s"], ["3", None], ["4", "x"]])

    assert _executor(fake).fetch("SELECT n, s") == [("3", None), ("4", "x")]


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
def test_failed_query_raises_with_reason(state: str) -> None:
    with pytest.raises(ExecutionError, match="boom"):
        _executor(FakeAthena([state])).execute("SELECT 1")


def test_query_that_never_finishes_times_out() -> None:
    with pytest.raises(ExecutionError, match="timed out"):
        _executor(FakeAthena(["RUNNING"]), timeout=0).execute("SELECT 1")


def test_staging_table_is_ctas_to_the_staging_prefix_and_dropped() -> None:
    fake = FakeAthena(["SUCCEEDED"])
    ex = _executor(fake)

    ex.materialise("results.stg_x", "SELECT 1")
    ex.ensure_namespace("results")

    assert fake.queries[0].endswith("DROP TABLE IF EXISTS results.stg_x")
    assert "external_location = 's3://bucket/stage/results.stg_x/'" in fake.queries[1]
    assert fake.queries[2].endswith("CREATE DATABASE IF NOT EXISTS results")


def test_staging_location_must_be_s3() -> None:
    with pytest.raises(ConfigurationError):
        AthenaExecutor("wg", "/tmp/x", client=FakeAthena([]))
