"""In-memory fingerprint store for tests and local runs (SG-B4, QDEV-881).

Implements the ``FingerprintStore`` protocol from ``grade.change_detection``. Rows are kept
serialised (``Fingerprint.to_dict``) so reads behave like a real table and a broken row
surfaces as ``StoredFingerprintError``, as it would from SG-B1's result tables or Iceberg.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from grade.change_detection import Fingerprint


class InMemoryFingerprintStore:
    """Dict-backed store."""

    def __init__(self) -> None:
        """Create an empty store."""
        self._rows: dict[str, dict[str, object]] = {}

    def get_many(self, home_ids: Sequence[str]) -> Mapping[str, Fingerprint]:
        """Return stored fingerprints for the ids that exist.

        Args:
            home_ids: Ids to look up.

        Returns:
            Fingerprints of the known ids.

        Raises:
            StoredFingerprintError: a stored row is corrupt.
        """
        return {h: Fingerprint.from_dict(self._rows[h]) for h in home_ids if h in self._rows}

    def put_many(self, fingerprints: Iterable[Fingerprint]) -> None:
        """Store fingerprints, replacing earlier ones of the same home.

        Args:
            fingerprints: Fingerprints to write.
        """
        for fp in fingerprints:
            self._rows[fp.home_id] = fp.to_dict()
