"""DDL rendering for both backends."""

from __future__ import annotations

import pytest

from score.errors import ConfigurationError
from score.schema import TABLES, create_table_sql, insert_select


def test_athena_ddl_is_iceberg_with_location() -> None:
    sql = create_table_sql("results", "run", "athena", "s3://bucket/scoring/")
    assert "string" in sql and "'table_type'='ICEBERG'" in sql
    assert "LOCATION 's3://bucket/scoring/run/'" in sql


def test_athena_needs_location() -> None:
    with pytest.raises(ConfigurationError):
        create_table_sql("results", "run", "athena")


def test_insert_covers_every_column_in_order() -> None:
    exprs = {name: "NULL" for name, _ in TABLES["rule_failure"]}
    sql = insert_select("r", "rule_failure", exprs, "FROM t", "athena")
    assert sql.count("CAST(NULL") == len(TABLES["rule_failure"])
    assert "AS bigint" in sql
