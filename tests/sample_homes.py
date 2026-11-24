"""Synthetic, real-shaped home records for the fingerprint tests (no real property data)."""

from __future__ import annotations

import hashlib
from typing import Any

from grade.fingerprint import HomeRecord, PhotoRef


def photo(photo_id: str, content: str | None = None) -> PhotoRef:
    """Photo whose hash is the real SHA-256 of synthetic bytes."""
    data = (content or f"bytes-of-{photo_id}").encode()
    return PhotoRef(photo_id, hashlib.sha256(data).hexdigest())


def text_fields(**overrides: Any) -> dict[str, Any]:
    """Text fields the semantic step reads (design section 7), with sample values."""
    fields: dict[str, Any] = {
        "title": "Sunny Cottage by the Lake",
        "description": "A bright two-bedroom cottage.\nSteps from the water.",
        "summary": "Lake cottage",
        "amenities": ["Wifi", "Kitchen", "Fireplace"],
        "address": {
            "city": "Springfield",
            "state": "IL",
            "country": "US",
            "street": "1 Test Rd",
        },
        "bedrooms": 2,
        "bathrooms": 1.5,
        "sleeps": 4,
        "stored_type": "cottage",
    }
    fields.update(overrides)
    return fields


def home(
    home_id: str = "1001",
    photo_ids: tuple[str, ...] = ("p1", "p2", "p3"),
    **overrides: Any,
) -> HomeRecord:
    """Home with sample text and photos."""
    return HomeRecord(
        home_id, text_fields(**overrides), tuple(photo(p) for p in photo_ids)
    )
