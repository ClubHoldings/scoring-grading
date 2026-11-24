"""Build the one-row-per-home feature query that every rule reads.

Rules are boolean SQL over these columns, so all text and regex work happens here once, and only
lengths and 0/1 flags leave the source tables. The SQL uses a subset common to Trino (Athena) and
DuckDB; the only shimmed function is ``regexp_like``.

Source tables are the replicated Sunshine tables, one current row per key, in ``source_schema``.
NOTE: bronze is append-only, so a latest-snapshot filter belongs in silver. Until the silver
contract lands (QDEV-687) this assumes one row per key.
"""

from __future__ import annotations

import datetime as dt
import re

from dq.errors import ConfigurationError
from dq.model import Ruleset

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

_BASE = """
WITH s AS (
    SELECT p.property_uid, p.property_name, p.property_type_ucode, p.property_source_ucode,
           p.sleeps, p.bedrooms, p.slug, p.featured_photo, p.address_uid,
           count(*) OVER (PARTITION BY p.slug) AS slug_homes
    FROM {src}.property p
    WHERE p.active_flag = 'Y' AND p.property_status_ucode IN ({statuses})
)
SELECT s.property_uid,
       s.property_type_ucode,
       s.sleeps,
       s.bedrooms,
       CASE WHEN trim(coalesce(s.property_name, '')) <> '' THEN 1 ELSE 0 END AS has_name,
       CASE WHEN trim(coalesce(s.slug, '')) <> '' THEN 1 ELSE 0 END AS has_slug,
       s.slug_homes,
       CASE WHEN s.featured_photo IS NOT NULL THEN 1 ELSE 0 END AS has_hero,
       CASE WHEN psrc.property_source_ucode IS NOT NULL THEN 1 ELSE 0 END AS has_source,
       CASE WHEN s.property_source_ucode IN ({exempt}) THEN 1 ELSE 0 END AS coords_exempt,
       length(coalesce(i.description, '')) AS desc_len,
       length(coalesce(i.summary, '')) AS summary_len,
       CASE WHEN i.description IS NULL THEN NULL
            WHEN regexp_like(i.description, '<[^>]+>') THEN 1 ELSE 0 END AS desc_has_html,
       CASE WHEN i.description IS NULL THEN NULL
            WHEN regexp_like(i.description,
           '[0-9]{3}[-. ][0-9]{3}[-. ][0-9]{4}|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\\.[A-Za-z]{2,}')
            THEN 1 ELSE 0 END AS desc_has_contact,
       coalesce(i.square_footage, 0) AS square_footage,
       CASE WHEN regexp_like(CAST(i.latitude AS VARCHAR), '^-?[0-9]+(\\.[0-9]+)?$')
             AND regexp_like(CAST(i.longitude AS VARCHAR), '^-?[0-9]+(\\.[0-9]+)?$')
             AND abs(try_cast(i.latitude AS DOUBLE)) <= 90
             AND abs(try_cast(i.longitude AS DOUBLE)) <= 180
             AND NOT (try_cast(i.latitude AS DOUBLE) = 0 AND try_cast(i.longitude AS DOUBLE) = 0)
            THEN 1 ELSE 0 END AS coords_valid,
       coalesce(ph.n, 0) AS photo_count,
       coalesce(am.n, 0) AS amenity_count,
       coalesce(bt.n, 0) AS bathroom_count,
       coalesce(cl.n, 0) AS club_links{extra_columns}
FROM s
LEFT JOIN {src}.property_information i ON i.property_uid = s.property_uid
LEFT JOIN (SELECT DISTINCT property_source_ucode FROM {src}.property_source) psrc
       ON psrc.property_source_ucode = s.property_source_ucode
LEFT JOIN (SELECT property_uid, count(*) AS n FROM {src}.photo GROUP BY property_uid) ph
       ON ph.property_uid = s.property_uid
LEFT JOIN (SELECT property_uid, count(*) AS n FROM {src}.amenity
           WHERE has_flag = 'Y' GROUP BY property_uid) am
       ON am.property_uid = s.property_uid
LEFT JOIN (SELECT property_uid, count(*) AS n FROM {src}.room
           WHERE room_type_ucode LIKE 'BATH%' GROUP BY property_uid) bt
       ON bt.property_uid = s.property_uid
LEFT JOIN (SELECT property_uid, count(*) AS n FROM {src}.property_club
           WHERE end_date IS NULL OR end_date >= DATE '{as_of}' GROUP BY property_uid) cl
       ON cl.property_uid = s.property_uid{extra_joins}
"""

# Per-flag fragments: (extra select columns, extra joins). Added only when the flag is on, so a
# missing source table never breaks a run that does not use it.
_FRAGMENTS: dict[str, tuple[str, str]] = {
    "address": (
        """,
       CASE WHEN trim(coalesce(a.city, '')) <> '' THEN 1 ELSE 0 END AS has_city,
       CASE WHEN trim(coalesce(a.country, '')) <> '' THEN 1 ELSE 0 END AS has_country,
       CASE WHEN trim(coalesce(a.province_state, '')) <> '' THEN 1 ELSE 0 END AS has_state,
       CASE WHEN trim(coalesce(a.postalcode, '')) <> '' THEN 1 ELSE 0 END AS has_postal""",
        "\nLEFT JOIN {src}.address a ON a.address_uid = s.address_uid",
    ),
    "fees": (
        """,
       coalesce(pr.n, 0) AS price_rows_current,
       coalesce(pr.tax_n, 0) AS tax_rows_current,
       coalesce(pr.cleaning_n, 0) AS cleaning_rows_current,
       CASE WHEN i.cancellation_policy_ucode IS NOT NULL THEN 1 ELSE 0 END AS has_cancel_policy""",
        """
LEFT JOIN (SELECT property_uid, count(*) AS n,
                  sum(CASE WHEN tax_percent > 0 THEN 1 ELSE 0 END) AS tax_n,
                  sum(CASE WHEN housekeeping > 0 THEN 1 ELSE 0 END) AS cleaning_n
           FROM {src}.property_price WHERE end_date >= DATE '{as_of}' GROUP BY property_uid) pr
       ON pr.property_uid = s.property_uid""",
    ),
}


def _ident(value: str, what: str) -> str:
    """Return ``value`` if it is a plain lower case identifier.

    Raises:
        ConfigurationError: anything else, so a schema name can never carry SQL.
    """
    if not _IDENT_RE.match(value):
        raise ConfigurationError(f"{what} {value!r} is not a plain lower case identifier")
    return value


def _in_list(codes: tuple[str, ...]) -> str:
    """Render codes as a SQL IN list; NULL when empty so the test matches nothing."""
    # Codes are validated upper case [A-Z0-9_] by the ruleset loader, so quoting is safe.
    return ", ".join(f"'{c}'" for c in codes) if codes else "NULL"


def features_sql(
    ruleset: Ruleset, source_schema: str, as_of: dt.date, enabled_flags: frozenset[str]
) -> str:
    """Return the SELECT that yields one feature row per scored home.

    Args:
        ruleset: Supplies the population and the exempt source codes.
        source_schema: Schema holding the replicated tables.
        as_of: Date for "current" links and prices.
        enabled_flags: Flags on for this run. Selects the optional fragments.

    Returns:
        SQL text, without a trailing semicolon.

    Raises:
        ConfigurationError: the schema name is not a plain identifier.
    """
    columns = ""
    joins = ""
    for flag in sorted(enabled_flags):
        if flag in _FRAGMENTS:
            columns += _FRAGMENTS[flag][0]
            joins += _FRAGMENTS[flag][1]
    return (
        (_BASE.replace("{extra_columns}", columns).replace("{extra_joins}", joins))
        .replace("{src}", _ident(source_schema, "source schema"))
        .replace("{statuses}", _in_list(ruleset.sellable_statuses))
        .replace("{exempt}", _in_list(ruleset.coords_exempt_sources))
        .replace("{as_of}", as_of.isoformat())
    )
