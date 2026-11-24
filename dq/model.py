"""Plain data types for rules, rulesets and run settings."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum


class Dimension(StrEnum):
    """The seven score dimensions (design 5.2). Weights are Balaji's, see ``DIMENSION_WEIGHTS``."""

    MEDIA = "media"
    CONTENT = "content"
    AMENITIES = "amenities"
    LOCATION = "location"
    OCCUPANCY = "occupancy"
    FEES_POLICIES_TAXES = "fees_policies_taxes"
    CHANNEL_CONSISTENCY = "channel_consistency"


# NOTE: Weights are for checking the draft point split only. The score itself is points earned
# over points available, so changing rule points never needs a code change.
DIMENSION_WEIGHTS: dict[Dimension, int] = {
    Dimension.MEDIA: 20,
    Dimension.CONTENT: 15,
    Dimension.AMENITIES: 15,
    Dimension.LOCATION: 10,
    Dimension.OCCUPANCY: 15,
    Dimension.FEES_POLICIES_TAXES: 15,
    Dimension.CHANNEL_CONSISTENCY: 10,
}

# Flags switch on rules whose inputs are not in the lake yet.
KNOWN_FLAGS: frozenset[str] = frozenset({"address", "media_tables", "fees", "cross_channel"})

TRIGGERS: tuple[str, ...] = ("nightly", "change", "manual", "new_ruleset")


@dataclass(frozen=True)
class Rule:
    """One SQL check on one home.

    Attributes:
        rule_id: Stable id, unique inside a ruleset.
        dimension: Score dimension the points belong to.
        description: Plain wording shown with a failure.
        points: Points earned on a pass. Counts in the total on a fail.
        is_minimum_requirement: A failure blocks the gate.
        check_sql: Boolean SQL over the ``h`` feature row. TRUE passes; FALSE or NULL fails.
        found_sql: SQL text shown on a failure (what was found). Must not contain free text.
        applies_sql: Optional boolean SQL. FALSE leaves the rule out for that home;
            NULL counts as applies.
        requires_flag: Optional flag that must be enabled for the rule to run.
    """

    rule_id: str
    dimension: Dimension
    description: str
    points: int
    is_minimum_requirement: bool
    check_sql: str
    found_sql: str
    applies_sql: str | None = None
    requires_flag: str | None = None


@dataclass(frozen=True)
class Ruleset:
    """A versioned, immutable set of rules plus the population settings it was built with.

    Attributes:
        version: Version label, for example ``v0.1``.
        note: Why this version exists.
        rules: All rules, including those behind a disabled flag.
        sellable_statuses: Property status codes in the scored population.
        coords_exempt_sources: Partner source codes exempt from coordinates (Q6), flagged as
            not applicable.
    """

    version: str
    note: str
    rules: tuple[Rule, ...]
    sellable_statuses: tuple[str, ...] = ("AVAILABLE", "AVAILABLE_RENTAL")
    coords_exempt_sources: tuple[str, ...] = ()
    content_hash: str = field(default="", compare=False)

    def active_rules(self, enabled_flags: frozenset[str]) -> tuple[Rule, ...]:
        """Rules that run given the enabled flags.

        Args:
            enabled_flags: Flags switched on for this run.

        Returns:
            Rules without a flag, or whose flag is enabled, in file order.
        """
        return tuple(
            r for r in self.rules if r.requires_flag is None or r.requires_flag in enabled_flags
        )


@dataclass(frozen=True)
class RunSettings:
    """Inputs of one run. Same settings and same lake data give the same scores.

    Attributes:
        ruleset_version: Ruleset to apply.
        as_of: Date used for "current" links. Passed in so a re-run is reproducible.
        trigger: Why the run happens, one of ``TRIGGERS``.
        enabled_flags: Flags switched on. Empty by default: address, media-table, fee and
            cross-channel rules stay off until their inputs exist.
        source_schema: Schema holding the replicated Sunshine tables.
        results_schema: Schema holding the result tables.
    """

    ruleset_version: str
    as_of: dt.date
    trigger: str = "manual"
    enabled_flags: frozenset[str] = frozenset()
    source_schema: str = "silver"
    results_schema: str = "results"
