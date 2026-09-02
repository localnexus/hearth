"""backend_hindsight.py — adapter for Hindsight (vectorize-io), sidecar-first.

The survey's shortlist #1 (run-verified 2026-08-29/30: typed temporal facts on a
local 30B, recall 0.27–0.36 s with zero LLM calls, strict bank isolation,
zero-egress with the env posture below, dependency tree vetted clean).

Topology — WHY a sidecar (learned the hard way, 2026-08-30): hindsight's server
closure requires protobuf>=7 while pipecat (the voice pipeline) pins
protobuf<7, so the server can NEVER share the engine venv. The split that
works:

  engine venv   : this adapter + ``hindsight-client`` (featherweight SDK —
                  aiohttp/pydantic, no protobuf) via ``hearth[memory-hindsight]``
  sidecar venv  : ``hindsight-all`` (the 1.4 GB closure), owned by the operator
                  (see docs/memory.md), executed through sidecar_runner.py

``mode = "sidecar"`` (default) spawns the runner with the configured sidecar
python and talks HTTP over loopback; ``mode = "embedded"`` keeps the old
in-process import for non-pipecat hosts (CLI rebuilds, tests, future spines).

Costs, stated plainly (run-verified): pg0 = a real bundled PostgreSQL
(~15 processes) on FIXED port 5432, data under ~/.pg0 — one instance per
machine; server start ≈ 5–14 s warm, paid once at session start; extraction at
session close runs seconds on a 30B-class local model (retain_max_chars bounds
it). Egress: the engine already sets HF_HUB_OFFLINE=1; this module and the
runner set LITELLM_LOCAL_MODEL_COST_MAP. First-ever run must fetch the
embed/rerank models once: HF_HUB_OFFLINE=0.

Async caveat baked in: the client SDK's sync methods raise inside a running
event loop (they call run_until_complete), AND the SDK caches one aiohttp
ClientSession bound to the event loop of the first call. The seam invokes
this adapter from the bot's async context, so every client call goes through
``self._call``, which hops to ONE persistent worker thread owned by the
backend — same thread, same loop, for the client's whole lifetime — and
joins. (Short-lived per-call threads leave the cached session on a dead
loop: RuntimeError on the second call. Run-observed 2026-08-30, the first
in-bot store.) Semantics stay synchronous, as the seam contract requires.

Sidecar survivability (incident 2026-08-30): the child is a separate process
that can die mid-session, and it used to die INVISIBLY — its stderr went to
DEVNULL and its stdout was read only until the handshake line, so a full pipe
buffer could stall the server outright. Both holes are closed here: the child's
stderr is appended to a logfile and a daemon thread drains its stdout into the
same file for the process's whole life, and ``_ensure`` notices a dead child
and respawns it ONCE (a second immediate death is the caller's — the seam's
containment layer degrades to the floor rather than dropping the session).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from loguru import logger

from .backend import MemoryItem, SessionRecord

# Egress kill switch #2 — for embedded mode / hand-run CLIs.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

_MAX_RETAIN_CHARS_DEFAULT = 6000
_RECENT_BOOST_DEFAULT = 3  # newest facts appended past semantic rank (0 = off)
_SIDECAR_START_TIMEOUT_S = 180.0  # cold pg0 init + model load can be slow once

# Same permission discipline as records.py: the sidecar log carries startup
# noise and extraction chatter about the operator's conversations.
_LOG_DIR_MODE = 0o700
_LOG_FILE_MODE = 0o600
_LOG_ROTATE_BYTES = 5 * 1024 * 1024  # one generation kept (<name>.1); no scheduler needed
_DEFAULT_LOG_REL = ("logs", "hindsight-sidecar.log")


def _ended_at(record: SessionRecord) -> datetime | None:
    """``record.ended`` as a datetime (retain's fact-dating anchor); None —
    retain falls back to its own clock — when the field is absent or odd."""
    try:
        return datetime.fromisoformat(record.ended) if record.ended else None
    except ValueError:
        return None


def _render_transcript(record: SessionRecord, max_chars: int) -> str:
    """The retain payload: a plain speaker-labelled transcript, tail-capped.

    Hindsight's extraction works on prose; the tail cap bounds session-end
    latency at the cost of dropping the oldest turns of a very long session —
    the canonical record keeps them all, so a later rebuild with a higher cap
    loses nothing.
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
    """retain/recall against a Hindsight server, one bank per companion."""

    name = "hindsight"

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._mode = str(cfg.get("mode", "sidecar"))
        self._proc: subprocess.Popen | None = None
        self._server = None  # embedded mode only
        self._client = None
        self._url: str | None = None
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._log: TextIO | None = None
        self._drain: threading.Thread | None = None

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

    # ── the sidecar's own log ────────────────────────────────────────────────

    def _log_path(self) -> Path:
        """Where the child's stderr/stdout goes.

        ``log_file`` in [memory.hindsight] wins outright (absolute or ~-relative);
        unset, it is DATA/logs/hindsight-sidecar.log — the operator's data root
        (config_loader's HEARTH_DATA anchor, the same one records.py writes
        under), never the engine tree, because this is runtime state.
        """
        configured = self._cfg.get("log_file")
        if configured:
            return Path(str(configured)).expanduser()
        from hearth.config import config_loader  # lazy: keeps import cost off the CLI path

        return config_loader.DATA_DIR.joinpath(*_DEFAULT_LOG_REL)

    def _rotate_log(self, path: Path) -> None:
        """One generation, checked at spawn: a long-lived operator install must
        not grow an unbounded log, and a session boundary is the only moment
        nothing is writing to the file."""
        try:
            if path.is_file() and path.stat().st_size > _LOG_ROTATE_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError as exc:  # noqa: BLE001 — a log that cannot rotate must not stop a session
            logger.warning("[memory] hindsight log rotate failed ({})", type(exc).__name__)

    def _open_log(self) -> TextIO:
        """Append-mode handle at 0600 in a 0700 dir (records.py's discipline).

        Line-buffered because two writers share it: the child (its stderr fd is
        this file) and the drain thread (its stdout, line by line).
        """
        path = self._log_path()
        path.parent.mkdir(mode=_LOG_DIR_MODE, parents=True, exist_ok=True)
        try:
            # parents=True won't re-chmod an existing parent; assert the leaf at least.
            os.chmod(path.parent, _LOG_DIR_MODE)
        except OSError:
            pass
        self._rotate_log(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _LOG_FILE_MODE)
        handle = os.fdopen(fd, "a", buffering=1, encoding="utf-8", errors="replace")
        try:
            os.chmod(path, _LOG_FILE_MODE)  # an existing file keeps its old mode otherwise
        except OSError:
            pass
        logger.info("[memory] hindsight sidecar log → {}", path)
        return handle

    def _write_log(self, line: str) -> None:
        """Best-effort: losing a log line never costs a session."""
        handle = self._log
        if handle is None:
            return
        try:
            handle.write(line if line.endswith("\n") else line + "\n")
        except (ValueError, OSError):  # closed underneath us by close()
            pass

    def _drain_stdout(self, proc: subprocess.Popen) -> None:
        """Keep reading the child's stdout forever — the WHOLE point is that the
        pipe can never fill: a full 64 KB buffer blocks the server's next print,
        which is a silent hang of the memory lane (incident 2026-08-30)."""
        stream = proc.stdout
        if stream is None:
            return
        try:
            for line in stream:
                self._write_log(line)
        except (ValueError, OSError):  # pipe closed at shutdown
            pass

    def _close_log(self) -> None:
        handle, self._log, self._drain = self._log, None, None
        if handle is None:
            return
        try:
            handle.close()
        except (ValueError, OSError):
            pass

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ensure(self) -> None:
        """Bring up the server lazily (first recall/store, not import) and keep
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
        rc = self._exited_rc()
        if rc is None:
            return
        logger.warning("[memory] hindsight sidecar died (rc={}) — respawning", rc)
        self._discard_dead_sidecar()
        self._connect()
        rc = self._exited_rc()
        if rc is not None:  # died again on the spot — do not loop, hand it up
            raise RuntimeError(f"hindsight sidecar died again immediately (rc={rc})")

    def _exited_rc(self) -> int | None:
        """The child's exit code if it has exited, else None. Embedded mode has
        no child, so it is always None — its liveness is the host's problem."""
        if self._mode != "sidecar" or self._proc is None:
            return None
        return self._proc.poll()

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
        self._close_log()
        self._proc = None
        self._client = None
        self._url = None

    def _connect(self) -> None:
        """Start the server for this mode, then bind a client to its URL."""
        if self._mode == "sidecar":
            self._start_sidecar()
        elif self._mode == "embedded":
            self._start_embedded()
        else:
            raise ValueError(f"unknown [memory.hindsight] mode: {self._mode!r}")
        self._client = self._new_client()

    def _new_client(self):
        """The single import seam for the light SDK — one place to patch in
        tests, which have no hindsight-client installed (and must not need it)."""
        from hindsight_client import Hindsight  # light SDK (hearth[memory-hindsight])

        return Hindsight(base_url=self._url)

    def _spawn_env(self) -> dict:
        env = dict(os.environ)
        for key, value in dict(self._cfg.get("env") or {}).items():
            env.setdefault(str(key), str(value))
        env.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        return env

    def _start_sidecar(self) -> None:
        python = self._cfg.get("python")
        if not python:
            raise ValueError(
                "[memory.hindsight] mode=sidecar needs `python` — the sidecar venv's "
                "interpreter (the venv holding hindsight-all; see docs/memory.md)"
            )
        runner = str(self._cfg.get("runner") or Path(__file__).with_name("sidecar_runner.py"))
        cmd = [
            str(python), runner,
            "--db-url", str(self._cfg.get("db_url", "pg0")),
            "--llm-provider", str(self._cfg.get("llm_provider", "ollama")),
            "--llm-model", str(self._cfg["llm_model"]),
            "--llm-api-key", str(self._cfg.get("llm_api_key", "")),
            "--log-level", str(self._cfg.get("log_level", "warning")),
        ]
        self._log = self._open_log()
        # start_new_session: the sidecar gets its OWN process group, so the
        # operator's Ctrl+C (SIGINT to the terminal's foreground group) never
        # reaches it. Run-observed 2026-08-30 (twice, rc=0 both times): the
        # child shut down gracefully the instant ^C landed, and the seam's
        # close-time store found a dead server 260 ms later. The sidecar's
        # lifetime is OURS to end — close() SIGTERMs it after store/extraction.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=self._log,
            text=True, env=self._spawn_env(), start_new_session=True,
        )
        deadline = time.monotonic() + float(
            self._cfg.get("start_timeout_s", _SIDECAR_START_TIMEOUT_S)
        )
        url: str | None = None
        while time.monotonic() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if line.startswith("HINDSIGHT_URL="):
                url = line.strip().split("=", 1)[1]
                break
            if line:
                self._write_log(line)  # startup noise: the log is where a failed boot is read
            if not line and proc.poll() is not None:
                self._close_log()
                raise RuntimeError(f"hindsight sidecar exited rc={proc.returncode} before ready")
        if url is None:
            proc.terminate()
            self._close_log()
            raise TimeoutError("hindsight sidecar did not become ready in time")
        self._proc = proc
        self._url = url
        # Daemon: the drain must never hold up interpreter exit, and it needs no
        # join — the read ends by itself when the pipe closes.
        self._drain = threading.Thread(
            target=self._drain_stdout, args=(proc,), name="hindsight-log", daemon=True
        )
        self._drain.start()
        logger.info("[memory] hindsight sidecar up at {} (pid {})", url, proc.pid)

    def _start_embedded(self) -> None:
        from hindsight import HindsightServer  # heavy closure — non-pipecat hosts only

        server = HindsightServer(
            db_url=str(self._cfg.get("db_url", "pg0")),
            llm_provider=str(self._cfg.get("llm_provider", "ollama")),
            llm_api_key=str(self._cfg.get("llm_api_key", "")),
            llm_model=str(self._cfg["llm_model"]),
            log_level=str(self._cfg.get("log_level", "warning")),
        )
        server.start()
        self._server = server
        self._url = server.url
        logger.info("[memory] hindsight embedded server up at {}", server.url)

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
        if self._proc is not None:
            rc = self._proc.poll()
            if rc is not None:
                # Nothing to terminate — say the code out loud, it is the only
                # trace of an unclean death besides the logfile.
                logger.warning("[memory] hindsight sidecar had already exited (rc={})", rc)
            else:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=30)
                except Exception as exc:  # noqa: BLE001 — shutdown must not raise
                    logger.warning(
                        "[memory] hindsight sidecar stop failed ({})", type(exc).__name__
                    )
                    try:
                        self._proc.kill()
                    except OSError:
                        pass
        if self._server is not None:
            try:
                self._server.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[memory] hindsight stop failed ({})", type(exc).__name__)
        self._close_log()  # the drain thread is a daemon: it ends with the pipe, never joined
        self._proc = None
        self._server = None
        self._client = None
        self._url = None

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
