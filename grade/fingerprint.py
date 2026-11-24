"""Deterministic fingerprints of a home's text fields and photo set (SG-B4, QDEV-881).

A fingerprint decides whether the AI steps must run again for a home (design v0.1,
section 7.4). It must give the same digest for the same *meaning* of the input on every
run, machine and Python version, otherwise unchanged homes get re-sent to the model and
cost money. Everything below is therefore defined as a canonical form first, hashed second.

Canonical form of the text fields
---------------------------------
* Field order: keys are sorted, so the order a query returns columns in does not matter.
* Strings: Unicode NFC, every run of whitespace (spaces, tabs, newlines, NBSP) becomes one
  space, ends are stripped. Re-flowed or re-saved text is not a content change. Letter case
  is kept, because the model sees it.
* Null handling: ``None``, empty and whitespace-only strings, empty collections and keys
  holding only those are all "missing" and dropped. A field that is missing and a field that
  is absent hash the same, so adding a new nullable column never changes old fingerprints.
* Numbers: ints, floats and Decimals with the same value hash the same (``3``, ``3.0``,
  ``Decimal("3.00")``, ``1.5`` and ``Decimal("1.50")``). Non-integral numbers are kept as
  exact decimal text, never rounded through float. NaN and infinity are rejected: they have
  no stable meaning. A string ``"3"`` stays different from the number ``3``.
* Mapping keys must be strings and are used as given (they are column names, not data).
* Multi-valued fields (lists, tuples, sets such as amenities) are unordered: items are
  canonicalised, de-duplicated and sorted. The source gives no order guarantee for them.
* Serialisation: compact JSON, sorted keys, UTF-8, no ASCII escaping, prefixed with
  ``FORMAT_VERSION`` so a future change of these rules cannot collide with old digests.

Photo set
---------
Photos are identified by ``(photo_id, sha256)``. The sha256 is the content hash that the
photo copy story (SG-B5) puts into the S3 key, so no bytes are read here. The set hash
covers the hero (first photo) and the unordered set of photos. Order beyond the hero is
ignored: the vision step treats only the first photo specially, per-photo labels are cached
by content hash, and re-ordering the rest changes no input of the step. A new hero, an
added, removed or replaced photo does change the hash.
FIXME: if SG-B6 picks the 8-10 photos sent to the model by position (design 7.1, open),
the hash must cover that selection instead. Ref: QDEV-883.

Nothing here logs or stores text; only digests leave the module.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

FORMAT_VERSION = "fp1"

_WHITESPACE_RE = re.compile(r"\s+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FingerprintError(ValueError):
    """Base class for every error this module family raises."""


class InvalidHomeRecordError(FingerprintError):
    """A home record cannot be fingerprinted (bad value, bad photo, duplicate id)."""


class Step(StrEnum):
    """AI steps whose results are cached per fingerprint (design section 7)."""

    SEMANTIC = "semantic"
    VISION = "vision"
    MATCH = "match"


@dataclass(frozen=True, slots=True)
class PhotoRef:
    """One photo of a home.

    Attributes:
        photo_id: Stable id the vision step cites as evidence.
        sha256: Lowercase hex SHA-256 of the image bytes (SG-B5 content hash).
    """

    photo_id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class HomeRecord:
    """Input for one home.

    Attributes:
        home_id: Unique id of the home within a run.
        text_fields: Only the fields the AI text steps read (title, description, ...).
            The module hashes whatever it is given; choosing the fields is the caller's job.
        photos: Photos in display order, hero first.
    """

    home_id: str
    text_fields: Mapping[str, Any]
    photos: tuple[PhotoRef, ...] = ()


@dataclass(frozen=True, slots=True)
class HomeHashes:
    """Digests computed for one home.

    Attributes:
        text_hash: Hash of the canonical text fields.
        photo_set_hash: Hash of the hero plus the unordered photo set.
        photo_hashes: Sorted distinct content hashes; the key of cached photo labels.
    """

    text_hash: str
    photo_set_hash: str
    photo_hashes: tuple[str, ...]


def _digest(payload: object) -> str:
    """SHA-256 of ``FORMAT_VERSION`` plus the compact, key-sorted JSON of ``payload``."""
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(f"{FORMAT_VERSION}\n{text}".encode()).hexdigest()


def _canonical_number(value: float | Decimal) -> int | dict[str, str]:
    """Canonical number: an int when integral, else a tagged exact decimal string.

    The tag keeps ``2.5`` (number) from colliding with ``"2.5"`` (text). ``repr`` of a
    float is its shortest round-trip form, so it agrees with the equivalent Decimal.

    Raises:
        InvalidHomeRecordError: NaN or infinity.
    """
    decimal = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    if not decimal.is_finite():
        raise InvalidHomeRecordError(f"non-finite number: {value}")
    decimal = decimal.normalize()
    if decimal == decimal.to_integral_value():
        return int(decimal)
    return {"$num": format(decimal, "f")}


def _canonical(value: Any, path: str) -> Any:
    """Return the canonical form of ``value``, or ``None`` when it counts as missing.

    Raises:
        InvalidHomeRecordError: unsupported type, non-string key, NaN or infinity.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        return _canonical_number(value)
    if isinstance(value, str):
        text = _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", value)).strip()
        return text or None
    if isinstance(value, Mapping):
        mapped: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidHomeRecordError(f"non-string key {key!r} at {path}")
            canonical = _canonical(item, f"{path}.{key}")
            if canonical is not None:
                mapped[key] = canonical
        return mapped or None
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical(v, f"{path}[]") for v in value]
        distinct = {
            json.dumps(i, sort_keys=True, ensure_ascii=False): i
            for i in items
            if i is not None
        }
        return [distinct[k] for k in sorted(distinct)] or None
    raise InvalidHomeRecordError(
        f"unsupported value type {type(value).__name__} at {path}"
    )


def canonical_text(text_fields: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical form of the text fields (see module docstring).

    Args:
        text_fields: Field name to value, as read for the AI text steps.

    Returns:
        Canonical dict with missing fields removed; ``{}`` when nothing is left.

    Raises:
        InvalidHomeRecordError: unsupported type, non-string key, NaN or infinity.
    """
    canonical = _canonical(dict(text_fields), "text_fields")
    return canonical if isinstance(canonical, dict) else {}


def text_hash(text_fields: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical text fields.

    Args:
        text_fields: Field name to value.

    Returns:
        Lowercase hex digest.

    Raises:
        InvalidHomeRecordError: see ``canonical_text``.
    """
    return _digest({"text": canonical_text(text_fields)})


def _validate_photos(photos: Iterable[PhotoRef]) -> tuple[PhotoRef, ...]:
    """Check photos and lower-case their hashes, keeping order.

    Raises:
        InvalidHomeRecordError: empty or duplicate photo id, malformed sha256.
    """
    checked: list[PhotoRef] = []
    seen: set[str] = set()
    for photo in photos:
        if not photo.photo_id.strip():
            raise InvalidHomeRecordError("photo_id must not be empty")
        digest = photo.sha256.lower()
        if not _SHA256_RE.fullmatch(digest):
            raise InvalidHomeRecordError(
                f"photo {photo.photo_id!r}: sha256 must be 64 hex characters"
            )
        if photo.photo_id in seen:
            raise InvalidHomeRecordError(f"duplicate photo_id {photo.photo_id!r}")
        seen.add(photo.photo_id)
        checked.append(PhotoRef(photo.photo_id, digest))
    return tuple(checked)


def _photo_set_digest(photos: tuple[PhotoRef, ...]) -> str:
    hero = [photos[0].photo_id, photos[0].sha256] if photos else None
    members = sorted([p.photo_id, p.sha256] for p in photos)
    return _digest({"hero": hero, "photos": members})


def photo_set_hash(photos: Iterable[PhotoRef]) -> str:
    """SHA-256 of the hero and the unordered set of ``(photo_id, sha256)`` pairs.

    Args:
        photos: Photos in display order, hero first; only the first one's position counts.

    Returns:
        Lowercase hex digest.

    Raises:
        InvalidHomeRecordError: bad sha256, empty or duplicate photo id.
    """
    return _photo_set_digest(_validate_photos(photos))


def compute_hashes(home: HomeRecord) -> HomeHashes:
    """Compute all digests for one home.

    Args:
        home: Record with text fields and ordered photos.

    Returns:
        Text hash, photo-set hash and the distinct photo content hashes.

    Raises:
        InvalidHomeRecordError: the record cannot be fingerprinted.
    """
    photos = _validate_photos(home.photos)
    return HomeHashes(
        text_hash=text_hash(home.text_fields),
        photo_set_hash=_photo_set_digest(photos),
        photo_hashes=tuple(sorted({p.sha256 for p in photos})),
    )
