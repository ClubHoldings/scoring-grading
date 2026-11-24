"""Tests for the photo key layout: real hashing, no mocks."""

from __future__ import annotations

import pytest

from grade.photo_keys import (
    InvalidPhotoKeyError,
    content_hash,
    original_key,
    resized_key,
)

PNG = b"\x89PNG\r\n\x1a\nfake-bytes"


def test_same_bytes_give_same_key_and_changed_bytes_give_new_key() -> None:
    h1, h2 = content_hash(PNG), content_hash(PNG + b"x")
    assert original_key("42", h1, "jpg") == original_key("42", content_hash(PNG), "jpg")
    assert original_key("42", h1, "jpg") != original_key("42", h2, "jpg")


def test_layout_matches_lifecycle_prefixes() -> None:
    h = content_hash(PNG)
    assert original_key("42", h, "jpg") == f"original/42/{h}.jpg"
    assert resized_key("42", h, 1024, "jpg") == f"resized/42/{h}_1024.jpg"


@pytest.mark.parametrize(
    ("home", "digest", "ext"),
    [
        ("4/2", "a" * 64, "jpg"),
        ("42", "A" * 64, "jpg"),
        ("42", "a" * 63, "jpg"),
        ("42", "a" * 64, "../x"),
    ],
)
def test_malformed_components_rejected(home: str, digest: str, ext: str) -> None:
    with pytest.raises(InvalidPhotoKeyError):
        original_key(home, digest, ext)


def test_non_positive_width_rejected() -> None:
    with pytest.raises(InvalidPhotoKeyError):
        resized_key("42", "a" * 64, 0, "jpg")
