"""SQL backends: DuckDB for local runs and tests, Athena for the lake.

The engine only talks to the ``Executor`` protocol, so the same rules and result tables run on both.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from dq.errors import ConfigurationError, ExecutionError
from dq.schema import Dialect

if TYPE_CHECKING:
    import duckdb

logger = structlog.get_logger(__name__)


class Executor(Protocol):
    """What the engine needs from a SQL backend."""

    dialect: Dialect

    def execute(self, sql: str) -> None:
        """Run one statement that returns no rows."""
        ...

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        """Run one query and return all rows."""
        ...

    def ensure_namespace(self, name: str) -> None:
        """Create the schema (database) if it is missing."""
        ...

    def materialise(self, name: str, select_sql: str) -> None:
        """Store a query result as a table ``name`` so later statements read it once."""
        ...

    def drop(self, name: str) -> None:
        """Drop a staging table made by ``materialise``."""
        ...


class DuckDbExecutor:
    """Executor on an open DuckDB connection."""

    dialect: Dialect = "duckdb"

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        """Wrap a connection and add the Trino function names the rules use.

        Args:
            con: Open DuckDB connection.
        """
        self._con = con
        # NOTE: Trino and Athena call it regexp_like; DuckDB calls it regexp_matches.
        self.execute("CREATE OR REPLACE MACRO regexp_like(s, p) AS regexp_matches(s, p)")

    def execute(self, sql: str) -> None:
        """Run one statement.

        Raises:
            ExecutionError: the statement failed.
        """
        self.fetch(sql)

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        """Run one query and return all rows.

        Raises:
            ExecutionError: the statement failed.
        """
        import duckdb

        try:
            result = self._con.execute(sql)
            return result.fetchall() if result.description else []
        except duckdb.Error as exc:
            raise ExecutionError(str(exc)) from exc

    def ensure_namespace(self, name: str) -> None:
        """Create the schema if missing (CREATE SCHEMA IF NOT EXISTS)."""
        self.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")

    def materialise(self, name: str, select_sql: str) -> None:
        """Create table ``name`` from a query."""
        self.execute(f"CREATE OR REPLACE TABLE {name} AS {select_sql}")

    def drop(self, name: str) -> None:
        """Drop a staging table. Result tables are never dropped."""
        self.execute(f"DROP TABLE IF EXISTS {name}")


class AthenaExecutor:
    """Executor on an Athena workgroup. Needs the ``aws`` extra (boto3).

    NOTE: Not exercised against real AWS yet (no account access in the build session); the
    statements it sends are the same ones the DuckDB tests run, apart from DDL and CTAS.
    """

    dialect: Dialect = "athena"

    def __init__(
        self,
        workgroup: str,
        staging_location: str,
        region: str = "us-west-2",
        poll_seconds: float = 1.0,
        timeout_seconds: float = 1800.0,
        client: Any = None,
    ) -> None:
        """Create the Athena client.

        Args:
            workgroup: Athena workgroup, for example ``lake-workgroup``.
            staging_location: ``s3://bucket/prefix`` for staging CTAS tables.
            region: AWS region.
            poll_seconds: Wait between status checks.
            timeout_seconds: Give up on a query after this long.
            client: Athena client. Created with boto3 when not given.

        Raises:
            ConfigurationError: the location is not an S3 URI or boto3 is missing.
        """
        if not staging_location.startswith("s3://"):
            raise ConfigurationError("staging_location must be an s3:// URI")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ConfigurationError("install the 'aws' extra to use Athena") from exc
            client = boto3.client("athena", region_name=region)
        self._client = client
        self._workgroup = workgroup
        self._staging = staging_location.rstrip("/")
        self._poll = poll_seconds
        self._timeout = timeout_seconds

    def _run(self, sql: str) -> str:
        qid: str = self._client.start_query_execution(QueryString=sql, WorkGroup=self._workgroup)[
            "QueryExecutionId"
        ]
        deadline = time.monotonic() + self._timeout
        while True:
            status = self._client.get_query_execution(QueryExecutionId=qid)["QueryExecution"][
                "Status"
            ]
            state = status["State"]
            if state == "SUCCEEDED":
                return qid
            if state in ("FAILED", "CANCELLED"):
                raise ExecutionError(
                    f"athena query {qid} {state}: {status.get('StateChangeReason')}"
                )
            if time.monotonic() > deadline:
                raise ExecutionError(f"athena query {qid} timed out")
            time.sleep(self._poll)

    def execute(self, sql: str) -> None:
        """Run one statement and wait for it.

        Raises:
            ExecutionError: the query failed or timed out.
        """
        self._run(sql)

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        """Run one query and return rows as tuples (Athena returns text, header row removed).

        Raises:
            ExecutionError: the query failed or timed out.
        """
        qid = self._run(sql)
        rows: list[tuple[Any, ...]] = []
        pages = self._client.get_paginator("get_query_results").paginate(QueryExecutionId=qid)
        for page in pages:
            for row in page["ResultSet"]["Rows"]:
                rows.append(tuple(c.get("VarCharValue") for c in row["Data"]))
        return rows[1:]

    def ensure_namespace(self, name: str) -> None:
        """Create the Glue database if missing."""
        self.execute(f"CREATE DATABASE IF NOT EXISTS {name}")

    def materialise(self, name: str, select_sql: str) -> None:
        """CTAS to a Parquet table under the staging prefix."""
        self.drop(name)
        self.execute(
            f"CREATE TABLE {name} WITH (format = 'PARQUET', "
            f"external_location = '{self._staging}/{name}/') AS {select_sql}"
        )

    def drop(self, name: str) -> None:
        """Drop a staging table. Its S3 files expire by lifecycle rule."""
        self.execute(f"DROP TABLE IF EXISTS {name}")
