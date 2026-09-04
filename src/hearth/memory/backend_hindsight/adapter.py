"""backend_hindsight/adapter.py — the seam contract, spoken to one hindsight
server.

recall · store · forget · clear · consolidate, one bank per companion. What is
NOT here: how a server comes to exist (sidecar.py) and how a record becomes
prose (payload.py). What IS here, and cannot move, is the client — because of
the async caveat below — and the policy for a server that has died.

Async caveat baked in: the client SDK's sync methods raise inside a running
event loop (they call run_until_complete), AND the SDK caches one aiohttp
ClientSession bound to the event loop of the first call. The seam invokes this
adapter from the bot's async context, so every client call goes through
``self._call``, which hops to ONE persistent worker thread owned by the backend
— same thread, same loop, for the client's whole lifetime — and joins.
(Short-lived per-call threads leave the cached session on a dead loop:
RuntimeError on the second call. Run-observed 2026-08-30, the first in-bot
store.) Semantics stay synchronous, as the seam contract requires.

Respawn policy lives here rather than in sidecar.py because it is a memory-lane
judgement, not a process one: a child that died mid-session is respawned ONCE,
and a second immediate death is handed up to the seam's containment layer,
which degrades to the floor rather than dropping the session.
"""

from __future__ import annotations

import asyncio
import concurrent.futures

from loguru import logger

from ..backend import MemoryItem, SessionRecord
from .payload import _MAX_RETAIN_CHARS_DEFAULT, _ended_at, _render_transcript
from .sidecar import Sidecar

_RECENT_BOOST_DEFAULT = 3  # newest facts appended past semantic rank (0 = off)
_FACT_COUNT_LIMIT = 1000   # fact_count's one-page bound (a gauge, not a census)


class HindsightBackend:
    """retain/recall against a Hindsight server, one bank per companion."""

    name = "hindsight"

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._sidecar = Sidecar(cfg)
        self._client = None
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None

    def _call(self, fn, /, *args, **kwargs):
        """Run every sync SDK method on ONE persistent worker thread.

        The SDK caches an aiohttp ClientSession bound to the event loop of the
        first call, so every call must share that thread/loop pair — from ANY
        calling context: the bot's event loop, an asyncio.to_thread worker (the
        voice prefetch lane), an executor thread, or a plain CLI script. A
        short-lived per-call thread leaves the cached session on a dead loop
        (RuntimeError on the second call — run-observed 2026-08-30), and
        dispatching on the CALLER's context leaks the same mismatch the moment
        two contexts mix (RuntimeError on every voice-prefetch recall —
        run-observed 2026-09-02). So there is no direct path: the pool is the
        only lane, in every context.
        """
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="hindsight-io"
            )
        return self._pool.submit(fn, *args, **kwargs).result()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ensure(self) -> None:
        """Bring the server up lazily (first recall/store, not import) and keep
        it up: a sidecar that died mid-session is respawned ONCE per call.

        The cap is deliberate. One respawn covers the real failure (the child
        process is gone, the store at session close would otherwise die on
        ClientConnectorError — run-observed 2026-08-30); a second immediate
        death means the sidecar cannot run at all, and looping on it would turn
        a degraded lane into a stalled session. That error belongs to the
        caller, where the seam's containment layer already handles it.
        """
        if self._client is None:
            self._connect()
            return
        rc = self._sidecar.exited_rc()
        if rc is None:
            return
        logger.warning("[memory] hindsight sidecar died (rc={}) — respawning", rc)
        self._discard_dead_sidecar()
        self._connect()
        rc = self._sidecar.exited_rc()
        if rc is not None:  # died again on the spot — do not loop, hand it up
            raise RuntimeError(f"hindsight sidecar died again immediately (rc={rc})")

    def _discard_dead_sidecar(self) -> None:
        """Drop the client bound to the corpse, keep the thread pool.

        The pool is NOT shut down on purpose: the SDK caches its aiohttp session
        on the loop of that one worker thread, so the replacement client has to
        be created and used on the same thread/loop pair (see ``_call``).
        """
        if self._client is not None:
            try:
                self._call(self._client.close)
            except Exception as exc:  # noqa: BLE001 — a corpse's client is expected to fail
                logger.debug(
                    "[memory] stale hindsight client close failed ({})", type(exc).__name__
                )
        self._sidecar.discard()
        self._client = None

    def _connect(self) -> None:
        """Start the server for this mode, then bind a client to its URL."""
        self._sidecar.start()
        self._client = self._new_client()

    def _new_client(self):
        """The single import seam for the light SDK — one place to patch in
        tests, which have no hindsight-client installed (and must not need it)."""
        from hindsight_client import Hindsight  # light SDK (hearth[memory-hindsight])

        return Hindsight(base_url=self._sidecar.url)

    def close(self) -> None:
        if self._client is not None:
            try:
                # On the same persistent thread: no running loop there, so the
                # SDK's sync close path runs and the cached aiohttp session
                # closes cleanly (retires the "Unclosed client session" noise).
                self._call(self._client.close)
            except Exception as exc:  # noqa: BLE001 — shutdown must not raise
                logger.warning("[memory] hindsight client close failed ({})", type(exc).__name__)
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        self._sidecar.stop()
        self._client = None

    # ── the seam contract ────────────────────────────────────────────────────

    def recall(self, companion: str, query: str, limit: int) -> list[MemoryItem]:
        self._ensure()
        result = self._call(self._client.recall, bank_id=companion, query=query)
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
        return items + self._recent_boost(companion, {i.text for i in items})

    def _recent_boost(self, companion: str, seen: set[str]) -> list[MemoryItem]:
        """The last-session slot (finding 2026-09-01): recall is a single
        top-K semantic query at session open, so a fact retained minutes ago
        can rank far below the cut and never reach the companion. Append the
        N newest valid facts (``list_memories`` is newest-first) the semantic
        pass didn't already surface. Contained: a failed boost costs nothing
        but itself."""
        n = int(self._cfg.get("recent_boost", _RECENT_BOOST_DEFAULT))
        if n <= 0:
            return []
        out: list[MemoryItem] = []
        try:
            result = self._call(
                self._client.list_memories, bank_id=companion, limit=max(n * 3, n + 2)
            )
            for entry in list(getattr(result, "items", None) or []):
                m = dict(entry)
                text = str(m.get("text") or "").strip()
                if not text or text in seen or str(m.get("state") or "valid") != "valid":
                    continue
                out.append(MemoryItem(
                    text=text,
                    when=str(m.get("date") or "")[:10],
                    source_session=f"hindsight/{companion}/recent",
                ))
                seen.add(text)
                if len(out) >= n:
                    break
        except Exception as exc:  # noqa: BLE001 — the boost must never cost the recall
            logger.warning(
                "[memory] recent-boost failed ({}) — semantic recall only",
                type(exc).__name__,
            )
        return out

    def fact_count(self, companion: str) -> dict:
        """Valid-fact gauge for the bank: {"facts": n, "capped": bool}.

        An optional capability (the curation pane consumes it via getattr —
        backends without a separate index simply lack the method). Bounded to
        one ``list_memories`` page: past _FACT_COUNT_LIMIT the count answers
        capped=True instead of paging — this is a curation gauge, not a
        census, and each page is a real backend round-trip."""
        self._ensure()
        result = self._call(
            self._client.list_memories, bank_id=companion, limit=_FACT_COUNT_LIMIT
        )
        items = list(getattr(result, "items", None) or [])
        valid = sum(1 for e in items
                    if str(dict(e).get("state") or "valid") == "valid")
        return {"facts": valid, "capped": len(items) >= _FACT_COUNT_LIMIT}

    def store(self, companion: str, record: SessionRecord) -> None:
        transcript = _render_transcript(
            record, int(self._cfg.get("retain_max_chars", _MAX_RETAIN_CHARS_DEFAULT))
        )
        if not transcript:
            return
        self._ensure()
        # Keyed store (record-level curation, D1): document_id = the session,
        # so (a) re-retaining a resumed session REPLACES its document instead
        # of re-extracting every fact additively — each graceful stop stores
        # the whole transcript, and save-by-default made resume the normal
        # lifecycle — and (b) ``forget`` can cascade-delete exactly one
        # session's facts. ``timestamp`` anchors extraction to when the
        # session actually ended, which keeps a rebuild's replayed history
        # correctly dated instead of stamped with the replay day.
        self._call(
            self._client.retain,
            bank_id=companion,
            content=transcript,
            document_id=record.session_id,
            update_mode="replace",
            timestamp=_ended_at(record),
        )

    def forget(self, companion: str, session_id: str) -> bool:
        """Cascade-delete the facts extracted from one session.

        The keyed store makes each session a document in the bank; the server
        cascade-deletes the document, its memory units and their links.
        False = no such document — facts stored before keyed retain (or a
        session that stored nothing); excising those takes a clean rebuild
        (``python -m hearth.memory rebuild --clean``)."""
        self._ensure()
        try:
            self._call(self._delete_document_sync, companion, session_id)
            return True
        except Exception as exc:  # noqa: BLE001 — only not-found is a non-error
            if type(exc).__name__ == "NotFoundException" or getattr(exc, "status", None) == 404:
                return False
            raise

    def _delete_document_sync(self, companion: str, session_id: str) -> None:
        """The one low-level (async-only) SDK call this adapter needs: the
        wrapper has no sync delete_document, so bridge exactly the way its own
        sync verbs do — run_until_complete on the calling thread's loop. Via
        ``_call`` that thread is the backend's one worker, whose loop already
        owns the SDK's cached aiohttp session (see ``_call``)."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(
            self._client.documents.delete_document(companion, session_id)
        )

    def clear(self, companion: str) -> None:
        """Wipe the companion's whole bank — documents, facts, links, the lot.

        The rebuild --clean primitive. ``delete_bank`` rather than the
        memories-only clear: this adapter never customizes bank profile or
        mission (nothing replay cannot recreate), and delete_bank also drops
        the DOCUMENTS a keyed re-replay would otherwise collide with. The
        next retain auto-recreates the bank."""
        self._ensure()
        self._call(self._client.delete_bank, bank_id=companion)

    def consolidate(self, companion: str) -> None:  # noqa: ARG002
        """No-op this pass: retain already extracts; Hindsight's ``reflect`` is
        an LLM-driven deliberation better wired to a real idle trigger, which
        the engine doesn't have yet (see docs/memory.md)."""
