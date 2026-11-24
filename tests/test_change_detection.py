"""Change detection on real-shaped sample data; real hashing and a real (in-memory) store."""

from __future__ import annotations

import pytest

from grade.change_detection import (
    Fingerprint,
    InMemoryFingerprintStore,
    InvalidVersionsError,
    Reason,
    RunVersions,
    Status,
    StoredFingerprintError,
    detect_changes,
)
from grade.fingerprint import HomeRecord, InvalidHomeRecordError, Step
from tests.sample_homes import home, photo

V1 = RunVersions(
    "r1", "m1", {Step.SEMANTIC: "ps1", Step.VISION: "pv1", Step.MATCH: "pm1"}
)


def _versions(
    rubric: str = "r1",
    model: str = "m1",
    semantic: str = "ps1",
    vision: str = "pv1",
    match: str = "pm1",
) -> RunVersions:
    return RunVersions(
        rubric, model, {Step.SEMANTIC: semantic, Step.VISION: vision, Step.MATCH: match}
    )


def _evaluated(*homes_in: HomeRecord) -> InMemoryFingerprintStore:
    """Store holding the fingerprints a first successful run would have written."""
    store = InMemoryFingerprintStore()
    report = detect_changes(list(homes_in), V1, store)
    store.put_many(d.fingerprint for d in report.to_evaluate)
    return store


def test_first_run_marks_everything_new_with_all_steps() -> None:
    report = detect_changes([home("1"), home("2")], V1, InMemoryFingerprintStore())
    assert [d.status for d in report.decisions] == [Status.NEW, Status.NEW]
    d = report.decisions[0]
    assert d.reasons == {Reason.FIRST_SEEN}
    assert d.steps == (Step.SEMANTIC, Step.VISION, Step.MATCH)
    assert len(d.photos_to_label) == 3
    assert report.to_evaluate == report.decisions


def test_second_run_on_same_data_is_all_unchanged() -> None:
    homes = [home("1"), home("2", photo_ids=("a",))]
    store = _evaluated(*homes)
    report = detect_changes(homes, V1, store)
    assert [d.status for d in report.decisions] == [Status.UNCHANGED] * 2
    assert report.to_evaluate == ()
    assert all(
        d.steps == () and d.photos_to_label == () and not d.reasons
        for d in report.decisions
    )


def test_sample_batch_mixes_new_changed_unchanged() -> None:
    store = _evaluated(home("1"), home("2"))
    report = detect_changes([home("1"), home("2", sleeps=6), home("3")], V1, store)
    assert [d.home_id for d in report.unchanged] == ["1"]
    assert [d.home_id for d in report.changed] == ["2"]
    assert [d.home_id for d in report.new] == ["3"]
    assert [d.home_id for d in report.to_evaluate] == ["2", "3"]


def test_whitespace_only_edit_stays_unchanged() -> None:
    store = _evaluated(home("1"))
    edited = home(
        "1", description="A bright two-bedroom cottage.   Steps from the water.\r\n"
    )
    assert detect_changes([edited], V1, store).decisions[0].status is Status.UNCHANGED


def test_text_change_reruns_text_steps_only() -> None:
    store = _evaluated(home("1"))
    d = detect_changes([home("1", title="Renamed Cottage")], V1, store).decisions[0]
    assert d.status is Status.CHANGED
    assert d.reasons == {Reason.TEXT_CHANGED}
    assert d.steps == (Step.SEMANTIC, Step.MATCH)
    assert d.photos_to_label == ()


def test_added_photo_labels_only_the_new_photo() -> None:
    store = _evaluated(home("1", photo_ids=("p1", "p2")))
    d = detect_changes([home("1", photo_ids=("p1", "p2", "p3"))], V1, store).decisions[
        0
    ]
    assert d.reasons == {Reason.PHOTOS_CHANGED}
    assert d.steps == (Step.VISION, Step.MATCH)
    assert d.photos_to_label == (photo("p3").sha256,)


def test_replaced_photo_bytes_label_only_the_new_hash() -> None:
    store = _evaluated(home("1", photo_ids=("p1", "p2")))
    changed = home("1", photo_ids=("p1",))
    changed = type(changed)(
        "1", changed.text_fields, (photo("p1"), photo("p2", "retouched"))
    )
    d = detect_changes([changed], V1, store).decisions[0]
    assert d.photos_to_label == (photo("p2", "retouched").sha256,)


def test_reorder_reruns_vision_but_reuses_every_cached_label() -> None:
    store = _evaluated(home("1", photo_ids=("p1", "p2", "p3")))
    d = detect_changes([home("1", photo_ids=("p3", "p1", "p2"))], V1, store).decisions[
        0
    ]
    assert d.reasons == {Reason.PHOTOS_CHANGED}
    assert d.steps == (Step.VISION, Step.MATCH)
    assert d.photos_to_label == ()


def test_removed_photo_reruns_vision_without_new_labels() -> None:
    store = _evaluated(home("1", photo_ids=("p1", "p2")))
    d = detect_changes([home("1", photo_ids=("p1",))], V1, store).decisions[0]
    assert d.steps == (Step.VISION, Step.MATCH)
    assert d.photos_to_label == ()


@pytest.mark.parametrize(
    ("versions", "reason"),
    [
        (_versions(rubric="r2"), Reason.RUBRIC_VERSION_CHANGED),
        (_versions(model="m2"), Reason.MODEL_VERSION_CHANGED),
    ],
)
def test_rubric_or_model_change_forces_full_rerun(
    versions: RunVersions, reason: Reason
) -> None:
    h = home("1")
    store = _evaluated(h)
    d = detect_changes([h], versions, store).decisions[0]
    assert d.status is Status.CHANGED
    assert d.reasons == {reason}
    assert d.steps == tuple(Step)
    assert set(d.photos_to_label) == {photo(p).sha256 for p in ("p1", "p2", "p3")}


@pytest.mark.parametrize(
    ("versions", "steps", "labels_all"),
    [
        (_versions(semantic="ps2"), (Step.SEMANTIC, Step.MATCH), False),
        (_versions(vision="pv2"), (Step.VISION, Step.MATCH), True),
        (_versions(match="pm2"), (Step.MATCH,), False),
    ],
)
def test_prompt_change_reruns_only_the_affected_step(
    versions: RunVersions, steps: tuple[Step, ...], labels_all: bool
) -> None:
    h = home("1")
    store = _evaluated(h)
    d = detect_changes([h], versions, store).decisions[0]
    assert d.reasons == {Reason.PROMPT_VERSION_CHANGED}
    assert d.steps == steps
    assert bool(d.photos_to_label) is labels_all


def test_version_and_data_changes_combine() -> None:
    store = _evaluated(home("1"))
    d = detect_changes(
        [home("1", sleeps=9)], _versions(semantic="ps2"), store
    ).decisions[0]
    assert d.reasons == {Reason.TEXT_CHANGED, Reason.PROMPT_VERSION_CHANGED}
    assert d.steps == (Step.SEMANTIC, Step.MATCH)


def test_stale_format_version_forces_full_rerun() -> None:
    store = InMemoryFingerprintStore()
    fresh = detect_changes([home("1")], V1, store).decisions[0].fingerprint
    old = fresh.to_dict() | {"format_version": "fp0"}
    store.put_many([Fingerprint.from_dict(old)])
    d = detect_changes([home("1")], V1, store).decisions[0]
    assert d.reasons == {Reason.FORMAT_CHANGED}
    assert d.steps == tuple(Step)


def test_detect_does_not_write_so_failed_ai_homes_are_retried() -> None:
    store = InMemoryFingerprintStore()
    detect_changes([home("1")], V1, store)
    assert detect_changes([home("1")], V1, store).decisions[0].status is Status.NEW


def test_duplicate_home_ids_rejected() -> None:
    with pytest.raises(InvalidHomeRecordError, match="duplicate home_id"):
        detect_changes([home("1"), home("1")], V1, InMemoryFingerprintStore())


def test_fingerprint_roundtrips_through_dict() -> None:
    fp = (
        detect_changes([home("1")], V1, InMemoryFingerprintStore())
        .decisions[0]
        .fingerprint
    )
    assert Fingerprint.from_dict(fp.to_dict()) == fp
    assert fp.prompt_versions == {"semantic": "ps1", "vision": "pv1", "match": "pm1"}


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"home_id": "1"},
        {"photo_hashes": "abc", "prompt_versions": {}},
        {"photo_hashes": [], "prompt_versions": []},
        {"photo_hashes": [], "prompt_versions": {"semantic": "x"}},
    ],
)
def test_unreadable_stored_fingerprint_raises(broken: dict[str, object]) -> None:
    with pytest.raises(StoredFingerprintError):
        Fingerprint.from_dict(broken)


@pytest.mark.parametrize(
    "args",
    [
        ("", "m", {Step.SEMANTIC: "a", Step.VISION: "b", Step.MATCH: "c"}),
        ("r", " ", {Step.SEMANTIC: "a", Step.VISION: "b", Step.MATCH: "c"}),
        ("r", "m", {Step.SEMANTIC: "a", Step.VISION: "b"}),
        ("r", "m", {Step.SEMANTIC: "a", Step.VISION: "", Step.MATCH: "c"}),
    ],
)
def test_incomplete_versions_rejected(args: tuple[str, str, dict[Step, str]]) -> None:
    with pytest.raises(InvalidVersionsError):
        RunVersions(*args)
