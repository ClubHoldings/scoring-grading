"""Real hashing on real-shaped sample data: canonical form, stability, validation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from grade.fingerprint import (
    FORMAT_VERSION,
    HomeRecord,
    InvalidHomeRecordError,
    PhotoRef,
    canonical_text,
    compute_hashes,
    photo_set_hash,
    text_hash,
)
from tests.sample_homes import home, photo, text_fields


def test_golden_digest_is_stable_across_runs() -> None:
    """Pins the canonical form: a change here means every stored fingerprint is stale."""
    assert FORMAT_VERSION == "fp1"
    assert text_hash({"title": "A", "bedrooms": 2}) == (
        "4957cb5522a53f9f0f86c6f8ebc4c0fac7cca944430f5ca02296e0a7bdab35b2"
    )


def test_field_order_does_not_matter() -> None:
    a = {"title": "A", "summary": "B", "sleeps": 4}
    b = {"sleeps": 4, "summary": "B", "title": "A"}
    assert text_hash(a) == text_hash(b)


@pytest.mark.parametrize(
    "variant",
    [
        "A  bright\ttwo-bedroom\r\ncottage.",
        "  A bright two-bedroom cottage.  ",
        "A bright two-bedroom cottage.",
        "A bright two-bedroom\ncottage.\n\n",
    ],
)
def test_whitespace_differences_are_not_changes(variant: str) -> None:
    assert text_hash({"description": variant}) == text_hash(
        {"description": "A bright two-bedroom cottage."}
    )


def test_unicode_normalisation() -> None:
    assert text_hash({"title": "Café"}) == text_hash({"title": "Café"})


def test_case_is_a_change() -> None:
    assert text_hash({"title": "Cottage"}) != text_hash({"title": "cottage"})


def test_missing_values_hash_like_absent_fields() -> None:
    base = text_hash({"title": "A"})
    assert text_hash({"title": "A", "summary": None}) == base
    assert text_hash({"title": "A", "summary": "   "}) == base
    assert text_hash({"title": "A", "amenities": []}) == base
    assert text_hash({"title": "A", "address": {"city": None, "street": ""}}) == base
    assert text_hash({}) == text_hash({"x": None})


def test_zero_and_false_are_values_not_missing() -> None:
    assert text_hash({"bedrooms": 0}) != text_hash({})
    assert text_hash({"pets": False}) != text_hash({})
    assert text_hash({"bedrooms": 0}) != text_hash({"bedrooms": False})


def test_numeric_types_with_same_value_agree() -> None:
    assert (
        text_hash({"n": 3})
        == text_hash({"n": 3.0})
        == text_hash({"n": Decimal("3.00")})
    )
    assert text_hash({"n": 1.5}) == text_hash({"n": Decimal("1.50")})
    assert text_hash({"n": 3}) != text_hash({"n": "3"})
    assert text_hash({"n": 1.5}) != text_hash({"n": 2.5})


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")]
)
def test_non_finite_numbers_rejected(bad: object) -> None:
    with pytest.raises(InvalidHomeRecordError):
        text_hash({"n": bad})


def test_unsupported_type_rejected() -> None:
    with pytest.raises(InvalidHomeRecordError, match="text_fields.when"):
        text_hash({"when": object()})


def test_multi_valued_fields_are_unordered_and_deduplicated() -> None:
    assert text_hash({"amenities": ["Wifi", "Kitchen", "Wifi"]}) == text_hash(
        {"amenities": ("Kitchen", " Wifi ")}
    )
    assert text_hash({"amenities": {"Wifi"}}) == text_hash({"amenities": ["Wifi"]})
    assert text_hash({"amenities": ["Wifi"]}) != text_hash(
        {"amenities": ["Wifi", "Pool"]}
    )


def test_nested_mapping_key_order_does_not_matter() -> None:
    a = {"address": {"city": "Springfield", "state": "IL"}}
    b = {"address": {"state": "IL", "city": "Springfield"}}
    assert text_hash(a) == text_hash(b)


def test_real_edit_changes_hash() -> None:
    base = text_fields()
    edited = text_fields(description="A bright three-bedroom cottage.")
    assert text_hash(base) != text_hash(edited)


def test_canonical_text_is_plain_data() -> None:
    assert canonical_text({"title": " A ", "x": None}) == {"title": "A"}
    assert canonical_text({}) == {}


def test_only_the_hero_position_counts() -> None:
    a, b, c = photo("p1"), photo("p2"), photo("p3")
    assert photo_set_hash([a, b, c]) == photo_set_hash([a, c, b])
    assert photo_set_hash([a, b, c]) != photo_set_hash([b, a, c])
    assert photo_set_hash([a, b]) == photo_set_hash((a, b))
    assert photo_set_hash([]) != photo_set_hash([a])


def test_changed_bytes_or_id_change_the_set_hash() -> None:
    base = photo_set_hash([photo("p1"), photo("p2")])
    assert base != photo_set_hash([photo("p1"), photo("p2", "new bytes")])
    assert base != photo_set_hash([photo("p1"), photo("p9", "bytes-of-p2")])


def test_photo_hash_case_is_normalised() -> None:
    p = photo("p1")
    upper = PhotoRef("p1", p.sha256.upper())
    assert photo_set_hash([upper]) == photo_set_hash([p])


@pytest.mark.parametrize(
    "bad",
    [PhotoRef("p1", "abc"), PhotoRef("p1", "z" * 64), PhotoRef("  ", "a" * 64)],
)
def test_bad_photo_rejected(bad: PhotoRef) -> None:
    with pytest.raises(InvalidHomeRecordError):
        photo_set_hash([bad])


def test_duplicate_photo_id_rejected() -> None:
    with pytest.raises(InvalidHomeRecordError, match="duplicate photo_id"):
        photo_set_hash([photo("p1"), photo("p1", "other")])


def test_compute_hashes_combines_both_levels() -> None:
    h = compute_hashes(home(photo_ids=("p1", "p2")))
    assert h.text_hash == text_hash(text_fields())
    assert h.photo_set_hash == photo_set_hash([photo("p1"), photo("p2")])
    assert h.photo_hashes == tuple(sorted({photo("p1").sha256, photo("p2").sha256}))


def test_same_bytes_under_two_ids_gives_one_content_hash() -> None:
    record = HomeRecord("1", {}, (photo("a", "same"), photo("b", "same")))
    assert len(compute_hashes(record).photo_hashes) == 1


def test_no_photos_is_valid() -> None:
    assert compute_hashes(HomeRecord("1", {})).photo_hashes == ()


def test_long_decimals_do_not_collide_through_float_rounding() -> None:
    a, b = Decimal("0.12345678901234567890"), Decimal("0.12345678901234567891")
    assert text_hash({"n": a}) != text_hash({"n": b})


def test_number_and_numeric_text_stay_distinct() -> None:
    assert text_hash({"n": 2.5}) != text_hash({"n": "2.5"})


def test_non_string_mapping_keys_rejected() -> None:
    with pytest.raises(InvalidHomeRecordError, match="non-string key"):
        text_hash({"address": {1: "a", "1": "b"}})
