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
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import subprocess
import time
from pathlib import Path

from loguru import logger

from .backend import MemoryItem, SessionRecord

# Egress kill switch #2 (survey §5b) — for embedded mode / hand-run CLIs.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

_MAX_RETAIN_CHARS_DEFAULT = 6000
_SIDECAR_START_TIMEOUT_S = 180.0  # cold pg0 init + model load can be slow once


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

    def _call(self, fn, /, *args, **kwargs):
        """Run a sync SDK method safely from sync OR async context.

        In a plain script (CLI rebuild) the call goes straight through. Inside
        a running event loop (the bot) it executes on ONE persistent worker
        thread owned by this backend: the SDK caches an aiohttp ClientSession
        bound to the loop of the first call, so every call must share that
        thread/loop pair. A short-lived per-call thread leaves the cached
        session on a dead loop — RuntimeError on the second call (run-observed
        2026-08-30, the first in-bot store after a recall).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return fn(*args, **kwargs)
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="hindsight-io"
            )
        return self._pool.submit(fn, *args, **kwargs).result()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ensure(self) -> None:
        """Bring up the server lazily (first recall/store, not import)."""
        if self._client is not None:
            return
        if self._mode == "sidecar":
            self._start_sidecar()
        elif self._mode == "embedded":
            self._start_embedded()
        else:
            raise ValueError(f"unknown [memory.hindsight] mode: {self._mode!r}")
        from hindsight_client import Hindsight  # light SDK (hearth[memory-hindsight])

        self._client = Hindsight(base_url=self._url)

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
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=self._spawn_env(),
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
            if not line and proc.poll() is not None:
                raise RuntimeError(f"hindsight sidecar exited rc={proc.returncode} before ready")
        if url is None:
            proc.terminate()
            raise TimeoutError("hindsight sidecar did not become ready in time")
        self._proc = proc
        self._url = url
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
            try:
                self._proc.terminate()
                self._proc.wait(timeout=30)
            except Exception as exc:  # noqa: BLE001 — shutdown must not raise
                logger.warning("[memory] hindsight sidecar stop failed ({})", type(exc).__name__)
                try:
                    self._proc.kill()
                except OSError:
                    pass
        if self._server is not None:
            try:
                self._server.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[memory] hindsight stop failed ({})", type(exc).__name__)
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
        return items

    def store(self, companion: str, record: SessionRecord) -> None:
        transcript = _render_transcript(
            record, int(self._cfg.get("retain_max_chars", _MAX_RETAIN_CHARS_DEFAULT))
        )
        if not transcript:
            return
        self._ensure()
        self._call(self._client.retain, bank_id=companion, content=transcript)

    def consolidate(self, companion: str) -> None:  # noqa: ARG002
        """No-op this pass: retain already extracts; Hindsight's ``reflect`` is
        an LLM-driven deliberation better wired to a real idle trigger, which
        the engine doesn't have yet (see docs/memory.md)."""
