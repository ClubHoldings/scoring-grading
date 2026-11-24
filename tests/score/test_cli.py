"""The run command: arguments in, results appended, exit code out."""

from __future__ import annotations

import json

import duckdb
import pytest

from score.cli import main


def _last_log(capsys: pytest.CaptureFixture[str], event: str) -> dict[str, object]:
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    return next(x for x in reversed(lines) if x.get("event") == event)


def test_duckdb_run_appends_results_and_logs_a_summary(
    source_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--backend", "duckdb", "--db", source_db, "--ruleset", "v0.1", "--as-of", "2026-11-24"]
    )

    assert code == 0
    summary = _last_log(capsys, "score_run_complete")
    assert summary["homes"] == 5 and summary["ruleset"] == "v0.1"
    con = duckdb.connect(source_db, read_only=True)
    assert con.execute("SELECT count(*) FROM results.home_score").fetchall() == [(5,)]


def test_flags_and_trigger_reach_the_run(source_db: str) -> None:
    code = main(
        [
            "--backend", "duckdb", "--db", source_db, "--ruleset", "v0.1",
            "--as-of", "2026-11-24", "--trigger", "nightly", "--flags", "address,fees",
        ]
    )  # fmt: skip

    assert code == 0
    con = duckdb.connect(source_db, read_only=True)
    assert con.execute("SELECT trigger, enabled_flags FROM results.run").fetchall() == [
        ("nightly", "address,fees")
    ]


@pytest.mark.parametrize(
    "extra",
    [["--ruleset", "v9.9"], ["--flags", "nope"], ["--as-of", "not-a-date"]],
)
def test_bad_input_exits_2_and_writes_nothing(source_db: str, extra: list[str]) -> None:
    args = ["--backend", "duckdb", "--db", source_db, "--ruleset", "v0.1", "--as-of", "2026-11-24"]

    assert main(args + extra) == 2
    con = duckdb.connect(source_db, read_only=True)
    schemas = con.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
    assert ("results",) not in schemas


def test_athena_backend_needs_its_settings(source_db: str) -> None:
    assert main(["--backend", "athena", "--ruleset", "v0.1", "--as-of", "2026-11-24"]) == 2
