"""supervisor/child.py — the voice bot as an owned child process.

Process-level truth only: spawn in its own process group with
EXPLICIT session args (the interactive session picker never engages without a
TTY, so the API passes --new / --resume [name], plus --memory <mode> when the
caller sets the sitting's memory posture); stop via the stop.sh
escalation ladder — SIGINT → wait → SIGTERM → wait → SIGKILL — so the graceful
finally path (TokenMeter summary, capture finalize, memory on_session_end,
session finalize/hold) always gets its chance. Warm stop only: the LLM server
is never touched (the stop.sh contract).

Adopt, don't collide: a bot that is already running (started at the desk, or
surviving a daemon restart — children live in their own process group, so a
daemon death never takes a live conversation) is found by the stop.sh pgrep
pattern and ADOPTED as unmanaged-but-reported, never killed or duplicated.
"readiness" here means the process is alive; pipeline-ready detection is a
later stroke — the panel proxy already tells the truth about readiness.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from hearth.serve import SUPERVISED_ENV

# stop.sh parity: matches python / python3 / python3.12 running the bot module.
_PATTERN = r"python[0-9.]* -m hearth\.pipeline\.bot"

# The bot's own --memory choices (kept in lockstep with bot.py's argparse).
_MEMORY_MODES = ("full", "recall-only", "off")

# SIGINT → escalate wait. stop.sh waits ~6 s; the daemon is deliberately more
# generous because memory consolidation (on_session_end) runs on this path.
STOP_GRACE_S = 15.0
# SIGTERM → SIGKILL wait (stop.sh: 1 s; a little headroom here).
TERM_GRACE_S = 5.0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class BotChild:
    """One voice-bot child: spawn / adopt / stop / report. No secrets held."""

    def __init__(
        self,
        *,
        argv: Optional[list] = None,
        env_overlay: Optional[dict] = None,
        log_path: Optional[Path] = None,
        pattern: str = _PATTERN,
        stop_grace_s: float = STOP_GRACE_S,
        term_grace_s: float = TERM_GRACE_S,
    ) -> None:
        self._argv = list(argv) if argv else [sys.executable, "-m", "hearth.pipeline.bot"]
        self._env_overlay = dict(env_overlay or {})
        self._log_path = Path(log_path) if log_path else None
        self._pattern = pattern
        self._stop_grace_s = float(stop_grace_s)
        self._term_grace_s = float(term_grace_s)
        self.state = "down"  # down | starting | running | stopping
        self.managed = False  # True = our spawn (proc handle held); False = adopted
        self.pid: Optional[int] = None
        self.started_at: Optional[float] = None  # adoption time for adopted (true start unknowable)
        self.last_exit: Optional[dict] = None  # {"code": int|None, "at": iso}
        self._proc = None
        self._reaper: Optional[asyncio.Task] = None
        self._log_fh = None

    # ── discovery ─────────────────────────────────────────────────────────────

    async def probe(self) -> list:
        """PIDs matching the bot pattern (pgrep -f, the stop.sh idiom)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep", "-f", self._pattern,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
        except OSError:
            return []
        me = os.getpid()
        return [p for p in (int(x) for x in out.split()) if p != me]

    async def adopt(self) -> bool:
        """Adopt an already-running bot (unmanaged). True iff one is live."""
        if self.state == "running":
            return True
        pids = await self.probe()
        if not pids:
            return False
        self.pid = pids[0]
        self.managed = False
        self.state = "running"
        self.started_at = time.time()
        logger.info("[supervisor] adopted a running bot (pid {}, unmanaged)", self.pid)
        return True

    async def reconcile(self) -> bool:
        """Re-anchor to process truth; True iff a bot is live. The status-poll hook.

        Two staleness directions, both adopted-only (a managed spawn has a
        reaper): a bot started at the desk AFTER the last adopt sweep (state
        still "down"), and an adopted bot stopped at the desk (state still
        "running" on a dead pid — nothing reaps a process we never spawned).
        "starting"/"stopping" are mid-transition on our own paths: left alone.
        """
        if self.state == "down":
            return await self.adopt()
        if self.state == "running" and not self.managed and self.pid is not None:
            if not await self._alive(self.pid):
                self.last_exit = {"code": None, "at": _now_iso()}  # adopted: code unknowable
                self.state = "down"
                self.pid = None
                logger.info("[supervisor] adopted bot is gone — marked down")
                # stop.sh + start.sh can both land between polls: sweep again
                # so a replacement desk bot is picked up in the same breath.
                return await self.adopt()
        return self.state in ("starting", "running")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, mode: str = "new", name: Optional[str] = None,
                    memory: Optional[str] = None) -> dict:
        if self.state in ("starting", "running", "stopping"):
            return {"ok": False, "error": f"bot is {self.state}", "pid": self.pid}
        if await self.adopt():
            return {"ok": False, "error": "a bot is already running — adopted, not restarted",
                    "pid": self.pid, "adopted": True}
        if mode == "new":
            args = ["--new"]
        elif mode == "resume":
            args = ["--resume"] + ([name] if name else [])
        else:
            return {"ok": False, "error": f"unknown mode {mode!r} (new | resume)"}
        if memory is not None:
            # Forwarded even for an explicit "full": the flag wins over a resumed
            # session's stamp (inherit_memory_mode); None = absent = inherit.
            if memory not in _MEMORY_MODES:
                return {"ok": False,
                        "error": f"unknown memory mode {memory!r} (full | recall-only | off)"}
            args += ["--memory", memory]

        env = dict(os.environ)
        env.update(self._env_overlay)  # values never logged
        env[SUPERVISED_ENV] = "1"  # tells the bot its parent is the facade (no attach)
        stdout = asyncio.subprocess.DEVNULL
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_fh = open(self._log_path, "ab")
            stdout = self._log_fh
        self.state = "starting"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv, *args,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,  # deterministic: no picker, ever
                stdout=stdout,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # own group: survives the daemon; killpg targets it
            )
        except OSError as exc:
            self.state = "down"
            self._close_log()
            return {"ok": False, "error": f"spawn failed ({type(exc).__name__})"}
        self.pid = self._proc.pid
        self.managed = True
        self.started_at = time.time()
        self.state = "running"
        self._reaper = asyncio.create_task(self._reap(self._proc))
        logger.info("[supervisor] bot started (pid {}, mode {}{})", self.pid, mode,
                    f", memory {memory}" if memory else "")
        result = {"ok": True, "pid": self.pid, "mode": mode}
        if memory is not None:
            result["memory"] = memory
        return result

    async def stop(self, hold: bool = False, name: Optional[str] = None) -> dict:
        if self.state == "down" and not await self.adopt():
            return {"ok": True, "note": "no bot running — nothing to stop"}
        pid = self.pid
        proc = self._proc  # captured now: the reaper clears it when the child dies
        if hold:
            # stop.sh --hold parity: drop the marker BEFORE signaling so the
            # bot's shutdown `finally` keeps (promotes) its session.
            try:
                from hearth.session import session_store  # lazy: reads active.toml

                session_store.write_hold_request(name)
            except Exception as exc:  # noqa: BLE001 — a marker failure must not block the stop
                logger.warning("[supervisor] hold marker failed ({}) — stopping without hold",
                               type(exc).__name__)
        self.state = "stopping"
        escalated = False
        ladder = ((signal.SIGINT, self._stop_grace_s),
                  (signal.SIGTERM, self._term_grace_s),
                  (signal.SIGKILL, 2.0))
        dead = False
        for sig, grace in ladder:
            self._signal(pid, sig)
            if await self._wait_dead(pid, grace):
                dead = True
                break
            escalated = True
            logger.warning("[supervisor] bot pid {} outlived {} — escalating", pid, sig.name)
        if not dead:
            self.state = "running"
            return {"ok": False, "error": "could not stop the bot", "pid": pid}
        code = None
        if proc is not None:  # our spawn: wait() is idempotent, returncode caches
            with contextlib.suppress(asyncio.TimeoutError):
                code = await asyncio.wait_for(proc.wait(), 2.0)
        self.last_exit = {"code": code, "at": _now_iso()}  # adopted: code unknowable
        self.state = "down"
        self.pid = None
        self._proc = None
        self._close_log()
        return {"ok": True, "escalated": escalated, "held": bool(hold)}

    async def _reap(self, proc) -> None:
        """Record a child that exits on its own (desk Ctrl-C, crash, outage)."""
        code = await proc.wait()
        if self._proc is proc:  # not superseded by a later start/stop
            self.last_exit = {"code": code, "at": _now_iso()}
            self.state = "down"
            self.pid = None
            self._proc = None
            self._close_log()
            logger.info("[supervisor] bot exited on its own (code {})", code)

    def close(self) -> None:
        """Daemon shutdown: abandon (never kill) the child; adopt on relaunch."""
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        self._close_log()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _signal(self, pid: int, sig: int) -> None:
        try:
            if self.managed:
                os.killpg(pid, sig)  # our spawn: pgid == pid (start_new_session)
            else:
                os.kill(pid, sig)  # adopted: signal the matched PID only (stop.sh shape)
        except ProcessLookupError:
            pass
        except (PermissionError, OSError):
            with contextlib.suppress(OSError):
                os.kill(pid, sig)

    async def _wait_dead(self, pid: int, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not await self._alive(pid):
                return True
            await asyncio.sleep(0.25)
        return not await self._alive(pid)

    @staticmethod
    async def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        # Signal-visible but possibly a ZOMBIE (dead, parent not reaping — an
        # adopted child whose parent is elsewhere). ps stat 'Z' ⇒ dead for us.
        try:
            proc = await asyncio.create_subprocess_exec(
                "ps", "-o", "stat=", "-p", str(pid),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
        except OSError:
            return True  # no ps? believe the signal probe
        stat = out.strip()
        if not stat:
            return False
        return not stat.startswith(b"Z")

    def _close_log(self) -> None:
        if self._log_fh is not None:
            with contextlib.suppress(OSError):
                self._log_fh.close()
            self._log_fh = None

    def status(self) -> dict:
        """Process-level truth for /admin/state. Names and numbers only."""
        up = None
        if self.state in ("starting", "running") and self.started_at is not None:
            up = round(time.time() - self.started_at, 1)
        return {"state": self.state, "pid": self.pid, "managed": self.managed,
                "uptime_s": up, "last_exit": self.last_exit}
