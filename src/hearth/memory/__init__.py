"""hearth.memory — the memory seam: recall · store · consolidate, a backend per companion.

Activation = config presence (config/memory.toml, [memory] enabled=true) — the
openclaw/serve gate shape: absent/disabled ⇒ ``maybe_attach`` returns None and
the engine is byte-identical. Enabled, the seam:

  * at session start, recalls ≤ N provenance-tagged items from the companion's
    backend and appends them to the composed system instruction (the persona
    render and PROMPT_FINGERPRINT are untouched — drift detection stays stable);
  * at graceful session end, writes the CANONICAL memory record (decider 7)
    and then lets the backend index it (``store``) and tidy (``consolidate``);
  * optionally (off by default, [memory.intent]) asks the extraction model at
    close whether the user STATED what to pick up next session, and injects
    that intent — dated — into the next boot's memory block (intent.py);
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
from . import intent as intent_mod
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
        intent_cfg = dict(cfg.get("intent") or {})
        self._intent_cfg = intent_cfg
        self.intent_enabled = bool(
            dict(intent_cfg.get("companions") or {}).get(
                companion, intent_cfg.get("enabled", False)
            )
        )
        # Read at attach so recall() can steer on it; consumed in augment().
        # Disabled ⇒ an existing slot is IGNORED, not deleted (re-enabling
        # must not have silently thrown the plan away).
        self._intent = self._read_intent() if self.intent_enabled else None

    # ── recall (session start) ───────────────────────────────────────────────

    def recall(self) -> list[MemoryItem]:
        """Contained recall: backend → floor → empty (decider 6)."""
        query = self._recall_query()
        try:
            return self.backend.recall(self.companion, query, self.recall_limit)
        except Exception as exc:  # noqa: BLE001 — memory must never break startup
            logger.warning(
                "[memory] {} recall failed ({}) — degrading to floor",
                self.backend.name, type(exc).__name__,
            )
        if self._floor is not self.backend:
            try:
                return self._floor.recall(self.companion, query, self.recall_limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[memory] floor recall failed ({}) — no recall", type(exc).__name__)
        return []

    def augment(self, system_instruction: str) -> str:
        """system_instruction + a framed memory block; byte-identical when empty.

        Provenance framing per decider 1: every line carries its date (or the
        backend's own temporal phrasing inside the text). A captured intent
        rides the same block as a dated last line — she opens aware of the
        plan, not merely better-briefed about it — and is consumed here,
        because "used" means injected, not merely read."""
        items = self.recall()
        intent_line = self._consume_intent_line()
        if not items and not intent_line:
            return system_instruction
        lines = []
        for item in items:
            prefix = f"({item.when}) " if item.when else ""
            lines.append(f"- {prefix}{item.text}")
        if intent_line:
            lines.append(intent_line)
        block = _HEADER + "\n".join(lines)
        if items:  # an intent-only block logs its own line, in _consume_intent_line
            logger.info("[memory] recalled {} item(s) via {}", len(items), self.backend.name)
        return f"{system_instruction}\n\n{block}\n"

    # ── the intent slot (boot side; capture side lives in on_session_end) ─────

    def _read_intent(self) -> Optional[dict]:
        """Contained slot read — a hint must never cost a boot (decider 6)."""
        try:
            return intent_mod.load_slot(
                self.companion, int(self._intent_cfg.get("expiry_days", 14))
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] intent slot unusable ({}) — ignored", type(exc).__name__)
            return None

    def _recall_query(self) -> str:
        """The standing query, steered by a pending intent: the bank surfaces
        material ABOUT the stated topic, not just recent/general facts. (The
        floor ignores the query — the intent LINE is what reaches it.)"""
        if self._intent:
            return f"{self.recall_query}; specifically: {self._intent['text']}"
        return self.recall_query

    def _consume_intent_line(self) -> str:
        """The dated intent line, and the slot's end: one boot, one use."""
        if not self._intent:
            return ""
        slot, self._intent = self._intent, None
        try:
            when = str(slot.get("stated_at", ""))[:10]
            dated = f"On {when} " if when else ""
            line = f"- {dated}you agreed to pick up {slot['text']} next time."
            intent_mod.clear_slot(slot["path"])
            logger.info("[memory] intent slot consumed (stated {})", when or "unknown")
            return line
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] intent injection failed ({}) — skipped",
                           type(exc).__name__)
            return ""

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
        self._capture_intent(record)
        return status

    def _capture_intent(self, record: SessionRecord) -> None:
        """The one extra question in the extraction lane, fully contained.

        Runs LAST on purpose: the canonical record is sacred and already on
        disk, so a slow, absent, or broken extraction model costs this hint and
        nothing else. Off unless [memory.intent] enables it for this companion.
        """
        if not self.intent_enabled:
            return
        try:
            intent_mod.capture(self.companion, record.messages,
                               record.session_id, self._intent_cfg)
        except Exception as exc:  # noqa: BLE001 — close must never fail on a hint
            logger.warning("[memory] intent capture failed ({}) — close unaffected",
                           type(exc).__name__)

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
