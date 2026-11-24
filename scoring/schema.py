"""Result table definitions, rendered for DuckDB (local, tests) and Athena Iceberg (the lake).

One column list per table is the single source of truth, so both backends keep the same shape.
Tables are append-only: nothing in this package issues UPDATE or DELETE on them. A run is only
visible once its row in ``run`` exists; ``run`` is written last, as the commit marker, so a run
that dies half-way leaves rows no reader joins to.
"""

from __future__ import annotations

from typing import Literal

from scoring.errors import ConfigurationError

Dialect = Literal["duckdb", "athena"]

# Abstract type -> (duckdb, athena).
_TYPES: dict[str, tuple[str, str]] = {
    "str": ("VARCHAR", "string"),
    "int": ("INTEGER", "int"),
    "bigint": ("BIGINT", "bigint"),
    "double": ("DOUBLE", "double"),
    "bool": ("BOOLEAN", "boolean"),
    "ts": ("TIMESTAMP", "timestamp"),
    "date": ("DATE", "date"),
}

TABLES: dict[str, list[tuple[str, str]]] = {
    "ruleset": [
        ("ruleset_version", "str"),
        ("note", "str"),
        ("content_hash", "str"),
        ("sellable_statuses", "str"),
        ("coords_exempt_sources", "str"),
        ("registered_at", "ts"),
    ],
    "rule": [
        ("ruleset_version", "str"),
        ("rule_id", "str"),
        ("dimension", "str"),
        ("description", "str"),
        ("points", "int"),
        ("is_minimum_requirement", "bool"),
        ("requires_flag", "str"),
        ("check_sql", "str"),
        ("found_sql", "str"),
        ("applies_sql", "str"),
    ],
    "run": [
        ("run_id", "str"),
        ("ruleset_version", "str"),
        ("trigger", "str"),
        ("as_of", "date"),
        ("enabled_flags", "str"),
        ("started_at", "ts"),
        ("homes_scored", "bigint"),
        ("points_available", "int"),
    ],
    "home_score": [
        ("run_id", "str"),
        ("property_uid", "bigint"),
        ("points_available", "int"),
        ("points_earned", "int"),
        ("score", "double"),
        ("min_req_failed", "int"),
    ],
    "rule_failure": [
        ("run_id", "str"),
        ("property_uid", "bigint"),
        ("rule_id", "str"),
        ("dimension", "str"),
        ("points_lost", "int"),
        ("found_value", "str"),
    ],
    "rule_not_applicable": [
        ("run_id", "str"),
        ("property_uid", "bigint"),
        ("rule_id", "str"),
        ("points", "int"),
    ],
}


def sql_type(abstract: str, dialect: Dialect) -> str:
    """Map an abstract column type to the backend type name (DuckDB or Athena)."""
    duck, athena = _TYPES[abstract]
    return duck if dialect == "duckdb" else athena


def cast_sql(expr: str, abstract: str, dialect: Dialect) -> str:
    """Wrap ``expr`` in a CAST to the backend type so INSERT column types always match."""
    return f"CAST({expr} AS {sql_type(abstract, dialect)})"


def create_table_sql(schema: str, table: str, dialect: Dialect, location: str | None = None) -> str:
    """Render CREATE TABLE IF NOT EXISTS for one result table.

    Args:
        schema: Schema or database holding the table.
        table: Table name from ``TABLES``.
        dialect: ``duckdb`` or ``athena``.
        location: Athena only. S3 prefix for the Iceberg table, ``s3://bucket/prefix``.

    Returns:
        One DDL statement.
    """
    cols = ", ".join(f"{name} {sql_type(t, dialect)}" for name, t in TABLES[table])
    head = f"CREATE TABLE IF NOT EXISTS {schema}.{table} ({cols})"
    if dialect == "duckdb":
        return head
    if location is None:
        raise ConfigurationError("athena tables need an S3 location")
    return (
        f"{head} LOCATION '{location.rstrip('/')}/{table}/' TBLPROPERTIES ('table_type'='ICEBERG')"
    )


def insert_select(
    schema: str, table: str, exprs: dict[str, str], from_sql: str, dialect: Dialect
) -> str:
    """Render INSERT ... SELECT with every column cast to its table type, in table order.

    Args:
        schema: Schema holding the table.
        table: Table name from ``TABLES``.
        exprs: Column name to SQL expression. Must cover every column.
        from_sql: Text after the select list: ``FROM ... WHERE ...``.
        dialect: ``duckdb`` or ``athena``.

    Returns:
        One INSERT statement.

    Raises:
        KeyError: a column has no expression.
    """
    select = ", ".join(cast_sql(exprs[name], t, dialect) for name, t in TABLES[table])
    return f"INSERT INTO {schema}.{table} SELECT {select} {from_sql}"
