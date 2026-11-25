"""Command line entry point: run one scoring pass and append the results.

    python -m score.cli --backend duckdb --db local.duckdb --ruleset v0.1 --as-of 2026-11-24
    python -m score.cli --backend athena --workgroup <wg> --staging-location s3://<b>/<p> \\
        --results-location s3://<b>/<p> --results-schema <db> --source-schema <db> \\
        --ruleset v0.1

Exit codes: 0 done, 2 bad input or a known scoring error. Anything else is a bug and propagates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Sequence

import structlog

from score.engine import run_rules
from score.errors import AppError, ConfigurationError
from score.executor import AthenaExecutor, DuckDbExecutor, Executor
from score.model import TRIGGERS, RunSettings

logger = structlog.get_logger(__name__)


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {value}") from exc


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="score", description="Run one data quality scoring pass.")
    p.add_argument("--ruleset", required=True, help="ruleset version, for example v0.1")
    p.add_argument("--as-of", type=_date, default=None, help="YYYY-MM-DD; default today (UTC)")
    p.add_argument("--trigger", choices=TRIGGERS, default="manual")
    p.add_argument("--flags", default="", help="comma list: address,fees,media_tables,...")
    p.add_argument("--source-schema", default="silver")
    p.add_argument("--results-schema", default="results")
    p.add_argument("--backend", choices=("duckdb", "athena"), default="duckdb")
    p.add_argument("--db", help="duckdb: database file with the source tables")
    p.add_argument("--workgroup", help="athena: workgroup")
    p.add_argument("--staging-location", help="athena: s3://bucket/prefix for staging tables")
    p.add_argument("--results-location", help="athena: s3://bucket/prefix for result tables")
    p.add_argument("--region", default="us-west-2", help="athena: AWS region")
    return p


def _executor(args: argparse.Namespace) -> tuple[Executor, str | None]:
    """Build the backend from the arguments.

    Returns:
        The executor and, for Athena, the S3 prefix of the result tables.

    Raises:
        ConfigurationError: a setting the backend needs is missing.
    """
    if args.backend == "duckdb":
        if not args.db:
            raise ConfigurationError("--db is required for the duckdb backend")
        try:
            import duckdb
        except ImportError as exc:
            raise ConfigurationError("install the 'local' extra to use duckdb") from exc
        return DuckDbExecutor(duckdb.connect(args.db)), None
    missing = [
        n for n in ("workgroup", "staging_location", "results_location") if not getattr(args, n)
    ]
    if missing:
        raise ConfigurationError(f"athena backend needs: {', '.join(missing)}")
    return (
        AthenaExecutor(args.workgroup, args.staging_location, region=args.region),
        args.results_location,
    )


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command.

    Args:
        argv: Arguments, without the program name. Defaults to ``sys.argv``.

    Returns:
        Process exit code: 0 done, 2 bad input or a known scoring error.
    """
    _configure_logging()
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    flags = frozenset(f for f in args.flags.split(",") if f)
    as_of = args.as_of or dt.datetime.now(dt.UTC).date()
    try:
        executor, results_location = _executor(args)
        run_rules(
            executor,
            RunSettings(
                ruleset_version=args.ruleset,
                as_of=as_of,
                trigger=args.trigger,
                enabled_flags=flags,
                source_schema=args.source_schema,
                results_schema=args.results_schema,
            ),
            athena_location=results_location,
        )
    except AppError as exc:
        logger.error("score_run_failed", error=type(exc).__name__, detail=str(exc))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
