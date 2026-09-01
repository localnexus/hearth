"""serve/memory_glue.py — session anchors for a door that never closes.

The memory seam is transport-agnostic: its contracts (recall at session start,
record + store + consolidate at session end) never mention a voice loop. What
the /v1 facade lacks is therefore not memory machinery but the seam's two
ANCHORS — a session start and a graceful close. The facade is stateless by
construction: it resolves identity once and re-composes [system] + client turns
on every request, so nothing in it knows when a conversation begins or ends.

This module rebuilds those anchors for a long-running service and reuses the
seam with ZERO API changes (signed design, the facade-lane memory seam):

  * a small in-process session table keyed (character, channel, session-hint);
  * one BACKEND per enabled companion, built lazily on that companion's first
    session and kept for the facade's life (a warm sidecar before the first
    call), with a FRESH ``MemorySeam`` per conversation over it — which is what
    re-reads the intent slot per conversation instead of once at boot;
  * the augmented instruction computed once at session open and cached on the
    entry, so recall costs one call per conversation and every later turn costs
    a dict lookup;
  * with [memory.per_turn] enabled, a CHAT request whose cue (the user's
    newest words) passes the seam's guards gets a PER-REQUEST instruction
    instead: the cached open block plus one targeted recall on the worker
    thread, deadline-guarded, identical cues served from a one-slot cache, any
    failure serving the cached open string (design lane (b), signed
    2026-09-01; the voice lane is untouched — its prefetch-behind variant is
    its own stroke);
  * turns accumulated FACADE-SIDE, verbatim: the last request's message list is
    not a faithful transcript, because a voice client windows its own history;
  * three close paths, so a record exists no matter how a conversation ends —
    deliberate closure (the primary chat close), an idle sweep, and facade
    shutdown — plus orphan finalization for checkpoints a crash left behind.

Threading, non-negotiable: every seam and backend call runs on ONE dedicated
worker thread. The seam contract is synchronous, some backends block for
seconds, and the hindsight adapter's own thread-hop refuses to run inside a
running event loop. The event-loop thread only ever mutates the session table.

Containment (decider 6): this is the health-load-bearing channel. Every step is
try/except-contained — recall failure ⇒ the base instruction, checkpoint
failure ⇒ logged, close failure ⇒ logged and the checkpoint survives for the
next start. Memory absent must mean "she doesn't recall", never "the
conversation dropped".
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import functools
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from hearth.config import config_loader
from hearth.memory import MemorySeam, _build_backend
from hearth.memory import intent as intent_mod
from hearth.memory import records as records_mod

CHANNELS = ("chat", "voice")   # whitelist — the value keys a session AND names a file
DEFAULT_CHANNEL = "chat"
SWEEP_INTERVAL_S = 60.0        # reaper cadence; the thresholds themselves are minutes
PER_TURN_DEADLINE_S = 5.0      # targeted-recall budget; overrun ⇒ the cached open instruction
CHECKPOINT_SCHEMA = 1
CHECKPOINT_KIND = "memory-checkpoint"

_HINT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# The closure pre-filter: the cheap turn-time test that decides whether the
# extraction seat is worth asking at all. A goodbye is short, is not a question,
# and is never the opening move of a conversation.
_CLOSURE_MAX_CHARS = 160


# ── keys, names, paths ───────────────────────────────────────────────────────

def normalize_channel(raw: Any) -> str:
    """The channel header, whitelisted (transcript.py's contract, same values)."""
    channel = str(raw or "").strip().lower()
    return channel if channel in CHANNELS else DEFAULT_CHANNEL


def sanitize_hint(raw: Any) -> str:
    """X-Hearth-Session, made safe for a filename and a session id.

    The header is client-supplied and lands in BOTH, so a value that is not
    plainly safe is replaced by a short digest of itself rather than rejected:
    the client keeps a stable subdivision of its channel, and no raw client
    bytes ever reach the filesystem. Empty/absent ⇒ a hintless key (the whole
    channel is one conversation).
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if _HINT_RE.match(text):
        return text
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def session_key(companion: str, channel: Any, hint: Any) -> tuple:
    """(character, channel, session-hint) — the conversation's identity."""
    return (str(companion), normalize_channel(channel), sanitize_hint(hint))


def _stem(channel: str, hint: str) -> str:
    """serve-<channel>[-<hint>] — the checkpoint's name and the id's prefix."""
    return f"serve-{channel}-{hint}" if hint else f"serve-{channel}"


def _checkpoint_root() -> Path:
    """DATA/characters — the orphan scan root (and the seam tests patch)."""
    return config_loader.CHARACTERS_DIR


def _checkpoint_dir(companion: str) -> Path:
    name = str(companion or "")
    if not _NAME_RE.match(name) or name.startswith("."):
        raise ValueError(f"invalid companion name: {companion!r}")
    return _checkpoint_root() / name / "memory" / "checkpoints"


def _iter_checkpoints() -> list:
    """Every lane checkpoint left on disk, across companions."""
    root = _checkpoint_root()
    if not root.is_dir():
        return []
    return sorted(root.glob("*/memory/checkpoints/serve-*.json"))


def _write_checkpoint(path: Path, payload: dict) -> None:
    """The records writer's contract, reused: 0600 in a 0700 tree, tmp+replace."""
    records_mod._ensure_dir(path.parent)
    records_mod._atomic_write_json(path, payload)


def _restamp_ended(companion: str, session_id: str, ended: str) -> None:
    """Correct an orphan's ``ended`` to when the facade DIED, not when it returned.

    ``on_session_end`` stamps ``ended`` with now — it cannot know better — which
    is right for every live close and wrong for a checkpoint finalized at the
    next start. The canonical record is therefore rewritten once, atomically,
    from the checkpoint's mtime. A backend keeps the boot-time stamp until its
    next rebuild: records are the truth, indexes are derived (decider 7).
    """
    path = Path(records_mod.records_dir(companion)) / f"{session_id}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("kind") != "memory-record":
        return
    data["ended"] = ended
    records_mod._atomic_write_json(path, data)


# ── the session entry + the close handle ─────────────────────────────────────

class _CloseHandle:
    """What ``MemorySeam._make_record`` reads off a session store: id, start, name.

    The facade has no SessionStore, so the glue supplies exactly the three
    attributes the seam asks for by ``getattr``. Duck-typed on purpose — this is
    why the facade lane needs no seam change at all.
    """

    __slots__ = ("session_id", "started", "name")

    def __init__(self, session_id: str, started: str, name: str) -> None:
        self.session_id = session_id
        self.started = started
        self.name = name


@dataclass
class _Session:
    """One open conversation. Mutated on the event-loop thread only."""

    companion: str
    persona: str
    channel: str
    hint: str
    seam: Any
    instruction: str          # the augmented system instruction, computed at open
    base_instruction: str     # what augment() received — the per-turn re-compose base
    started: str              # ISO, local — the record's `started`
    session_id: str           # serve-<channel>[-<hint>]-<startedYYYYMMDDTHHMMSS>
    stem: str                 # serve-<channel>[-<hint>] — the checkpoint's name
    touched: float            # clock() at the last exchange
    turns: list = field(default_factory=list)
    exchanges: int = 0
    seq: int = 0              # bumped per exchange; the closure check's staleness guard
    last_cue: str = ""        # per-turn recall: one-slot cache (same words, same answer)
    last_cue_instruction: str = ""


# ── the glue ─────────────────────────────────────────────────────────────────

class ServeMemory:
    """The facade's session manager: open → accumulate → close, all contained."""

    def __init__(self, mem_cfg: dict, *, clock=time.monotonic) -> None:
        self._cfg = dict(mem_cfg or {})
        serve = dict(self._cfg.get("serve") or {})
        self._idle_voice = float(serve.get("idle_close_voice", 5))
        self._idle_chat = float(serve.get("idle_close_chat", 480))
        self._checkpoints = bool(serve.get("checkpoint", True))
        self._intent_cfg = dict(self._cfg.get("intent") or {})
        # Injectable so the idle sweep is testable without waiting on wall time.
        self._clock = clock
        self._sessions: dict = {}
        self._opening: dict = {}     # key → future; one open per key, never two
        self._backends: dict = {}    # companion → backend (None = opted out)
        self._tasks: set = set()
        self._sweeper = None
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="serve-memory")

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Finalize what a previous run left open, then run the idle sweep.

        The orphan pass is scheduled rather than awaited: extraction can take
        seconds and the facade must bind immediately. It still runs FIRST on the
        worker thread, so the first session of the new run simply queues behind
        it.
        """
        self._spawn(self._finalize_orphans())
        self._sweeper = asyncio.ensure_future(self._sweep_loop())

    async def stop(self) -> None:
        """Facade shutdown: every open session becomes a record, then the
        backends close — exactly once each, here and nowhere else."""
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._sweeper
            self._sweeper = None
        await self.drain()
        for key in list(self._sessions):
            await self.close_session(key)
        for companion, backend in list(self._backends.items()):
            if backend is None:
                continue
            try:
                await self._run(backend.close)
            except Exception as exc:  # noqa: BLE001 — shutdown must complete
                logger.warning("[serve-memory] {} backend close failed ({})",
                               companion, type(exc).__name__)
        self._backends.clear()
        self._pool.shutdown(wait=True)

    async def drain(self) -> None:
        """Await every scheduled step (checkpoints, closure checks, orphans)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ── the worker thread ────────────────────────────────────────────────────

    async def _run(self, fn, *args):
        """The ONE seam/backend lane: a single worker thread, never the loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, functools.partial(fn, *args))

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(self._guard(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a background step is never fatal
            logger.warning("[serve-memory] background step failed ({})", type(exc).__name__)

    # ── session open (first request of a conversation) ───────────────────────

    async def instruction(self, companion: str, persona: str, channel: Any,
                          hint: Any, base_instruction: str, cue: str = "") -> str:
        """The system instruction this request should send.

        Opens the session on its first request (paying recall once) and returns
        the AUGMENTED instruction; every later turn of the same conversation
        gets the cached string. With [memory.per_turn] enabled, ``cue`` (the
        user's newest words) may upgrade that to a per-request instruction —
        see _turn_instruction. A companion mapped to "none", or any failure at
        all, returns ``base_instruction`` unchanged — the conversation proceeds
        without memory rather than not proceeding.
        """
        key = session_key(companion, channel, hint)
        session = self._sessions.get(key)
        if session is not None:
            session.touched = self._clock()
            return await self._turn_instruction(session, cue)
        pending = self._opening.get(key)
        if pending is not None:
            # A second request arrived while the first was still recalling.
            with contextlib.suppress(Exception):
                await pending
            session = self._sessions.get(key)
            if session is None:
                return base_instruction
            return await self._turn_instruction(session, cue)
        opened_future = asyncio.get_running_loop().create_future()
        self._opening[key] = opened_future
        try:
            session = await self._open(key, companion, persona, base_instruction)
        finally:
            self._opening.pop(key, None)
            if not opened_future.done():
                opened_future.set_result(True)
        if session is None:
            return base_instruction
        return await self._turn_instruction(session, cue)

    async def _turn_instruction(self, session: _Session, cue: str) -> str:
        """Per-turn targeted recall (design lane (b)) — or the cached string.

        Guards are decided here cheaply (gate, chat lane only, cue length,
        one-slot cue cache); the recall itself runs on the worker thread under
        a deadline. Every failure path serves the OPEN instruction: an extra
        must never cost the turn (decider 6)."""
        seam = session.seam
        if not getattr(seam, "per_turn_enabled", False):
            return session.instruction
        if session.channel != "chat":
            # Design lane (b) scope: chat only — a synchronous recall would
            # tax every voice turn (latency doctrine); the voice lane's
            # prefetch-behind variant is its own stroke.
            return session.instruction
        cue = " ".join(str(cue or "").split())
        if len(cue) < int(getattr(seam, "per_turn_min_chars", 12)):
            return session.instruction
        if cue == session.last_cue and session.last_cue_instruction:
            return session.last_cue_instruction
        try:
            result = await asyncio.wait_for(
                self._run(seam.augment_turn, session.base_instruction, cue),
                timeout=PER_TURN_DEADLINE_S)
        except Exception as exc:  # noqa: BLE001 — contained: the open string serves
            logger.warning("[serve-memory] turn recall failed ({}) — "
                           "open-time instruction", type(exc).__name__)
            return session.instruction
        session.last_cue, session.last_cue_instruction = cue, str(result)
        return session.last_cue_instruction

    async def _open(self, key: tuple, companion: str, persona: str,
                    base_instruction: str) -> Optional[_Session]:
        try:
            opened = await self._run(self._open_blocking, companion, persona, base_instruction)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve-memory] session open failed for {} ({}) — "
                           "this conversation runs without memory",
                           companion, type(exc).__name__)
            return None
        if opened is None:
            return None
        seam, instruction = opened
        now = datetime.now().astimezone()
        stem = _stem(key[1], key[2])
        session = _Session(
            companion=companion, persona=persona, channel=key[1], hint=key[2],
            seam=seam, instruction=instruction, base_instruction=base_instruction,
            started=now.isoformat(timespec="seconds"),
            session_id=f"{stem}-{now:%Y%m%dT%H%M%S}",
            stem=stem, touched=self._clock(),
        )
        self._sessions[key] = session
        logger.info("[serve-memory] session open: {} channel={} backend={}",
                    companion, session.channel, getattr(seam.backend, "name", "?"))
        return session

    def _open_blocking(self, companion: str, persona: str, base_instruction: str):
        """Worker thread: backend (built once per companion) + a fresh seam."""
        backend = self._backend_for(companion)
        if backend is None:
            return None
        seam = MemorySeam(companion, persona, backend, self._cfg)
        return seam, seam.augment(base_instruction)

    def _backend_for(self, companion: str):
        """One backend per companion, kept for the facade's life. "none" opts a
        companion out — cached as such, so the answer costs nothing after the
        first request."""
        if companion in self._backends:
            return self._backends[companion]
        name = str(dict(self._cfg.get("companions") or {}).get(
            companion, self._cfg.get("backend", "floor")))
        if name == "none":
            self._backends[companion] = None
            logger.info('[serve-memory] {} maps to backend "none" — no sessions', companion)
            return None
        backend = _build_backend(name, self._cfg)
        self._backends[companion] = backend
        logger.info("[serve-memory] backend up for {}: {}", companion, name)
        return backend

    # ── per exchange ─────────────────────────────────────────────────────────

    def note_exchange(self, companion: str, channel: Any, hint: Any,
                      user_text: str, reply_text: str) -> None:
        """One completed exchange, appended verbatim.

        Loop-thread only, and deliberately synchronous: the table mutation must
        be ordered against the closure check's staleness guard. The disk write
        and the extraction seat are scheduled, never awaited on the reply path.
        """
        session = self._sessions.get(session_key(companion, channel, hint))
        if session is None:
            return
        session.turns.append({"role": "user", "content": user_text})
        session.turns.append({"role": "assistant", "content": reply_text})
        session.exchanges += 1
        session.seq += 1
        session.touched = self._clock()
        if self._checkpoints:
            self._spawn(self._checkpoint(session))
        if self._closure_worth_asking(session, user_text):
            self._spawn(self._closure_check(
                session_key(companion, channel, hint), session, session.seq))

    async def _checkpoint(self, session: _Session) -> None:
        """A crash-recoverable snapshot after every exchange (KBs, off the reply
        path). The sweep/shutdown/closure paths finalize it and remove it; a
        facade start finalizes whatever a crash left."""
        payload = {
            "schema": CHECKPOINT_SCHEMA,
            "kind": CHECKPOINT_KIND,
            "companion": session.companion,
            "persona": session.persona,
            "channel": session.channel,
            "started": session.started,
            "session_id": session.session_id,
            "turns": list(session.turns),
        }
        path = _checkpoint_dir(session.companion) / f"{session.stem}.json"
        try:
            await self._run(_write_checkpoint, path, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve-memory] checkpoint write failed ({}) — "
                           "conversation unaffected", type(exc).__name__)

    def _closure_worth_asking(self, session: _Session, user_text: str) -> bool:
        """The cheap pre-filter in front of the extraction seat.

        Deliberate closure is the PRIMARY chat close (signed: the day ends when
        the user ends it), so the question is asked at turn time — but only when
        it could plausibly be a goodbye: a short line, not a question, and not
        the conversation's opening exchange. Voice closes on its transport clock
        instead, and with no local seat configured there is nothing to ask.
        """
        if session.channel != "chat" or session.exchanges < 2:
            return False
        if str(self._intent_cfg.get("llm_provider") or "").strip().lower() != "ollama":
            return False
        if not str(self._intent_cfg.get("llm_model") or "").strip():
            return False
        text = str(user_text or "")
        return len(text) <= _CLOSURE_MAX_CHARS and "?" not in text

    async def _closure_check(self, key: tuple, session: _Session, seq: int) -> None:
        """Ask the seat whether that was a goodbye; close if it was and the
        conversation has not moved on since we asked."""
        tail = list(session.turns)
        try:
            closure, _topic = await self._run(
                intent_mod.detect_closure_and_topic, tail, self._intent_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve-memory] closure check failed ({}) — session left open",
                           type(exc).__name__)
            return
        if not closure:
            return
        if self._sessions.get(key) is not session or session.seq != seq:
            return  # a newer exchange landed while we asked — the conversation continued
        logger.info("[serve-memory] deliberate closure — closing {} {}",
                    session.companion, session.channel)
        await self.close_session(key)

    # ── close ────────────────────────────────────────────────────────────────

    async def close_session(self, key: tuple, ended: str = "") -> None:
        """Record → store → consolidate → intent capture, on the worker thread.

        Popping first makes the close idempotent under concurrent triggers (a
        sweep and a closure check can race). A failure here is logged and
        swallowed: the checkpoint survives and the next facade start finalizes
        it.
        """
        session = self._sessions.pop(key, None)
        if session is None:
            return
        try:
            await self._run(self._close_blocking, session, ended)
        except Exception as exc:  # noqa: BLE001 — a close must never propagate
            logger.warning("[serve-memory] close failed ({}) — checkpoint left "
                           "for the next start", type(exc).__name__)

    def _close_blocking(self, session: _Session, ended: str = "") -> None:
        """Worker thread. The seam is dropped, never closed — the backend is
        shared across this companion's conversations and closes at shutdown."""
        handle = _CloseHandle(session.session_id, session.started,
                              f"facade {session.channel}")
        status = ""
        try:
            status = session.seam.on_session_end(session.turns, handle)
        except Exception as exc:  # noqa: BLE001 — on_session_end is contained, but belt+braces
            logger.warning("[serve-memory] on_session_end failed ({})", type(exc).__name__)
        if ended and status:
            try:
                _restamp_ended(session.companion, session.session_id, ended)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[serve-memory] record restamp failed ({}) — "
                               "record kept as written", type(exc).__name__)
        self._drop_checkpoint(session.companion, session.stem)
        if status:
            logger.info("[serve-memory] session closed: {} — {}", session.companion, status)

    def _drop_checkpoint(self, companion: str, stem: str) -> None:
        """The last step of a close. The checkpoint is a transient this module
        wrote to stand in for a record that now exists — removing it is the only
        deletion the glue performs."""
        try:
            os.remove(_checkpoint_dir(companion) / f"{stem}.json")
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve-memory] checkpoint removal failed ({})", type(exc).__name__)

    # ── the idle sweep ───────────────────────────────────────────────────────

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad sweep, not the sweeper
                logger.warning("[serve-memory] idle sweep failed ({}) — next sweep retries",
                               type(exc).__name__)

    async def sweep(self) -> None:
        """Close what has gone quiet: voice at ``idle_close_voice``, chat at
        ``idle_close_chat`` (the fallback behind deliberate closure)."""
        now = self._clock()
        for key, session in list(self._sessions.items()):
            limit = self._idle_voice if session.channel == "voice" else self._idle_chat
            if now - session.touched >= limit * 60.0:
                logger.info("[serve-memory] idle close ({} min) — {} {}",
                            limit, session.companion, session.channel)
                await self.close_session(key)

    # ── orphan finalization (a crash left checkpoints) ───────────────────────

    async def _finalize_orphans(self) -> None:
        try:
            finalized = await self._run(self._finalize_orphans_blocking)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve-memory] orphan scan failed ({})", type(exc).__name__)
            return
        if finalized:
            logger.info("[serve-memory] finalized {} orphaned conversation(s) from "
                        "a previous run", finalized)

    def _finalize_orphans_blocking(self) -> int:
        finalized = 0
        for path in _iter_checkpoints():
            try:
                if self._finalize_one(path):
                    finalized += 1
            except Exception as exc:  # noqa: BLE001 — one bad file, not the scan
                logger.warning("[serve-memory] orphan {} unusable ({}) — left in place",
                               path.name, type(exc).__name__)
        return finalized

    def _finalize_one(self, path: Path) -> bool:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("kind") != CHECKPOINT_KIND:
            return False
        companion = str(data.get("companion") or "")
        backend = self._backend_for(companion) if companion else None
        if backend is None:
            os.remove(path)  # opted out (or nameless): the transient goes, no record
            return False
        session = _Session(
            companion=companion,
            persona=str(data.get("persona") or "default"),
            channel=normalize_channel(data.get("channel")),
            hint="",
            seam=MemorySeam(companion, str(data.get("persona") or "default"),
                            backend, self._cfg),
            instruction="",
            base_instruction="",
            started=str(data.get("started") or ""),
            session_id=str(data.get("session_id") or path.stem),
            stem=path.stem,
            touched=0.0,
            turns=data.get("turns") if isinstance(data.get("turns"), list) else [],
        )
        ended = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
            timespec="seconds")
        self._close_blocking(session, ended)
        return True
