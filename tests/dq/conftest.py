"""Shared fixtures: synthetic Sunshine-shaped source tables with invented sample homes."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import duckdb
import pytest

from dq.executor import DuckDbExecutor

AS_OF = dt.date(2026, 11, 24)

PHOTO_RULE_TEXT = """
version = "v0.9"
note = "test"
[[rule]]
id = "MED_PHOTO_1"
dimension = "media"
description = "At least 1 photo"
points = 5
check = "h.photo_count >= 1"
found = "'photos=' || CAST(h.photo_count AS VARCHAR)"
"""

COORDS_RULE_TEXT = """
[[rule]]
id = "LOC_COORDS"
dimension = "location"
description = "Valid coordinates"
points = 5
minimum = true
applies = "h.coords_exempt = 0"
check = "h.coords_valid = 1"
found = "'coordinates missing or out of range'"
"""

GOOD_DESC = "A bright and quiet home near the water with a large deck. " * 4  # 228 chars

# property_uid, name, type, source, status, sleeps, bedrooms, slug, hero, address_uid
PROPERTIES = [
    (1, "Sample Home One", "VILLA", "SRC_A", "AVAILABLE", 6, 3, "home-one", "hero.jpg", 1),
    (2, "", "VILLA", "SRC_A", "AVAILABLE", 4, 2, "home-two", None, 2),
    (3, "Sample Home Three", None, "SRC_X", "AVAILABLE", None, 0, "dup-slug", "h.jpg", 3),
    (4, "Sample Home Four", "CABIN", "SRC_A", "AVAILABLE", 2, 1, "dup-slug", "h.jpg", 4),
    (
        5,
        "Sample Home Five",
        "CABIN",
        "SRC_EXEMPT",
        "AVAILABLE_RENTAL",
        2,
        1,
        "home-five",
        "h.jpg",
        5,
    ),
    (6, "Inactive Home", "CABIN", "SRC_A", "AVAILABLE", 2, 1, "home-six", "h.jpg", 6),
    (7, "Retired Home", "CABIN", "SRC_A", "RETIRED", 2, 1, "home-seven", "h.jpg", 7),
]


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    _load_sources(c)
    yield c
    c.close()


@pytest.fixture
def photo_rule() -> str:
    """Ruleset text with one rule: at least 1 photo, 5 points."""
    return PHOTO_RULE_TEXT


@pytest.fixture
def coords_rule() -> str:
    """Extra rule: valid coordinates, minimum requirement, N/A when exempt."""
    return COORDS_RULE_TEXT


@pytest.fixture
def as_of() -> dt.date:
    return AS_OF


@pytest.fixture
def executor(con: duckdb.DuckDBPyConnection) -> DuckDbExecutor:
    return DuckDbExecutor(con)


def _load_sources(c: duckdb.DuckDBPyConnection) -> None:
    c.execute("CREATE SCHEMA silver")
    c.execute(
        "CREATE TABLE silver.property (property_uid BIGINT, property_name VARCHAR, "
        "property_type_ucode VARCHAR, property_source_ucode VARCHAR, "
        "property_status_ucode VARCHAR, "
        "sleeps INTEGER, bedrooms INTEGER, slug VARCHAR, featured_photo VARCHAR, "
        "address_uid BIGINT, active_flag VARCHAR)"
    )
    for row in PROPERTIES:
        active = "N" if row[0] == 6 else "Y"
        c.execute("INSERT INTO silver.property VALUES (?,?,?,?,?,?,?,?,?,?,?)", [*row, active])
    c.execute(
        "CREATE TABLE silver.property_information (property_uid BIGINT, description VARCHAR, "
        "summary VARCHAR, square_footage INTEGER, latitude VARCHAR, longitude VARCHAR, "
        "cancellation_policy_ucode VARCHAR, min_full_baths INTEGER, max_full_baths INTEGER, "
        "min_half_baths INTEGER, max_half_baths INTEGER)"
    )
    info = [
        (1, GOOD_DESC, "Short summary", 1800, "41.5", "-70.6", "FLEX"),
        (2, "<p>Call 555-123-4567 now</p>", "", 0, "91.0", "10.0", None),
        (3, "x" * 150 + " mail me at a.b@example.com", None, 900, "0", "0", None),
        (4, GOOD_DESC, "s", 700, "abc", "10", "FLEX"),
        (5, GOOD_DESC, "s", 700, None, None, "FLEX"),
    ]
    c.executemany(
        "INSERT INTO silver.property_information VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(*row, None, None, None, None) for row in info],
    )
    c.execute("CREATE TABLE silver.property_source (property_source_ucode VARCHAR)")
    c.executemany(
        "INSERT INTO silver.property_source VALUES (?)", [("SRC_A",), ("SRC_A",), ("SRC_EXEMPT",)]
    )
    c.execute("CREATE TABLE silver.photo (property_uid BIGINT, path VARCHAR)")
    counts = {1: 21, 2: 0, 3: 10, 4: 15, 5: 1}
    for uid, n in counts.items():
        for i in range(n):
            c.execute("INSERT INTO silver.photo VALUES (?,?)", [uid, f"p{i}"])
    c.execute("CREATE TABLE silver.amenity (property_uid BIGINT, has_flag VARCHAR)")
    c.executemany(
        "INSERT INTO silver.amenity VALUES (?,?)", [(1, "Y"), (1, "Y"), (2, "N"), (4, "Y")]
    )
    c.execute("CREATE TABLE silver.room (property_uid BIGINT, room_type_ucode VARCHAR)")
    c.executemany(
        "INSERT INTO silver.room VALUES (?,?)",
        [(1, "BATH_FULL"), (1, "BEDROOM"), (4, "BATH_HALF"), (5, "KITCHEN")],
    )
    c.execute("CREATE TABLE silver.property_club (property_uid BIGINT, end_date DATE)")
    c.executemany(
        "INSERT INTO silver.property_club VALUES (?,?)",
        [(1, None), (2, dt.date(2026, 1, 1)), (4, dt.date(2027, 1, 1)), (5, None)],
    )
    c.execute(
        "CREATE TABLE silver.address (address_uid BIGINT, city VARCHAR, province_state VARCHAR, "
        "postalcode VARCHAR, country VARCHAR)"
    )
    c.executemany(
        "INSERT INTO silver.address VALUES (?,?,?,?,?)",
        [(1, "Town", "ST", "00001", "US"), (2, "", "", "", ""), (4, "Town", None, None, "US")],
    )
    c.execute(
        "CREATE TABLE silver.property_price (property_uid BIGINT, end_date DATE, "
        "tax_percent DOUBLE, housekeeping DOUBLE)"
    )
    c.executemany(
        "INSERT INTO silver.property_price VALUES (?,?,?,?)",
        [
            (1, dt.date(2027, 1, 1), 10.0, 150.0),
            (4, dt.date(2027, 1, 1), 0.0, 0.0),
            (2, dt.date(2020, 1, 1), 5.0, 5.0),
        ],
    )
