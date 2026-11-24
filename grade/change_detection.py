"""Change detection: which homes, and which AI steps, must run again (SG-B4, QDEV-881).

Compares each home's fresh fingerprint (``grade.fingerprint``) with the last stored one and
the pinned rubric / prompt / model versions. Only changed homes go to the AI; unchanged
homes reuse their stored result (design v0.1, section 7.4).

Granularity follows the agreed two-level design (Jira QDEV-881 comment): within a changed
home only the affected step reruns.

=================  ===========================================================
Change             Steps that rerun
=================  ===========================================================
text               semantic, match
photo set          vision, match; labels only for photos with a new content hash
prompt of a step   that step, plus match when it feeds match
rubric or model    all steps; every photo is labelled again
first time seen    all steps; every photo is labelled
=================  ===========================================================

The match check reads the semantic claims and the vision output, so it reruns whenever
either of them does.

Storage contract: ``detect_changes`` only reads. The caller stores ``decision.fingerprint``
(``store.put_many``) **after** the AI steps for that home succeeded. Storing earlier would
mark a home with a failed AI call as "unchanged" on the next night and silently skip it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

import structlog

from grade.fingerprint import (
    FORMAT_VERSION,
    FingerprintError,
    HomeRecord,
    InvalidHomeRecordError,
    Step,
    compute_hashes,
)

logger = structlog.get_logger(__name__)


class InvalidVersionsError(FingerprintError):
    """Pinned versions are incomplete or empty."""


class StoredFingerprintError(FingerprintError):
    """A stored fingerprint cannot be read back."""


class Status(StrEnum):
    """Outcome per home."""

    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class Reason(StrEnum):
    """Why a home must (re)run. A home can have several."""

    FIRST_SEEN = "first_seen"
    FORMAT_CHANGED = "format_changed"
    TEXT_CHANGED = "text_changed"
    PHOTOS_CHANGED = "photos_changed"
    RUBRIC_VERSION_CHANGED = "rubric_version_changed"
    PROMPT_VERSION_CHANGED = "prompt_version_changed"
    MODEL_VERSION_CHANGED = "model_version_changed"


@dataclass(frozen=True, slots=True)
class RunVersions:
    """Pinned versions of this run (settings, not code; design section 7.4).

    Attributes:
        rubric: Rubric version, shared by all steps.
        model: Model version, shared by all steps.
        prompts: Prompt version per step; every ``Step`` must be present.
    """

    rubric: str
    model: str
    prompts: Mapping[Step, str]

    def __post_init__(self) -> None:
        """Validate that nothing is empty and every step has a prompt version.

        Raises:
            InvalidVersionsError: a version is empty or a step has no prompt version.
        """
        missing = [s.value for s in Step if not self.prompts.get(s, "").strip()]
        if missing:
            raise InvalidVersionsError(
                f"prompt version missing for: {', '.join(missing)}"
            )
        if not self.rubric.strip() or not self.model.strip():
            raise InvalidVersionsError("rubric and model versions must not be empty")


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What is stored per home after a successful AI run.

    Attributes:
        home_id: Home the fingerprint belongs to.
        format_version: Canonical-form version it was computed with.
        text_hash: Hash of the canonical text fields.
        photo_set_hash: Hash of the ordered photo list.
        photo_hashes: Sorted distinct photo content hashes.
        rubric_version: Pinned rubric version of the run.
        model_version: Pinned model version of the run.
        prompt_versions: Pinned prompt version per step name.
    """

    home_id: str
    format_version: str
    text_hash: str
    photo_set_hash: str
    photo_hashes: tuple[str, ...]
    rubric_version: str
    model_version: str
    prompt_versions: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-ready dict, for SG-B1 result tables or Iceberg rows."""
        return {
            "home_id": self.home_id,
            "format_version": self.format_version,
            "text_hash": self.text_hash,
            "photo_set_hash": self.photo_set_hash,
            "photo_hashes": list(self.photo_hashes),
            "rubric_version": self.rubric_version,
            "model_version": self.model_version,
            "prompt_versions": dict(self.prompt_versions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Fingerprint:
        """Inverse of ``to_dict``.

        Raises:
            StoredFingerprintError: a field is missing or has the wrong type.
        """
        try:
            photo_hashes = data["photo_hashes"]
            prompts = data["prompt_versions"]
            if not isinstance(photo_hashes, Sequence) or isinstance(photo_hashes, str):
                raise TypeError("photo_hashes must be a list")
            if not isinstance(prompts, Mapping):
                raise TypeError("prompt_versions must be a mapping")
            missing = [s.value for s in Step if s.value not in prompts]
            if missing:
                raise KeyError(f"prompt_versions lacks {missing}")
            return cls(
                home_id=str(data["home_id"]),
                format_version=str(data["format_version"]),
                text_hash=str(data["text_hash"]),
                photo_set_hash=str(data["photo_set_hash"]),
                photo_hashes=tuple(str(h) for h in photo_hashes),
                rubric_version=str(data["rubric_version"]),
                model_version=str(data["model_version"]),
                prompt_versions={str(k): str(v) for k, v in prompts.items()},
            )
        except (KeyError, TypeError) as exc:
            raise StoredFingerprintError(
                f"unreadable stored fingerprint: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class Decision:
    """Verdict for one home.

    Attributes:
        home_id: The home.
        status: new, changed or unchanged.
        reasons: Why; empty only when unchanged.
        steps: AI steps to run, in ``Step`` order; empty when unchanged.
        photos_to_label: Content hashes the vision step must label. Hashes not listed
            keep their cached labels. Empty when vision does not run.
        fingerprint: Fresh fingerprint to store once the steps succeeded.
    """

    home_id: str
    status: Status
    reasons: frozenset[Reason]
    steps: tuple[Step, ...]
    photos_to_label: tuple[str, ...]
    fingerprint: Fingerprint


@dataclass(frozen=True, slots=True)
class ChangeReport:
    """All decisions of one run, in input order."""

    decisions: tuple[Decision, ...] = field(default_factory=tuple)

    def _with(self, status: Status) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if d.status is status)

    @property
    def new(self) -> tuple[Decision, ...]:
        """Homes never evaluated before."""
        return self._with(Status.NEW)

    @property
    def changed(self) -> tuple[Decision, ...]:
        """Known homes with a text, photo or version change."""
        return self._with(Status.CHANGED)

    @property
    def unchanged(self) -> tuple[Decision, ...]:
        """Homes that reuse their stored result."""
        return self._with(Status.UNCHANGED)

    @property
    def to_evaluate(self) -> tuple[Decision, ...]:
        """New and changed homes: the only ones sent to the AI."""
        return tuple(d for d in self.decisions if d.status is not Status.UNCHANGED)


class FingerprintStore(Protocol):
    """Storage interface; SG-B1 result tables or Iceberg implement it later."""

    def get_many(self, home_ids: Sequence[str]) -> Mapping[str, Fingerprint]:
        """Return the last stored fingerprint for each known home id (absent = unknown)."""
        ...

    def put_many(self, fingerprints: Iterable[Fingerprint]) -> None:
        """Store fingerprints, replacing any earlier one of the same home."""
        ...


class InMemoryFingerprintStore:
    """Dict-backed store for tests and local runs."""

    def __init__(self) -> None:
        """Create an empty store."""
        self._rows: dict[str, dict[str, object]] = {}

    def get_many(self, home_ids: Sequence[str]) -> Mapping[str, Fingerprint]:
        """Return stored fingerprints for the ids that exist.

        Raises:
            StoredFingerprintError: a stored row is corrupt.
        """
        return {
            h: Fingerprint.from_dict(self._rows[h]) for h in home_ids if h in self._rows
        }

    def put_many(self, fingerprints: Iterable[Fingerprint]) -> None:
        """Store (serialised, so reads behave like a real table)."""
        for fp in fingerprints:
            self._rows[fp.home_id] = fp.to_dict()


def _current(home: HomeRecord, versions: RunVersions) -> Fingerprint:
    hashes = compute_hashes(home)
    return Fingerprint(
        home_id=home.home_id,
        format_version=FORMAT_VERSION,
        text_hash=hashes.text_hash,
        photo_set_hash=hashes.photo_set_hash,
        photo_hashes=hashes.photo_hashes,
        rubric_version=versions.rubric,
        model_version=versions.model,
        prompt_versions={s.value: versions.prompts[s] for s in Step},
    )


def _decide(fresh: Fingerprint, stored: Fingerprint | None) -> Decision:
    all_steps = tuple(Step)
    if stored is None:
        return Decision(
            fresh.home_id, Status.NEW, frozenset({Reason.FIRST_SEEN}), all_steps,
            fresh.photo_hashes, fresh,
        )  # fmt: skip
    if stored.format_version != fresh.format_version:
        return Decision(
            fresh.home_id, Status.CHANGED, frozenset({Reason.FORMAT_CHANGED}), all_steps,
            fresh.photo_hashes, fresh,
        )  # fmt: skip

    reasons: set[Reason] = set()
    steps: set[Step] = set()
    if stored.rubric_version != fresh.rubric_version:
        reasons.add(Reason.RUBRIC_VERSION_CHANGED)
        steps.update(Step)
    if stored.model_version != fresh.model_version:
        reasons.add(Reason.MODEL_VERSION_CHANGED)
        steps.update(Step)
    changed_prompts = {
        s
        for s in Step
        if stored.prompt_versions[s.value] != fresh.prompt_versions[s.value]
    }
    if changed_prompts:
        reasons.add(Reason.PROMPT_VERSION_CHANGED)
        steps.update(changed_prompts)
    # Photo labels are cached per content hash; they stay valid only while the vision
    # step itself (rubric, model, prompt) is unchanged.
    labels_valid = Step.VISION not in steps
    if stored.text_hash != fresh.text_hash:
        reasons.add(Reason.TEXT_CHANGED)
        steps.add(Step.SEMANTIC)
    if stored.photo_set_hash != fresh.photo_set_hash:
        reasons.add(Reason.PHOTOS_CHANGED)
        steps.add(Step.VISION)
    if Step.SEMANTIC in steps or Step.VISION in steps:
        steps.add(Step.MATCH)

    if not steps:
        return Decision(fresh.home_id, Status.UNCHANGED, frozenset(), (), (), fresh)
    ordered = tuple(s for s in Step if s in steps)
    if Step.VISION not in steps:
        to_label: tuple[str, ...] = ()
    elif labels_valid:
        known = set(stored.photo_hashes)
        to_label = tuple(h for h in fresh.photo_hashes if h not in known)
    else:
        to_label = fresh.photo_hashes
    return Decision(
        fresh.home_id, Status.CHANGED, frozenset(reasons), ordered, to_label, fresh
    )


def detect_changes(
    homes: Sequence[HomeRecord], versions: RunVersions, store: FingerprintStore
) -> ChangeReport:
    """Classify every home as new, changed or unchanged.

    Args:
        homes: Home records of this run; ids must be unique.
        versions: Pinned rubric, prompt and model versions of this run.
        store: Source of the last stored fingerprints. Only read.

    Returns:
        One decision per home, in input order.

    Raises:
        InvalidHomeRecordError: duplicate home id, or a record cannot be fingerprinted.
        StoredFingerprintError: the store returned an unreadable fingerprint.
    """
    ids = [h.home_id for h in homes]
    if len(set(ids)) != len(ids):
        raise InvalidHomeRecordError("duplicate home_id in input")
    stored = store.get_many(ids)
    report = ChangeReport(
        tuple(_decide(_current(h, versions), stored.get(h.home_id)) for h in homes)
    )
    logger.info(
        "change_detection_done",
        homes=len(homes),
        new=len(report.new),
        changed=len(report.changed),
        unchanged=len(report.unchanged),
    )
    return report
