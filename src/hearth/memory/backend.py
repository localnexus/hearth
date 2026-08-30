"""backend.py — the memory seam's contract: item, record, and backend protocol.

Design source: the signed memory-seam design (two hooks + optional consolidate,
a backend per companion). The types here ARE the contract:

  * ``MemoryItem`` is the only thing ``recall`` may return — provenance
    (source_session, when) is enforced by the type, not by review (decider 1:
    wrong memory is worse than no memory; backends that cannot attach
    provenance are not admitted).
  * ``SessionRecord`` is the canonical substrate (decider 7): Hearth's own
    format, written by the seam on every graceful session end, outliving any
    backend. Every backend must be rebuildable by replaying records through
    ``store`` — backend = disposable cache, record = hers.
  * ``MemoryBackend`` is the protocol adapters implement (~100 lines each).
    Heavy dependencies live behind optional extras (pip install
    hearth[memory-<backend>]); the floor needs none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryItem:
    """One recalled memory, provenance-carrying by construction.

    ``when`` is an ISO date (YYYY-MM-DD) when the backend knows it; a backend
    whose items embed their own temporal phrasing inside ``text`` (Hindsight's
    "When: …" facts) may leave it "" — the prompt framing then relies on the
    text itself. ``source_session`` names the record or bank the item came
    from, so the "what she remembers" surface can trace every line.
    """

    text: str
    source_session: str
    when: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class SessionRecord:
    """The canonical per-session memory record (schema 1).

    Carries the full persistable message list — the substrate a replaying
    backend indexes — plus the metadata recall framing needs. ``messages``
    follow session_store's shape: ``{"role": ..., "content": ...}`` dicts,
    system role excluded.
    """

    companion: str
    session_id: str
    started: str
    ended: str
    name: str = ""
    persona: str = "default"
    messages: list = field(default_factory=list)


@runtime_checkable
class MemoryBackend(Protocol):
    """What every memory backend implements. All four methods are synchronous
    and run OFF the per-turn path (recall at session start, store/consolidate
    at session end) — the voice loop is sub-500 ms and never waits on these.
    """

    name: str

    def recall(self, companion: str, query: str, limit: int) -> list[MemoryItem]:
        """≤ limit items for this companion. Must not call an LLM per item."""
        ...

    def store(self, companion: str, record: SessionRecord) -> None:
        """Index one canonical record. Extraction is the backend's business."""
        ...

    def consolidate(self, companion: str) -> None:
        """Optional idle-time internal maintenance. Default no-op."""
        ...

    def close(self) -> None:
        """Release resources (stop an embedded server, close handles)."""
        ...


# ── deterministic digest (no LLM — the floor's recall text) ──────────────────

def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def digest_record(record: SessionRecord) -> str:
    """A one-paragraph, deterministic, LLM-free digest of a session record.

    This is what the compaction floor injects: enough for "last time we
    spoke about …" continuity on a fresh install with zero extra
    dependencies (decider 5). Deliberately extractive — first user line,
    last exchange, turn count — never generated, so it can't hallucinate.
    """
    user_msgs = [m for m in record.messages if m.get("role") == "user"]
    assistant_msgs = [m for m in record.messages if m.get("role") == "assistant"]
    parts: list[str] = []
    if record.name:
        parts.append(f"a conversation called “{record.name}”")
    else:
        parts.append("a conversation")
    if user_msgs:
        parts.append(f"that began with the user saying “{_clip(user_msgs[0].get('content', ''), 200)}”")
    if user_msgs and assistant_msgs:
        parts.append(
            "and ended with the user saying "
            f"“{_clip(user_msgs[-1].get('content', ''), 200)}” "
            f"and you answering “{_clip(assistant_msgs[-1].get('content', ''), 200)}”"
        )
    turns = min(len(user_msgs), len(assistant_msgs))
    parts.append(f"({turns} exchange{'s' if turns != 1 else ''})")
    return " ".join(parts)
