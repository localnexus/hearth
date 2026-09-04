"""floor.py — the compaction floor: recall over canonical records, zero extras.

Decider 5: a fresh install has cross-session memory on day one. The floor's
recall is the last N canonical records rendered through the deterministic
digest — no LLM, no embeddings, no daemon, no dependency beyond stdlib.

``store`` is a no-op ON PURPOSE: the seam itself writes the canonical record
before any backend sees it (the record is the substrate, the
backend an index; the floor IS the substrate reader, so it has nothing to
index). That also makes it the degrade-to-floor target: when a
richer backend fails, the seam answers from here, and memory absent means
"the companion doesn't recall", never "session down".
"""

from __future__ import annotations

from pathlib import Path

from .backend import MemoryItem, SessionRecord, digest_record
from . import records as records_mod


class FloorBackend:
    """Recall = last N records, digested. The zero-dependency floor."""

    name = "floor"

    def __init__(self, directory: Path | None = None) -> None:
        # Explicit directory is the test seam (session_store's sessions_dir shape);
        # None → the companion's own records dir under the data root.
        self._directory = directory

    def recall(self, companion: str, query: str, limit: int) -> list[MemoryItem]:
        # ``query`` is accepted per the protocol and ignored: the floor is
        # recency-based, not semantic. That is its honesty — it never claims
        # relevance it can't compute.
        items: list[MemoryItem] = []
        for record in records_mod.iter_records(companion, self._directory, newest_first=True):
            if len(items) >= max(0, int(limit)):
                break
            items.append(
                MemoryItem(
                    text=digest_record(record),
                    source_session=record.session_id,
                    when=(record.ended or record.started)[:10],
                    confidence=1.0,
                )
            )
        return items

    def store(self, companion: str, record: SessionRecord) -> None:  # noqa: ARG002
        """No-op: the seam already wrote the canonical record (see module doc)."""

    def consolidate(self, companion: str) -> None:  # noqa: ARG002
        """No-op: nothing to consolidate in a plain record store."""

    def forget(self, companion: str, session_id: str) -> bool:  # noqa: ARG002
        """True unconditionally: the floor indexes nothing, so deleting the
        record file (the caller's half of a forget) already removed the
        session from its recall — next turn, completely."""
        return True

    def clear(self, companion: str) -> None:  # noqa: ARG002
        """No-op: no derived index to drop (see store)."""

    def close(self) -> None:
        """No resources held."""
