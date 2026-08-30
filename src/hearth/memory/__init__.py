"""hearth.memory — the memory seam: recall · store · consolidate, a backend per companion.

Activation = config presence (config/memory.toml, [memory] enabled=true) — the
openclaw/serve gate shape: absent/disabled ⇒ ``maybe_attach`` returns None and
the engine is byte-identical. Enabled, the seam:

  * at session start, recalls ≤ N provenance-tagged items from the companion's
    backend and appends them to the composed system instruction (the persona
    render and PROMPT_FINGERPRINT are untouched — drift detection stays stable);
  * at graceful session end, writes the CANONICAL memory record (decider 7)
    and then lets the backend index it (``store``) and tidy (``consolidate``);
  * contains every backend failure (decider 6): recall degrades to the
    compaction floor, then to nothing; store/consolidate log and drop. Memory
    absent must mean "she doesn't recall", never "session down".

Backend selection is per companion: [memory].backend is the default,
[memory.companions] overrides it by name, "none" opts a companion out.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger

from .backend import MemoryItem, SessionRecord
from .floor import FloorBackend
from . import records as records_mod

__all__ = ["maybe_attach", "MemorySeam", "MemoryItem", "SessionRecord"]

_HEADER = (
    "## MEMORY — from earlier conversations\n"
    "You remember these things from your own earlier conversations with the user.\n"
    "Each line is dated where known; memories may be incomplete — it is always\n"
    "better to say you don't recall than to invent a memory:\n"
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _build_backend(backend_name: str, cfg: dict):
    """Construct the named backend. Unknown name ⇒ ConfigError (config tier is
    fail-fast, naming the file); a MISSING EXTRA for a known backend is caught
    later at the containment boundary (decider 6), not here — the engine must
    start even when the memory dependency is broken."""
    from hearth.config.config_loader import MEMORY_TOML, ConfigError

    if backend_name == "floor":
        return FloorBackend()
    if backend_name == "hindsight":
        from . import backend_hindsight  # lazy: extras-gated import cost

        hs_cfg = dict(cfg.get("hindsight") or {})
        if not hs_cfg.get("llm_model"):
            raise ConfigError(
                f"[memory.hindsight] needs llm_model (the local extraction model): {MEMORY_TOML}"
            )
        return backend_hindsight.HindsightBackend(hs_cfg)
    raise ConfigError(f"unknown memory backend {backend_name!r} in {MEMORY_TOML}")


class MemorySeam:
    """The engine-facing wrapper: containment + prompt framing + record writing."""

    def __init__(self, companion: str, persona: str, backend, cfg: dict) -> None:
        self.companion = companion
        self.persona = persona
        self.backend = backend
        self.recall_limit = int(cfg.get("recall_limit", 6))
        self.recall_query = str(
            cfg.get("recall_query", "the user's life, preferences, and recent conversations")
        )
        self._floor = backend if isinstance(backend, FloorBackend) else FloorBackend()

    # ── recall (session start) ───────────────────────────────────────────────

    def recall(self) -> list[MemoryItem]:
        """Contained recall: backend → floor → empty (decider 6)."""
        try:
            return self.backend.recall(self.companion, self.recall_query, self.recall_limit)
        except Exception as exc:  # noqa: BLE001 — memory must never break startup
            logger.warning(
                "[memory] {} recall failed ({}) — degrading to floor",
                self.backend.name, type(exc).__name__,
            )
        if self._floor is not self.backend:
            try:
                return self._floor.recall(self.companion, self.recall_query, self.recall_limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[memory] floor recall failed ({}) — no recall", type(exc).__name__)
        return []

    def augment(self, system_instruction: str) -> str:
        """system_instruction + a framed memory block; byte-identical when empty.

        Provenance framing per decider 1: every line carries its date (or the
        backend's own temporal phrasing inside the text)."""
        items = self.recall()
        if not items:
            return system_instruction
        lines = []
        for item in items:
            prefix = f"({item.when}) " if item.when else ""
            lines.append(f"- {prefix}{item.text}")
        block = _HEADER + "\n".join(lines)
        logger.info("[memory] recalled {} item(s) via {}", len(items), self.backend.name)
        return f"{system_instruction}\n\n{block}\n"

    # ── store + consolidate (session end) ────────────────────────────────────

    def on_session_end(self, messages, store=None) -> str:
        """Write the canonical record, then index + consolidate. Fully contained
        — returns a short status string for the shutdown log, never raises.
        MUST run before session_store.finalize (which deletes ephemeral files)."""
        try:
            record = self._make_record(messages, store)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] record build failed ({}) — nothing stored", type(exc).__name__)
            return "record build failed — nothing stored"
        if record is None:
            return ""  # empty session: no record, no status noise
        try:
            records_mod.write_record(record)
        except Exception as exc:  # noqa: BLE001 — the canonical write comes first;
            # if IT fails there is nothing safe to index either.
            logger.warning("[memory] canonical record write failed ({})", type(exc).__name__)
            return "canonical record write failed"
        status = f"record kept ({record.session_id})"
        try:
            self.backend.store(self.companion, record)
        except Exception as exc:  # noqa: BLE001 — log and drop (decider 6)
            logger.warning("[memory] {} store failed ({}) — record kept, index skipped",
                           self.backend.name, type(exc).__name__)
            status += " — backend index skipped"
        try:
            self.backend.consolidate(self.companion)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] {} consolidate failed ({})",
                           self.backend.name, type(exc).__name__)
        return status

    def _make_record(self, messages, store) -> Optional[SessionRecord]:
        """None for a session with no completed user turn (nothing to remember)."""
        persistable = [
            m for m in (messages or [])
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        ]
        if not any(m.get("role") == "user" for m in persistable):
            return None
        ended = _now_iso()
        session_id = getattr(store, "session_id", None) or f"unsaved-{ended[:19].replace(':', '')}"
        return SessionRecord(
            companion=self.companion,
            session_id=str(session_id),
            started=str(getattr(store, "started", "") or ""),
            ended=ended,
            name=str(getattr(store, "name", "") or ""),
            persona=self.persona,
            messages=persistable,
        )

    def close(self) -> None:
        """Contained resource release (stops an embedded server if one runs)."""
        try:
            self.backend.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] backend close failed ({})", type(exc).__name__)


def maybe_attach(companion: str, persona: str = "default") -> Optional[MemorySeam]:
    """The activation gate. None when config/memory.toml is absent, disabled,
    or maps this companion to "none" — engine byte-identical, nothing loaded.
    Malformed config ⇒ ConfigError naming the file (fail-fast, config tier)."""
    from hearth.config import config_loader

    cfg = config_loader.load_memory_config()
    if cfg is None:
        return None
    backend_name = str(dict(cfg.get("companions") or {}).get(companion, cfg.get("backend", "floor")))
    if backend_name == "none":
        return None
    backend = _build_backend(backend_name, cfg)
    logger.info("[memory] seam attached: companion={} backend={}", companion, backend_name)
    return MemorySeam(companion, persona, backend, cfg)
