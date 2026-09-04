"""backend_hindsight/sidecar.py — the hindsight server process, and keeping it
visible.

WHY a sidecar at all (learned the hard way, 2026-08-30): hindsight's server
closure requires protobuf>=7 while pipecat (the voice pipeline) pins
protobuf<7, so the server can NEVER share the engine venv. The split that
works:

  engine venv   : the adapter + ``hindsight-client`` (featherweight SDK —
                  aiohttp/pydantic, no protobuf) via ``hearth[memory-hindsight]``
  sidecar venv  : ``hindsight-all`` (the 1.4 GB closure), owned by the operator
                  (see docs/memory.md), executed through sidecar_runner.py

``mode = "sidecar"`` (default) spawns the runner with the configured sidecar
python and talks HTTP over loopback; ``mode = "embedded"`` keeps the old
in-process import for non-pipecat hosts (CLI rebuilds, tests, future spines).
Both are started from here, so the adapter above never learns which one it got
— it asks for a URL and is handed one.

Costs, stated plainly (run-verified): pg0 = a real bundled PostgreSQL
(~15 processes) on FIXED port 5432, data under ~/.pg0 — one instance per
machine; server start ≈ 5–14 s warm, paid once at session start. Egress: the
engine already sets HF_HUB_OFFLINE=1; this module and the runner set
LITELLM_LOCAL_MODEL_COST_MAP. First-ever run must fetch the embed/rerank
models once: HF_HUB_OFFLINE=0.

SURVIVABILITY, which is most of what this file is (incident 2026-08-30): the
child is a separate process that can die mid-session, and it used to die
INVISIBLY — its stderr went to DEVNULL and its stdout was read only until the
handshake line, so a full pipe buffer could stall the server outright. Both
holes are closed here: the child's stderr is appended to a logfile and a daemon
thread drains its stdout into the same file for the process's whole life. The
adapter asks ``exited_rc()`` and decides what to do about a corpse; this file
only ever reports.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import TextIO

from loguru import logger

from .sweep import _reap_orphaned_sidecars

# Egress kill switch #2 — for embedded mode / hand-run CLIs. It lives here
# because this is the file that starts an in-process server; importing the
# adapter still trips it, since the adapter imports this module.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

_SIDECAR_START_TIMEOUT_S = 180.0  # cold pg0 init + model load can be slow once

# Same permission discipline as records.py: the sidecar log carries startup
# noise and extraction chatter about the operator's conversations.
_LOG_DIR_MODE = 0o700
_LOG_FILE_MODE = 0o600
_LOG_ROTATE_BYTES = 5 * 1024 * 1024  # one generation kept (<name>.1); no scheduler needed
_DEFAULT_LOG_REL = ("logs", "hindsight-sidecar.log")


class Sidecar:
    """One hindsight server and everything it takes to keep one honest.

    Owns exactly three things: the child process (or the embedded server), the
    logfile that makes it visible, and the drain thread that keeps its pipe from
    filling. Knows nothing about memory, banks, recall or the SDK — it hands out
    a URL and answers whether the thing behind it is still alive.

    Not restarted from in here: a corpse is REPORTED (``exited_rc``) and the
    adapter decides, because respawn policy is a memory-lane decision (one
    retry, then degrade) rather than a process-supervision one.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._mode = str(cfg.get("mode", "sidecar"))
        self._proc: subprocess.Popen | None = None
        self._server = None  # embedded mode only
        self._log: TextIO | None = None
        self._drain: threading.Thread | None = None
        self.url: str | None = None

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
        except (ValueError, OSError):  # closed underneath us by stop()
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

    # ── starting one ─────────────────────────────────────────────────────────

    def start(self) -> str:
        """Bring the server up for this mode and return its URL."""
        if self._mode == "sidecar":
            self._start_sidecar()
        elif self._mode == "embedded":
            self._start_embedded()
        else:
            raise ValueError(f"unknown [memory.hindsight] mode: {self._mode!r}")
        return self.url

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
        runner = str(self._cfg.get("runner")
                     or Path(__file__).parent.parent / "sidecar_runner.py")
        # Sweep before spawning, not after: this is the one moment we are certain
        # a sidecar is wanted, and it makes the leak self-healing at the next
        # session rather than something the operator has to notice.
        _reap_orphaned_sidecars(runner)
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
        # lifetime is OURS to end — stop() SIGTERMs it after store/extraction.
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
        self.url = url
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
        self.url = server.url
        logger.info("[memory] hindsight embedded server up at {}", server.url)

    # ── liveness, and the two ways one ends ──────────────────────────────────

    def exited_rc(self) -> int | None:
        """The child's exit code if it has exited, else None. Embedded mode has
        no child, so it is always None — its liveness is the host's problem."""
        if self._mode != "sidecar" or self._proc is None:
            return None
        return self._proc.poll()

    def discard(self) -> None:
        """Let go of a child that is ALREADY dead: release the log, forget the
        handles. Nothing is signalled — there is nothing left to signal."""
        self._close_log()
        self._proc = None
        self.url = None

    def stop(self) -> None:
        """End it: SIGTERM, wait, kill as a last resort; stop an embedded
        server; release the log. Never raises — this runs at shutdown."""
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
        self.url = None
