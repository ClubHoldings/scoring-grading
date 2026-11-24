"""S3 key layout for property photos (QDEV-889, SG-I2).

The layout is mirrored by the lifecycle rule in infra/terraform/modules/photo_bucket:
``resized/`` is a fixed top-level prefix so one rule expires every resized copy, and
``original/`` is never expired. The content hash in the name makes the copy job idempotent
(same bytes, same key) and exposes changed photos as new keys.
"""

from __future__ import annotations

import hashlib
import re

ORIGINAL_PREFIX = "original"
RESIZED_PREFIX = "resized"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,5}$")
_HOME_RE = re.compile(r"^[0-9]+$")


class InvalidPhotoKeyError(ValueError):
    """Raised when a key component is malformed."""


def content_hash(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of the photo bytes."""
    return hashlib.sha256(data).hexdigest()


def _check(home_id: str, sha256: str, ext: str) -> None:
    if not _HOME_RE.fullmatch(home_id):
        raise InvalidPhotoKeyError(f"home_id must be numeric: {home_id!r}")
    if not _HASH_RE.fullmatch(sha256):
        raise InvalidPhotoKeyError("sha256 must be 64 lowercase hex characters")
    if not _EXT_RE.fullmatch(ext):
        raise InvalidPhotoKeyError(
            f"extension must be 1-5 lowercase alphanumerics: {ext!r}"
        )


def original_key(home_id: str, sha256: str, ext: str) -> str:
    """Key of the untouched copy: ``original/<home_id>/<sha256>.<ext>``.

    Raises:
        InvalidPhotoKeyError: a component is malformed.
    """
    _check(home_id, sha256, ext)
    return f"{ORIGINAL_PREFIX}/{home_id}/{sha256}.{ext}"


def resized_key(home_id: str, sha256: str, width: int, ext: str) -> str:
    """Key of a resized copy: ``resized/<home_id>/<sha256>_<width>.<ext>``.

    Raises:
        InvalidPhotoKeyError: a component is malformed or width is not positive.
    """
    _check(home_id, sha256, ext)
    if width <= 0:
        raise InvalidPhotoKeyError(f"width must be positive: {width}")
    return f"{RESIZED_PREFIX}/{home_id}/{sha256}_{width}.{ext}"
