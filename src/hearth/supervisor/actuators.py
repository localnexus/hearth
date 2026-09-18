"""supervisor/actuators.py — declared external actuators.

The supervisor OWNS the voice bot and nothing else: every other process
is a watched external. An actuator is the operator's own declared command —
``[serve.supervisor.actuators.<name>]`` in serve.toml — for the moments
watching is not enough: free the LLM server's models (an explicit cold stop;
warm stays the default everywhere), bring a streaming client back after a
reboot, kick a stalled service.

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
  * one run per actuator at a time; a second press answers busy;
  * an optional guard = "companion": refused while a companion is running,
    unless the press carries ?force=1 — the shape for a command whose cost
    the NEXT TURN pays (freeing the model server's models: a live session
    owns its model's residency, and only a confirmed press may take it);
  * a STEPPED actuator names other actuators instead of a command —
    ``steps = [{actuator, accept, until, wait_s, retry_s}, …]`` — and runs
    them in order, each through the same bounded runner and its own log:
    ``accept`` lists the exit codes that count as done (default [0]);
    ``until = "probe-down"`` waits (≤ ``wait_s``) after the step for the
    actuator's own probe_url host:port to stop accepting connections — the
    shape of "stop the door, then wait for it to actually leave"; ``retry_s``
    presses a step again, once a second, until it exits 0 or the seconds run
    out — the shape of "start it as soon as launchd will let you". A stepped
    actuator has no command of its own; it is only built-in blocks that use
    the shape today (models/door.py's door-reload).
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
from urllib.parse import urlsplit

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
            steps = _parse_steps(entry.get("steps"))
            if not command and not steps:
                # the registry catches this at check time; at runtime a bad
                # block costs only itself, never the mount (containment posture)
                logger.warning("[supervisor] actuator {!r} has no command — skipped", name)
                continue
            self._acts[str(name)] = {
                "command": command,
                "steps": steps,
                "timeout_s": float(entry.get("timeout_s", DEFAULT_TIMEOUT_S)),
                "cwd": str(entry.get("cwd") or ""),
                "note": str(entry.get("note") or ""),
                "probe_url": str(entry.get("probe_url") or ""),
                "guard": str(entry.get("guard") or ""),
            }

    def __contains__(self, name: object) -> bool:
        return name in self._acts

    def names(self) -> list[str]:
        return sorted(self._acts)

    def guard(self, name: str) -> str:
        """The declared guard for one actuator ("" = none)."""
        return self._acts[name]["guard"]

    def probe_urls(self) -> dict[str, str]:
        return {n: a["probe_url"] for n, a in self._acts.items() if a["probe_url"]}

    def status(self) -> dict:
        """name → note/running/last record — no commands, no output."""
        return {n: {"note": a["note"],
                    "guard": a["guard"],
                    "running": n in self._running,
                    "last": self._last.get(n)}
                for n, a in self._acts.items()}

    async def run(self, name: str) -> dict:
        act = self._acts[name]  # KeyError = the caller's 404
        if name in self._running:
            raise ActuatorBusy(name)
        self._running.add(name)
        try:
            if act["steps"]:
                record = await self._run_steps(name, act)
            else:
                record = await self._run_bounded(name, act)
        finally:
            self._running.discard(name)
        self._last[name] = record
        return record

    async def _run_steps(self, name: str, act: dict) -> dict:
        """The steps in order, each an ordinary run of the actuator it names;
        the first one that does not come out right ends the sequence. The
        stepped actuator's own log carries one line per step; the step's
        output is in the step's log, as always."""
        started, t0 = _now_iso(), time.monotonic()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._log_dir, 0o700)
        log_path = self._log_dir / f"{name}.log"
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(log_path, 0o600)
        steps_out: list[dict] = []
        ok, failed_step, last_exit = True, None, None

        def note(line: str) -> None:
            os.write(fd, (line + "\n").encode("utf-8"))

        try:
            note(f"\n── {started} — run ({len(act['steps'])} steps)")
            for step in act["steps"]:
                target = step["actuator"]
                if target not in self._acts:
                    note(f"step {target}: not declared — stopping")
                    ok, failed_step = False, target
                    break
                deadline = time.monotonic() + step["retry_s"]
                while True:
                    try:
                        rec = await self.run(target)
                    except ActuatorBusy:
                        note(f"step {target}: busy — stopping")
                        ok, failed_step = False, target
                        break
                    last_exit = rec["exit"]
                    done = (not rec["timed_out"]) and rec["exit"] in step["accept"]
                    if done or time.monotonic() >= deadline:
                        break
                    note(f"step {target}: exit {rec['exit']} — pressing again")
                    await asyncio.sleep(1.0)
                if failed_step is not None:
                    break
                steps_out.append({"actuator": target, "exit": rec["exit"],
                                  "timed_out": rec["timed_out"],
                                  "duration_s": rec["duration_s"], "ok": done})
                note(f"step {target}: exit {rec['exit']}"
                     f"{' (timeout)' if rec['timed_out'] else ''} in {rec['duration_s']}s")
                if not done:
                    ok, failed_step = False, target
                    break
                if step["until"] == "probe-down":
                    probe = self._acts[target]["probe_url"] or act["probe_url"]
                    gone = await _wait_port_closed(probe, step["wait_s"])
                    note(f"step {target}: probe {'down' if gone else 'STILL UP'} "
                         f"after the wait ({probe or 'no probe_url'})")
                    if not gone:
                        ok, failed_step = False, target
                        break
        finally:
            os.close(fd)
        duration = round(time.monotonic() - t0, 2)
        record = {"ok": ok, "exit": last_exit, "timed_out": False,
                  "started": started, "duration_s": duration,
                  "log": str(log_path), "steps": steps_out,
                  "failed_step": failed_step}
        logger.info("[supervisor] actuator {} → {} ({} steps) in {}s",
                    name, "ok" if ok else f"failed at {failed_step}",
                    len(steps_out), duration)
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


def _parse_steps(raw) -> list[dict]:
    """The steps of a stepped actuator, leniently: a bare string is the name
    of an actuator with the defaults; anything unreadable is dropped."""
    out: list[dict] = []
    for item in list(raw or []):
        if isinstance(item, str):
            item = {"actuator": item}
        if not isinstance(item, dict) or not item.get("actuator"):
            continue
        accept = item.get("accept")
        try:
            accept_set = {int(a) for a in (accept if accept is not None else [0])}
        except (TypeError, ValueError):
            accept_set = {0}
        out.append({
            "actuator": str(item["actuator"]),
            "accept": accept_set,
            "until": str(item.get("until") or ""),
            "wait_s": float(item.get("wait_s", 30.0)),
            "retry_s": float(item.get("retry_s", 0.0)),
        })
    return out


async def _wait_port_closed(probe_url: str, wait_s: float) -> bool:
    """True once nothing accepts a TCP connection at the probe's host:port
    (or at once when there is no probe to ask) — a process that has left
    holds no listener. False when it is still there after `wait_s`."""
    parts = urlsplit(probe_url or "")
    host, port = parts.hostname, parts.port
    if not host or not port:
        return True
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 1.0)
        except (OSError, asyncio.TimeoutError):
            return True
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.5)
