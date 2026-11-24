"""Ruleset file loading and validation, on the shipped v0.1 file and on bad inputs."""

from __future__ import annotations

import pytest

from dq.errors import ConfigurationError, UnknownRulesetError
from dq.model import DIMENSION_WEIGHTS, Dimension
from dq.ruleset_loader import load_ruleset, parse_ruleset

GOOD = """
version = "v0.9"
note = "n"
[[rule]]
id = "R_ONE"
dimension = "media"
description = "d"
points = 3
check = "h.photo_count >= 1"
found = "'x'"
"""


def test_shipped_ruleset_points_match_dimension_weights() -> None:
    rs = load_ruleset("v0.1")
    totals = {d: 0 for d in Dimension}
    for r in rs.rules:
        totals[r.dimension] += r.points
    assert totals == DIMENSION_WEIGHTS
    assert len(rs.content_hash) == 64


def test_unknown_or_malformed_version() -> None:
    with pytest.raises(UnknownRulesetError):
        load_ruleset("v99.0")
    with pytest.raises(UnknownRulesetError):
        load_ruleset("../etc")


def test_minimal_ruleset_parses() -> None:
    rs = parse_ruleset(GOOD, expected_version="v0.9")
    assert rs.rules[0].points == 3 and not rs.rules[0].is_minimum_requirement
    assert rs.sellable_statuses == ("AVAILABLE", "AVAILABLE_RENTAL")


@pytest.mark.parametrize(
    "old,new",
    [
        ("points = 3", "points = 0"),
        ("points = 3", "points = true"),
        ('dimension = "media"', 'dimension = "nope"'),
        ('id = "R_ONE"', 'id = "r one"'),
        ('check = "h.photo_count >= 1"', 'check = "1=1; DROP TABLE x"'),
        ('check = "h.photo_count >= 1"', 'check = "1=1 -- c"'),
        ("found = \"'x'\"", 'found = ""'),
        ("points = 3", 'points = 3\nflag = "bogus"'),
        ("points = 3", 'points = 3\napplies = ""'),
        ('note = "n"', 'note = "n"\ncoords_exempt_sources = ["bad code"]'),
        ('note = "n"', 'note = "n"\nsellable_statuses = []'),
    ],
)
def test_invalid_rule_rejected(old: str, new: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_ruleset(GOOD.replace(old, new))


def test_structural_errors() -> None:
    with pytest.raises(ConfigurationError):
        parse_ruleset("not toml = = =")
    with pytest.raises(ConfigurationError):
        parse_ruleset('version = "v0.9"\nnote = "n"')
    with pytest.raises(ConfigurationError):
        parse_ruleset(GOOD, expected_version="v0.8")
    dup = GOOD + GOOD.split('note = "n"')[1]
    with pytest.raises(ConfigurationError):
        parse_ruleset(dup)
