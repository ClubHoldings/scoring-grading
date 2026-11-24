"""Load and validate ruleset files (TOML) shipped in ``dq/rulesets``.

Points live in these files, not in code, so the business can change them by adding a new version.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from importlib import resources
from typing import Any

import structlog

from dq.errors import ConfigurationError, UnknownRulesetError
from dq.model import KNOWN_FLAGS, Dimension, Rule, Ruleset

logger = structlog.get_logger(__name__)

_VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+$")
_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,40}$")
_CODE_RE = re.compile(r"^[A-Z0-9_]{1,40}$")


def _filename(version: str) -> str:
    """Return the packaged file name for a version, for example ``v0_1.toml``.

    Raises:
        UnknownRulesetError: the version label is malformed.
    """
    if not _VERSION_RE.match(version):
        raise UnknownRulesetError(version)
    return version.replace(".", "_") + ".toml"


def read_ruleset_text(version: str) -> str:
    """Return the packaged TOML text of a ruleset version.

    Args:
        version: Version label such as ``v0.1``.

    Returns:
        File content.

    Raises:
        UnknownRulesetError: no file for this version.
    """
    path = resources.files("dq.rulesets").joinpath(_filename(version))
    if not path.is_file():
        raise UnknownRulesetError(version)
    return path.read_text(encoding="utf-8")


def load_ruleset(version: str) -> Ruleset:
    """Load a packaged ruleset by version.

    Args:
        version: Version label such as ``v0.1``.

    Returns:
        The validated ruleset with its content hash.

    Raises:
        UnknownRulesetError: no file for this version.
        ConfigurationError: the file is invalid.
    """
    return parse_ruleset(read_ruleset_text(version), expected_version=version)


def _content_hash(doc: dict[str, Any]) -> str:
    """Hash parsed ruleset content, so a comment-only edit keeps the version's identity."""
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{where}: '{key}' must be a non-empty string")
    return value


def _require_sql(raw: dict[str, Any], key: str, where: str) -> str:
    """Return a SQL fragment, rejecting statement separators and comments."""
    value = _require_str(raw, key, where)
    if ";" in value or "--" in value or "/*" in value:
        raise ConfigurationError(f"{where}: '{key}' must be one expression, no ';' or comments")
    return value


def _parse_rule(raw: dict[str, Any]) -> Rule:
    """Validate one rule table.

    Raises:
        ConfigurationError: missing or invalid field.
    """
    rule_id = _require_str(raw, "id", "rule")
    where = f"rule {rule_id}"
    if not _RULE_ID_RE.match(rule_id):
        raise ConfigurationError(f"{where}: id must be upper case letters, digits and underscores")
    try:
        dimension = Dimension(_require_str(raw, "dimension", where))
    except ValueError as exc:
        raise ConfigurationError(f"{where}: unknown dimension {raw['dimension']!r}") from exc
    points = raw.get("points")
    if isinstance(points, bool) or not isinstance(points, int) or points <= 0:
        raise ConfigurationError(f"{where}: points must be a positive integer")
    flag = raw.get("flag")
    if flag is not None and flag not in KNOWN_FLAGS:
        raise ConfigurationError(f"{where}: unknown flag {flag!r}")
    applies = _require_sql(raw, "applies", where) if "applies" in raw else None
    return Rule(
        rule_id=rule_id,
        dimension=dimension,
        description=_require_str(raw, "description", where),
        points=points,
        is_minimum_requirement=bool(raw.get("minimum", False)),
        check_sql=_require_sql(raw, "check", where),
        found_sql=_require_sql(raw, "found", where),
        applies_sql=applies,
        requires_flag=flag,
    )


def _parse_codes(raw: Any, key: str) -> tuple[str, ...]:
    """Validate a list of upper case codes.

    Raises:
        ConfigurationError: not a list of codes.
    """
    if not isinstance(raw, list) or not all(isinstance(c, str) and _CODE_RE.match(c) for c in raw):
        raise ConfigurationError(f"'{key}' must be a list of upper case codes")
    return tuple(raw)


def parse_ruleset(text: str, expected_version: str | None = None) -> Ruleset:
    """Parse and validate ruleset TOML text.

    Args:
        text: TOML document.
        expected_version: When given, the file's ``version`` must equal it.

    Returns:
        The validated ruleset. ``content_hash`` is the SHA-256 of the parsed content,
            so comment and layout edits do not change it.

    Raises:
        ConfigurationError: syntax or validation problem.
    """
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"ruleset is not valid TOML: {exc}") from exc
    version = _require_str(doc, "version", "ruleset")
    if not _VERSION_RE.match(version) or (expected_version and version != expected_version):
        raise ConfigurationError(f"ruleset version {version!r} does not match {expected_version!r}")
    raw_rules = doc.get("rule")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigurationError("ruleset has no rules")
    rules = tuple(_parse_rule(r) for r in raw_rules)
    ids = [r.rule_id for r in rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ConfigurationError(f"duplicate rule ids: {duplicates}")
    ruleset = Ruleset(
        version=version,
        note=_require_str(doc, "note", "ruleset"),
        rules=rules,
        sellable_statuses=_parse_codes(
            doc.get("sellable_statuses", ["AVAILABLE", "AVAILABLE_RENTAL"]), "sellable_statuses"
        ),
        coords_exempt_sources=_parse_codes(
            doc.get("coords_exempt_sources", []), "coords_exempt_sources"
        ),
        content_hash=_content_hash(doc),
    )
    if not ruleset.sellable_statuses:
        raise ConfigurationError("'sellable_statuses' must not be empty")
    logger.info("ruleset_loaded", version=version, rules=len(rules))
    return ruleset
