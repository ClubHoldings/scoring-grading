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
photo set or hero  vision, match; labels only for photos with a new content hash
prompt of a step   that step, plus match when it feeds match
rubric or model    all steps; every photo is labelled again
canonical format   all steps; photo labels are kept (content hashes do not change)
first time seen    all steps; every photo is labelled
=================  ===========================================================

The match check reads the semantic claims and the vision output, so it reruns whenever
either of them does. Reasons accumulate: a home with a text edit and a new prompt version
reports both.

Storage contract: ``detect_changes`` only reads. The caller stores ``decision.fingerprint``
(``store.put_many``) **after** the AI steps for that home succeeded. Storing earlier would
mark a home with a failed AI call as "unchanged" on the next night and silently skip it.

A home that cannot be fingerprinted (bad photo hash, NaN, duplicate id) is reported in
``ChangeReport.rejected`` and does not stop the other homes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import structlog

from grade.fingerprint import (
    FORMAT_VERSION,
    FingerprintError,
    HomeHashes,
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
            raise InvalidVersionsError(f"prompt version missing for: {', '.join(missing)}")
        if not self.rubric.strip() or not self.model.strip():
            raise InvalidVersionsError("rubric and model versions must not be empty")


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What is stored per home after a successful AI run.

    Flat on purpose: it maps one-to-one onto a result-table row. Prompt versions are keyed
    by the step's string value so a step added later does not break stored rows.

    Attributes:
        home_id: Home the fingerprint belongs to.
        format_version: Canonical-form version it was computed with.
        text_hash: Hash of the canonical text fields.
        photo_set_hash: Hash of the hero plus the unordered photo set.
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

    @classmethod
    def build(cls, home_id: str, hashes: HomeHashes, versions: RunVersions) -> Fingerprint:
        """Combine fresh hashes with the pinned versions of this run.

        Args:
            home_id: Home the hashes belong to.
            hashes: Digests from ``compute_hashes``.
            versions: Pinned versions of this run.

        Returns:
            The fingerprint to store once the AI steps succeeded.
        """
        return cls(
            home_id=home_id,
            format_version=FORMAT_VERSION,
            text_hash=hashes.text_hash,
            photo_set_hash=hashes.photo_set_hash,
            photo_hashes=hashes.photo_hashes,
            rubric_version=versions.rubric,
            model_version=versions.model,
            prompt_versions={s.value: versions.prompts[s] for s in Step},
        )

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

        Prompt versions of steps unknown to the row are left out, so ``detect_changes``
        sees them as changed instead of failing the batch.

        Args:
            data: Row as written by ``to_dict``.

        Returns:
            The fingerprint.

        Raises:
            StoredFingerprintError: a field is missing or has the wrong type.
        """
        try:
            photo_hashes = data["photo_hashes"]
            prompts = data["prompt_versions"]
            home_id = data["home_id"]
        except KeyError as exc:
            raise StoredFingerprintError(f"stored fingerprint lacks {exc}") from exc
        if not isinstance(photo_hashes, Sequence) or isinstance(photo_hashes, str):
            raise StoredFingerprintError("photo_hashes must be a list")
        if not isinstance(prompts, Mapping):
            raise StoredFingerprintError("prompt_versions must be a mapping")
        try:
            return cls(
                home_id=str(home_id),
                format_version=str(data["format_version"]),
                text_hash=str(data["text_hash"]),
                photo_set_hash=str(data["photo_set_hash"]),
                photo_hashes=tuple(str(h) for h in photo_hashes),
                rubric_version=str(data["rubric_version"]),
                model_version=str(data["model_version"]),
                prompt_versions={str(k): str(v) for k, v in prompts.items()},
            )
        except KeyError as exc:
            raise StoredFingerprintError(f"stored fingerprint lacks {exc}") from exc


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
class RejectedHome:
    """A home that could not be fingerprinted and was skipped.

    Attributes:
        home_id: The home (may appear more than once for duplicate ids).
        reason: Why, without any home text.
    """

    home_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChangeReport:
    """All decisions of one run, in input order, plus the homes that were rejected."""

    decisions: tuple[Decision, ...] = ()
    rejected: tuple[RejectedHome, ...] = ()

    def _by_status(self, status: Status) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if d.status is status)

    @property
    def new(self) -> tuple[Decision, ...]:
        """Homes never evaluated before."""
        return self._by_status(Status.NEW)

    @property
    def changed(self) -> tuple[Decision, ...]:
        """Known homes with a text, photo or version change."""
        return self._by_status(Status.CHANGED)

    @property
    def unchanged(self) -> tuple[Decision, ...]:
        """Homes that reuse their stored result."""
        return self._by_status(Status.UNCHANGED)

    @property
    def to_evaluate(self) -> tuple[Decision, ...]:
        """New and changed homes: the only ones sent to the AI."""
        return tuple(d for d in self.decisions if d.status is not Status.UNCHANGED)


class FingerprintStore(Protocol):
    """Storage interface; SG-B1 result tables or Iceberg implement it later."""

    def get_many(self, home_ids: Sequence[str]) -> Mapping[str, Fingerprint]:
        """Return the last stored fingerprint per known home id.

        Args:
            home_ids: Ids to look up.

        Returns:
            Fingerprints for the ids that exist; unknown ids are absent.
        """
        ...

    def put_many(self, fingerprints: Iterable[Fingerprint]) -> None:
        """Store fingerprints, replacing any earlier one of the same home.

        Args:
            fingerprints: Fingerprints to write.
        """
        ...


def _decision(
    fresh: Fingerprint,
    status: Status,
    reasons: Iterable[Reason],
    steps: Iterable[Step],
    photos_to_label: Iterable[str],
) -> Decision:
    wanted = set(steps)
    return Decision(
        home_id=fresh.home_id,
        status=status,
        reasons=frozenset(reasons),
        steps=tuple(s for s in Step if s in wanted),
        photos_to_label=tuple(photos_to_label),
        fingerprint=fresh,
    )


def _decide(fresh: Fingerprint, stored: Fingerprint | None) -> Decision:
    """Compare a fresh fingerprint with the stored one (see module docstring table).

    Args:
        fresh: Fingerprint of the home as it is now, with this run's versions.
        stored: Last stored fingerprint, or ``None`` for a home never evaluated.

    Returns:
        The verdict, including which steps rerun and which photos need labels.
    """
    if stored is None:
        return _decision(fresh, Status.NEW, [Reason.FIRST_SEEN], Step, fresh.photo_hashes)

    reasons: set[Reason] = set()
    steps: set[Step] = set()
    if stored.rubric_version != fresh.rubric_version:
        reasons.add(Reason.RUBRIC_VERSION_CHANGED)
        steps.update(Step)
    if stored.model_version != fresh.model_version:
        reasons.add(Reason.MODEL_VERSION_CHANGED)
        steps.update(Step)
    for step in Step:
        if stored.prompt_versions.get(step.value) != fresh.prompt_versions[step.value]:
            reasons.add(Reason.PROMPT_VERSION_CHANGED)
            steps.add(step)
    # Cached photo labels stay valid only while the vision step itself (rubric, model,
    # prompt) is unchanged. Decided here, before data changes add vision to the steps.
    labels_valid = Step.VISION not in steps

    if stored.format_version != fresh.format_version:
        # Hashes from another canonical form are not comparable: rerun all steps.
        reasons.add(Reason.FORMAT_CHANGED)
        steps.update(Step)
    else:
        if stored.text_hash != fresh.text_hash:
            reasons.add(Reason.TEXT_CHANGED)
            steps.add(Step.SEMANTIC)
        if stored.photo_set_hash != fresh.photo_set_hash:
            reasons.add(Reason.PHOTOS_CHANGED)
            steps.add(Step.VISION)
    if Step.SEMANTIC in steps or Step.VISION in steps:
        steps.add(Step.MATCH)

    if not steps:
        return _decision(fresh, Status.UNCHANGED, [], [], [])
    if Step.VISION not in steps:
        to_label: Iterable[str] = ()
    elif labels_valid:
        known = set(stored.photo_hashes)
        to_label = (h for h in fresh.photo_hashes if h not in known)
    else:
        to_label = fresh.photo_hashes
    return _decision(fresh, Status.CHANGED, reasons, steps, to_label)


def _fingerprint_all(
    homes: Sequence[HomeRecord], versions: RunVersions
) -> tuple[list[Fingerprint], list[RejectedHome]]:
    """Fingerprint every home, rejecting duplicates and invalid records."""
    id_counts = Counter(h.home_id for h in homes)
    fresh: list[Fingerprint] = []
    rejected: list[RejectedHome] = []
    for home in homes:
        if id_counts[home.home_id] > 1:
            rejected.append(RejectedHome(home.home_id, "duplicate home_id in input"))
            continue
        try:
            hashes = compute_hashes(home)
        except InvalidHomeRecordError as exc:
            rejected.append(RejectedHome(home.home_id, str(exc)))
            continue
        fresh.append(Fingerprint.build(home.home_id, hashes, versions))
    return fresh, rejected


def detect_changes(
    homes: Sequence[HomeRecord], versions: RunVersions, store: FingerprintStore
) -> ChangeReport:
    """Classify every home as new, changed or unchanged.

    Args:
        homes: Home records of this run.
        versions: Pinned rubric, prompt and model versions of this run.
        store: Source of the last stored fingerprints. Only read.

    Returns:
        One decision per valid home in input order, plus the rejected homes (invalid
        record, or an id that appears more than once; all its occurrences are rejected).

    Raises:
        StoredFingerprintError: the store returned an unreadable fingerprint.
    """
    fresh, rejected = _fingerprint_all(homes, versions)
    stored = store.get_many([f.home_id for f in fresh])
    report = ChangeReport(
        decisions=tuple(_decide(f, stored.get(f.home_id)) for f in fresh),
        rejected=tuple(rejected),
    )
    if rejected:
        logger.warning(
            "change_detection_rejected",
            count=len(rejected),
            home_ids=[r.home_id for r in rejected],
        )
    logger.info(
        "change_detection_done",
        homes=len(homes),
        new=len(report.new),
        changed=len(report.changed),
        unchanged=len(report.unchanged),
        rejected=len(rejected),
    )
    return report
