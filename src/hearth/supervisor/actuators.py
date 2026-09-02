"""supervisor/actuators.py — declared external actuators.

The supervisor OWNS the voice bot and nothing else: every other process
is a watched external. An actuator is the operator's own declared command —
``[serve.supervisor.actuators.<name>]`` in serve.toml — for the moments
watching is not enough: free the LLM server's models (an explicit cold stop;
warm stays the default everywhere), bring StreamCore back after a reboot,
kick a stalled roster.

Containment shape:
  * fixed argv, exec'd directly — no shell, and no runtime arguments: the
    config file is the sole authority on what can run behind the door;
  * bounded — timeout_s, then SIGTERM → SIGKILL on the DIRECT command only:
    a bring-up script that deliberately detaches a server (its own session)
    never becomes this daemon's child, and a timeout never reaps what it
    left running;
  * output goes to a 0600 log file (DATA/logs/actuators/<name>.log), never
    into a response — a command may print what a route must not;
    routes carry names, exit codes, and durations only;
  * one run per actuator at a time; a second press answers busy.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

DEFAULT_TIMEOUT_S = 120.0
_TERM_GRACE_S = 2.0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ActuatorBusy(Exception):
    """This actuator is already mid-run (one at a time, per name)."""


class ActuatorSet:
    """The declared actuators: parse leniently, run bounded, report honestly."""

    def __init__(self, cfg: dict, log_dir: Path) -> None:
        self._log_dir = Path(log_dir)
        self._acts: dict[str, dict] = {}
        self._running: set[str] = set()
        self._last: dict[str, dict] = {}
        for name, raw in dict(cfg or {}).items():
            entry = dict(raw or {})
            command = [str(a) for a in (entry.get("command") or [])]
            if not command:
                # the registry catches this at check time; at runtime a bad
                # block costs only itself, never the mount (decider-6 posture)
                logger.warning("[supervisor] actuator {!r} has no command — skipped", name)
                continue
            self._acts[str(name)] = {
                "command": command,
                "timeout_s": float(entry.get("timeout_s", DEFAULT_TIMEOUT_S)),
                "cwd": str(entry.get("cwd") or ""),
                "note": str(entry.get("note") or ""),
                "probe_url": str(entry.get("probe_url") or ""),
            }

    def __contains__(self, name: object) -> bool:
        return name in self._acts

    def names(self) -> list[str]:
        return sorted(self._acts)

    def probe_urls(self) -> dict[str, str]:
        return {n: a["probe_url"] for n, a in self._acts.items() if a["probe_url"]}

    def status(self) -> dict:
        """name → note/running/last record — no commands, no output."""
        return {n: {"note": a["note"],
                    "running": n in self._running,
                    "last": self._last.get(n)}
                for n, a in self._acts.items()}

    async def run(self, name: str) -> dict:
        act = self._acts[name]  # KeyError = the caller's 404
        if name in self._running:
            raise ActuatorBusy(name)
        self._running.add(name)
        try:
            record = await self._run_bounded(name, act)
        finally:
            self._running.discard(name)
        self._last[name] = record
        return record

    async def _run_bounded(self, name: str, act: dict) -> dict:
        started, t0 = _now_iso(), time.monotonic()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._log_dir, 0o700)
        log_path = self._log_dir / f"{name}.log"
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(log_path, 0o600)
        timed_out = False
        try:
            os.write(fd, f"\n── {started} — run\n".encode("utf-8"))
            proc = await asyncio.create_subprocess_exec(
                *act["command"],
                stdout=fd, stderr=fd,
                cwd=act["cwd"] or None,
                start_new_session=True,  # what it detaches is never our child
            )
            try:
                rc = await asyncio.wait_for(proc.wait(), act["timeout_s"])
            except asyncio.TimeoutError:
                timed_out = True
                rc = await self._reap(proc)
        except OSError as exc:  # spawn itself failed (bad path, perms)
            logger.warning("[supervisor] actuator {} spawn failed ({})",
                           name, type(exc).__name__)
            rc = None
        finally:
            os.close(fd)
        duration = round(time.monotonic() - t0, 2)
        record = {"ok": (rc == 0 and not timed_out), "exit": rc,
                  "timed_out": timed_out, "started": started,
                  "duration_s": duration, "log": str(log_path)}
        logger.info("[supervisor] actuator {} → exit {}{} in {}s",
                    name, rc, " (timeout)" if timed_out else "", duration)
        return record

    @staticmethod
    async def _reap(proc) -> Optional[int]:
        """SIGTERM → grace → SIGKILL, on the DIRECT command only."""
        for sig, grace in ((signal.SIGTERM, _TERM_GRACE_S), (signal.SIGKILL, 5.0)):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                break
            with contextlib.suppress(asyncio.TimeoutError):
                return await asyncio.wait_for(proc.wait(), grace)
        with contextlib.suppress(asyncio.TimeoutError):
            return await asyncio.wait_for(proc.wait(), 1.0)
        return proc.returncode
