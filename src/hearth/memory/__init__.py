"""hearth.memory — the memory seam: recall · store · consolidate, a backend per companion.

Activation = config presence (config/memory.toml, [memory] enabled=true) — the
openclaw/serve gate shape: absent/disabled ⇒ ``maybe_attach`` returns None and
the engine is byte-identical. Enabled, the seam:

  * at session start, recalls ≤ N provenance-tagged items from the companion's
    backend and appends them to the composed system instruction (the persona
    render and PROMPT_FINGERPRINT are untouched — drift detection stays stable);
  * per turn where the host lane opts in ([memory.per_turn] — the chat facade;
    design lane (b), signed 2026-09-01), re-queries the backend with the user's
    own words and appends what surfaced under a labeled line — the open-time
    block is never recomputed, and a guard-tripped or failing turn recall
    serves the open composition unchanged;
  * at graceful session end, writes the CANONICAL memory record
    and then lets the backend index it (``store``) and tidy (``consolidate``);
  * optionally (off by default, [memory.intent]) asks the extraction model at
    close whether the user STATED what to pick up next session, and injects
    that intent — dated — into the next boot's memory block (intent.py);
  * contains every backend failure: recall degrades to the
    compaction floor, then to nothing; store/consolidate log and drop. Memory
    absent must mean "the companion doesn't recall", never "session down".

Backend selection is per companion: [memory].backend is the default,
[memory.companions] overrides it by name, "none" opts a companion out.

Per-session memory mode (``maybe_attach(mode=...)``): recall-in and record-out
are independent operations, so one sitting can choose
  * "full" (default)        — everything above, unchanged;
  * "recall-only"           — recall runs as normal (open-time, per-turn, the
    injected intent line) but the seam RETAINS nothing: on_session_end writes
    no record, indexes nothing, captures no intent, and the intent slot it
    injected is preserved for the next retaining session instead of consumed;
  * "off"                   — no seam at all (None), same as unenrolled.
The mode governs the memory BANK only — transcript persistence is the session
store's own, separate decision.
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

# Framing for per-turn targeted extras ([memory.per_turn]): they ride the same
# MEMORY block, under their own label, so provenance stays legible (design
# lane (b), decision 4 — labeled, never silently merged).
_TURN_HEADER = "Also surfaced by what the user just said (may bear on this turn):"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _build_backend(backend_name: str, cfg: dict):
    """Construct the named backend. Unknown name ⇒ ConfigError (config tier is
    fail-fast, naming the file); a MISSING EXTRA for a known backend is caught
    later at the containment boundary, not here — the engine must
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

    def __init__(self, companion: str, persona: str, backend, cfg: dict,
                 retain: bool = True) -> None:
        self.companion = companion
        self.persona = persona
        self.backend = backend
        # Per-session mode, recall-only ⇒ False: recall stays live, but
        # on_session_end retains nothing and the intent slot is peeked, never
        # consumed. Default True keeps every existing construction unchanged.
        self.retain = bool(retain)
        self.recall_limit = int(cfg.get("recall_limit", 6))
        self.recall_query = str(
            cfg.get("recall_query", "the user's life, preferences, and recent conversations")
        )
        self._floor = backend if isinstance(backend, FloorBackend) else FloorBackend()
        per_turn = dict(cfg.get("per_turn") or {})
        self.per_turn_enabled = bool(per_turn.get("enabled", False))
        self.per_turn_limit = int(per_turn.get("limit", 3))
        self.per_turn_min_chars = int(per_turn.get("min_cue_chars", 12))
        # Voice lane opt-in (prefetch-behind, own gate): the chat gate does
        # not light the voice loop; both must be on. Ships OFF.
        self.per_turn_voice = bool(per_turn.get("voice", False))
        # augment() fills these; augment_turn() re-frames them per request
        # without re-recalling the open set or re-touching the intent slot.
        self._session_lines: list[str] = []
        self._session_texts: set[str] = set()
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
        # Status-tap state (the panel's read-only memory line): attribution of
        # the last recalls — counts + the backend that ACTUALLY answered (a
        # floor fallback must never masquerade as the primary). Set by
        # recall() / augment_turn(); names, counts, timestamps only.
        self._open_recall: Optional[dict] = None
        self._turn_recall: Optional[dict] = None

    # ── recall (session start) ───────────────────────────────────────────────

    def recall(self) -> list[MemoryItem]:
        """Contained recall: backend → floor → empty. Each rung records its
        attribution for the status tap — count + the source that answered."""
        query = self._recall_query()
        try:
            items = self.backend.recall(self.companion, query, self.recall_limit)
            self._open_recall = {"count": len(items), "source": self.backend.name,
                                 "at": _now_iso()}
            return items
        except Exception as exc:  # noqa: BLE001 — memory must never break startup
            logger.warning(
                "[memory] {} recall failed ({}) — degrading to floor",
                self.backend.name, type(exc).__name__,
            )
        if self._floor is not self.backend:
            try:
                items = self._floor.recall(self.companion, query, self.recall_limit)
                self._open_recall = {"count": len(items), "source": self._floor.name,
                                     "at": _now_iso()}
                return items
            except Exception as exc:  # noqa: BLE001
                logger.warning("[memory] floor recall failed ({}) — no recall", type(exc).__name__)
        self._open_recall = {"count": 0, "source": "", "at": _now_iso()}
        return []

    def augment(self, system_instruction: str) -> str:
        """system_instruction + a framed memory block; byte-identical when empty.

        Provenance framing: every line carries its date (or the
        backend's own temporal phrasing inside the text). A captured intent
        rides the same block as a dated last line — the companion opens aware of the
        plan, not merely better-briefed about it — and is consumed here,
        because "used" means injected, not merely read. The composed lines are
        cached for augment_turn() — the per-turn path re-frames them, never
        re-recalls the open set."""
        items = self.recall()
        intent_line = self._consume_intent_line()
        lines = []
        for item in items:
            prefix = f"({item.when}) " if item.when else ""
            lines.append(f"- {prefix}{item.text}")
        if intent_line:
            lines.append(intent_line)
        self._session_lines = lines
        self._session_texts = {item.text for item in items}
        if items:  # an intent-only block logs its own line, in _consume_intent_line
            logger.info("[memory] recalled {} item(s) via {}", len(items), self.backend.name)
        return self._compose(system_instruction, [])

    def _compose(self, system_instruction: str, extras: list[MemoryItem]) -> str:
        """base + the framed block: the cached open lines, then targeted extras
        under their own label. Nothing at all ⇒ byte-identical passthrough."""
        lines = list(self._session_lines)
        if extras:
            lines.append(_TURN_HEADER)
            for item in extras:
                prefix = f"({item.when}) " if item.when else ""
                lines.append(f"- {prefix}{item.text}")
        if not lines:
            return system_instruction
        block = _HEADER + "\n".join(lines)
        return f"{system_instruction}\n\n{block}\n"

    # ── per-turn targeted recall (design lane (b), signed 2026-09-01) ────────

    def recall_turn(self, cue: str) -> tuple[list[MemoryItem], str]:
        """Contained targeted recall — the user's own words as the query.

        Same containment ladder as recall() (backend → floor → empty); the
        floor ignores queries by design, so its answer simply dedupes away
        against the open-time lines. Asks for headroom above the per-turn cap
        because dedupe happens seam-side, in augment_turn().

        Returns (items, source-backend-name) so the caller's log names the
        backend that actually answered — a floor fallback must never
        masquerade as the primary (run-observed 2026-09-02: a broken primary
        looked healthy for a day behind the mislabeled success line)."""
        want = self.per_turn_limit + self.recall_limit
        try:
            return self.backend.recall(self.companion, cue, want), self.backend.name
        except Exception as exc:  # noqa: BLE001 — an extra must never cost the turn
            has_floor = self._floor is not self.backend
            logger.warning("[memory] {} turn recall failed ({}) — {}",
                           self.backend.name, type(exc).__name__,
                           "trying the floor" if has_floor else "no extras")
        if self._floor is not self.backend:
            try:
                return self._floor.recall(self.companion, cue, want), self._floor.name
            except Exception as exc:  # noqa: BLE001
                logger.warning("[memory] floor turn recall failed ({}) — no extras",
                               type(exc).__name__)
        return [], ""

    def augment_turn(self, system_instruction: str, cue: str) -> str:
        """The per-request instruction: the open-time block + targeted extras.

        Every guard falls back to the open composition, byte-identical to what
        augment() returned: gate off, cue below min_cue_chars, nothing new
        surfaced. The intent slot is untouched here — consumed once, at
        augment(); its cached line rides every composition."""
        cue = " ".join(str(cue or "").split())
        extras: list[MemoryItem] = []
        if (self.per_turn_enabled and self.per_turn_limit > 0
                and len(cue) >= self.per_turn_min_chars):
            items, source = self.recall_turn(cue)
            seen = set(self._session_texts)
            for item in items:
                if not item.text or item.text in seen:
                    continue
                seen.add(item.text)
                extras.append(item)
                if len(extras) >= self.per_turn_limit:
                    break
            if extras:
                logger.info("[memory] turn recall surfaced {} extra(s) via {}",
                            len(extras), source)
            # Status tap: every ACTUAL recall is recorded, zero-extra ones
            # included (honest); guard-skipped turns leave the last one standing.
            self._turn_recall = {"extras": len(extras), "source": source,
                                 "at": _now_iso()}
        return self._compose(system_instruction, extras)

    def status(self) -> dict:
        """JSON-safe seam status for the panel's read-only memory line
        (:65000 DISPLAYS memory state; every memory write lives behind the
        :65001 facade — the write-layer rule, signed (c) 2026-09-02).

        Names, counts, gate booleans, timestamps — never message content,
        never the cue text (SessionMeta discipline). Pure attribute reads:
        no backend call, safe on the event loop."""
        return {
            "companion": self.companion,
            "backend": self.backend.name,
            "retain": self.retain,
            "recall_limit": self.recall_limit,
            "per_turn": {
                "chat": self.per_turn_enabled,
                # Effective, not raw: the voice lane needs BOTH gates (bot.py
                # only builds the prefetch processor when chat is on too).
                "voice": self.per_turn_enabled and self.per_turn_voice,
                "limit": self.per_turn_limit,
            },
            "open_recall": self._open_recall,
            "turn_recall": self._turn_recall,
        }

    # ── the intent slot (boot side; capture side lives in on_session_end) ─────

    def _read_intent(self) -> Optional[dict]:
        """Contained slot read — a hint must never cost a boot."""
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
        """The dated intent line, and the slot's end: one boot, one use.

        "One use" means one RETAINING boot: a recall-only session still opens
        aware of the plan (the line is injected) but must not destroy it — the
        slot is peeked, not popped, and survives for the next full session."""
        if not self._intent:
            return ""
        slot, self._intent = self._intent, None
        try:
            when = str(slot.get("stated_at", ""))[:10]
            dated = f"On {when} " if when else ""
            line = f"- {dated}you agreed to pick up {slot['text']} next time."
            if self.retain:
                intent_mod.clear_slot(slot["path"])
                logger.info("[memory] intent slot consumed (stated {})", when or "unknown")
            else:
                logger.info("[memory] intent slot injected, preserved (stated {}) — "
                            "recall-only session", when or "unknown")
            return line
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] intent injection failed ({}) — skipped",
                           type(exc).__name__)
            return ""

    # ── store + consolidate (session end) ────────────────────────────────────

    def on_session_end(self, messages, store=None) -> str:
        """Write the canonical record, then index + consolidate. Fully contained
        — returns a short status string for the shutdown log, never raises.
        MUST run before session_store.finalize (which deletes ephemeral files).

        A recall-only session (retain=False) suppresses ALL of it — record,
        index, consolidate, intent capture — and says so in the status, so the
        shutdown log can never be misread as a memory failure."""
        if not self.retain:
            logger.info("[memory] recall-only session — nothing retained")
            return "recall-only session — nothing retained"
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
        except Exception as exc:  # noqa: BLE001 — log and drop
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


def maybe_attach(companion: str, persona: str = "default",
                 mode: str = "full") -> Optional[MemorySeam]:
    """The activation gate. None when config/memory.toml is absent, disabled,
    or maps this companion to "none" — engine byte-identical, nothing loaded.
    Malformed config ⇒ ConfigError naming the file (fail-fast, config tier).

    ``mode`` is the per-session memory mode (module docstring): "full"
    (default), "recall-only" (attach with retain=False), "off" (None even for
    an enrolled companion — this sitting runs without the seam)."""
    if mode not in ("full", "recall-only", "off"):
        raise ValueError(f"unknown memory mode {mode!r} (full | recall-only | off)")
    from hearth.config import config_loader

    cfg = config_loader.load_memory_config()
    if cfg is None:
        return None
    if mode == "off":
        logger.info("[memory] session memory OFF — seam not attached for {}", companion)
        return None
    backend_name = str(dict(cfg.get("companions") or {}).get(companion, cfg.get("backend", "floor")))
    if backend_name == "none":
        return None
    backend = _build_backend(backend_name, cfg)
    logger.info("[memory] seam attached: companion={} backend={}{}",
                companion, backend_name,
                " mode=recall-only (nothing will be retained)" if mode == "recall-only" else "")
    return MemorySeam(companion, persona, backend, cfg, retain=(mode != "recall-only"))
