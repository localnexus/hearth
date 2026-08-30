"""backend_hindsight.py — adapter for Hindsight (vectorize-io) embedded mode.

The survey's shortlist #1 (run-verified 2026-08-29/30: typed temporal facts on a
local 30B, recall 0.27–0.36 s with zero LLM calls, strict bank isolation,
zero-egress with the env posture below, dependency tree vetted clean).

Costs, stated plainly (all from the run-verify):
  * pg0 = a real bundled PostgreSQL (~15 processes) on FIXED port 5432 with its
    data under ~/.pg0 — one instance per machine; a second live bot would share
    it (banks stay isolated) but cannot start its own.
  * embedded server start ≈ 5–14 s warm — paid once at session start, never
    per turn.
  * dependency closure ≈ 1.4 GB — which is exactly why it lives behind
    ``pip install hearth[memory-hindsight]`` and this module imports hindsight
    lazily; without the extra, selecting this backend fails soft at the seam
    (decider 6), not hard at engine import.

Egress posture (survey §5b): bot.py already airgaps HF (HF_HUB_OFFLINE=1
setdefault); this module setdefaults the second kill switch
(LITELLM_LOCAL_MODEL_COST_MAP) before hindsight/litellm import. First-ever run
needs the embed/rerank models fetched once: HF_HUB_OFFLINE=0 ./start.sh.
"""

from __future__ import annotations

import os

from loguru import logger

from .backend import MemoryItem, SessionRecord

# Kill switch #2 (survey §5b): litellm otherwise fetches its model-cost map
# from raw.githubusercontent.com at import. setdefault → operator can override.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

_MAX_RETAIN_CHARS_DEFAULT = 6000


def _render_transcript(record: SessionRecord, max_chars: int) -> str:
    """The retain payload: a plain speaker-labelled transcript, tail-capped.

    Hindsight's extraction works on prose; the tail cap bounds session-end
    latency (retain ran 1.3–6 s per item in the stand-up) at the cost of
    dropping the oldest turns of a very long session — the canonical record
    keeps them all, so a later rebuild with a higher cap loses nothing.
    """
    lines: list[str] = []
    for m in record.messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        speaker = "User" if role == "user" else "Assistant"
        content = " ".join(str(m.get("content", "")).split())
        if content:
            lines.append(f"{speaker}: {content}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
        cut = text.find("\n")  # drop the partial first line after the cut
        if 0 <= cut < len(text) - 1:
            text = text[cut + 1:]
    return text


class HindsightBackend:
    """retain/recall against an embedded HindsightServer, one bank per companion."""

    name = "hindsight"

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._server = None
        self._client = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ensure(self) -> None:
        """Start the embedded server lazily (first recall/store, not import)."""
        if self._client is not None:
            return
        for key, value in dict(self._cfg.get("env") or {}).items():
            os.environ.setdefault(str(key), str(value))
        from hindsight import HindsightClient, HindsightServer  # heavy: extras-gated

        server = HindsightServer(
            db_url=str(self._cfg.get("db_url", "pg0")),
            llm_provider=str(self._cfg.get("llm_provider", "ollama")),
            llm_api_key=str(self._cfg.get("llm_api_key", "")),
            llm_model=str(self._cfg["llm_model"]),
            log_level=str(self._cfg.get("log_level", "warning")),
        )
        server.start()
        self._server = server
        self._client = HindsightClient(base_url=server.url)
        logger.info("[memory] hindsight embedded server up at {}", server.url)

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.stop()
            except Exception as exc:  # noqa: BLE001 — shutdown must not raise
                logger.warning("[memory] hindsight stop failed ({})", type(exc).__name__)
        self._server = None
        self._client = None

    # ── the seam contract ────────────────────────────────────────────────────

    def recall(self, companion: str, query: str, limit: int) -> list[MemoryItem]:
        self._ensure()
        result = self._client.recall(bank_id=companion, query=query)
        raw = getattr(result, "results", result) or []
        items: list[MemoryItem] = []
        for entry in list(raw)[: max(0, int(limit))]:
            text = str(getattr(entry, "text", "") or "").strip()
            if not text:
                continue
            # Hindsight world facts carry their own temporal phrasing inside the
            # text ("… | When: on Sunday, August 30, 2026 | …"); ``when`` stays ""
            # and the framing relies on the text (see MemoryItem docstring).
            items.append(MemoryItem(text=text, source_session=f"hindsight/{companion}"))
        return items

    def store(self, companion: str, record: SessionRecord) -> None:
        transcript = _render_transcript(
            record, int(self._cfg.get("retain_max_chars", _MAX_RETAIN_CHARS_DEFAULT))
        )
        if not transcript:
            return
        self._ensure()
        self._client.retain(bank_id=companion, content=transcript)

    def consolidate(self, companion: str) -> None:  # noqa: ARG002
        """No-op this pass: retain already extracts; Hindsight's ``reflect`` is
        an LLM-driven deliberation better wired to a real idle trigger, which
        the engine doesn't have yet (see docs/memory.md)."""
