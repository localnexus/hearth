"""compact_watch — the facade's auto-compaction execution half.

The bot's close path (``session/compact_trigger``) drops ``*.request`` files
in ``DATA/ops/compact-queue/``; this watch — a low-cadence background task on
the serve facade — runs them once no bot is alive. A periodic scan (not a
stop-hook) is deliberate: it also covers desk-stopped bots and closes that
happened while the facade itself was down.

Execution contract:

- The **maintenance lock is the only in-progress truth** (see
  ``session/maintenance_lock``). The watch fires nothing while ANY compaction
  lock is held (manual desk runs included) and nothing while the bot child is
  up — one compaction at a time, model RAM being what it is.
- A claim is ``.request`` → ``.running`` (rename, then the claim time is
  written into it); the spawned compactor gets ``--request-file`` and owns
  the file from there (success = removed, failure = ``.failed``). A
  ``.running`` whose character lock is FREE and whose claim is stale means
  the run died before reporting — surfaced as ``.failed``, never retried.
- The compactor itself is operator machinery, not part of this tree:
  ``DATA/ops/compact-companion-session.sh``. Absent, requests stay parked
  and one log line says so per facade life.
- Spawned **detached** (own session): a facade bounce must not kill a
  mid-run compaction. Output appends to ``DATA/logs/compact-auto.log``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from hearth.config import config_loader
from hearth.session import maintenance_lock

INTERVAL_S = 60.0
STALE_RUNNING_S = 180.0
# A run that deferred itself (RAM floor — see the compactor's gate) stamps
# deferred_ts back into its restored .request; leave it parked this long
# before the next attempt, so a busy machine gets a probe every ten minutes
# instead of a spawn-and-defer cycle every tick.
DEFER_RECHECK_S = 600.0


def queue_dir() -> Path:
    return Path(config_loader.DATA_DIR) / "ops" / "compact-queue"


def compactor_path() -> Path:
    return Path(config_loader.DATA_DIR) / "ops" / "compact-companion-session.sh"


#: Queue file suffix → the state a person should read it as.
_QUEUE_STATES = {".request": "parked", ".running": "running", ".failed": "failed"}


def queue_status() -> list:
    """The queue as the panel may show it: one entry per queue file, NAMES AND
    STATES ONLY — never a byte of session content.

    ``.failed`` is why this exists. A held maintenance lock already surfaces a
    running compaction, but a run that dies in its first second holds the lock
    for less than one poll, so the launch page could never catch it — and the
    breadcrumb it leaves is never auto-retried, by design. Without this the
    only record of a failure is a line in ``logs/compact-auto.log``, which is
    to say: invisible to the person who pressed the button.
    """
    qdir = queue_dir()
    if not qdir.is_dir():
        return []
    out = []
    for path in sorted(qdir.iterdir()):
        state = _QUEUE_STATES.get(path.suffix)
        if state is None:
            continue
        info = _read_info(path) or {}
        out.append({
            "state": state,
            "character": info.get("character") or "?",
            "session": info.get("session") or "?",
            "source": info.get("source") or "auto",
            "requested": info.get("requested"),
            # Written by the compactor's exit path; absent on older breadcrumbs
            # and on anything that died without reaching it.
            "error": info.get("error"),
            "step": info.get("step"),
        })
    return out


def _read_info(path: Path) -> Optional[dict]:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


async def tick(app) -> Optional[str]:
    """One scan pass. Returns a short action note (for logs/tests), or None."""
    child = app.get("bot_child") if hasattr(app, "get") else app["bot_child"]
    if child is not None and child.status().get("state") in ("starting", "running"):
        return None  # a bot owns the stage — compaction waits

    qdir = queue_dir()
    if not qdir.is_dir():
        return None

    # Standing claims first: a held character lock = a compaction is active
    # (ours or a desk run) — one at a time. A free lock on a stale claim = the
    # run died before reporting; surface it, never retry.
    for running in sorted(qdir.glob("*.running")):
        info = _read_info(running) or {}
        char = info.get("character")
        held = maintenance_lock.probe(char) if char else None
        if held is not None:
            return None  # active compaction — wait
        claimed = float(info.get("claimed_ts") or running.stat().st_mtime)
        if time.time() - claimed > STALE_RUNNING_S:
            failed = running.with_name(running.name[:-len(".running")] + ".failed")
            os.replace(running, failed)
            logger.warning("[compact-watch] {} died before reporting — marked "
                           ".failed (a human clears it)", running.name)
            return f"reclaimed {failed.name}"
        return None  # young claim — the run is warming up
    if maintenance_lock.held_locks(op="compact"):
        return None  # manual compaction under way somewhere

    requests = [r for r in sorted(qdir.glob("*.request"))
                if time.time() - float((_read_info(r) or {}).get("deferred_ts") or 0)
                > DEFER_RECHECK_S]
    if not requests:
        return None
    script = compactor_path()
    if not (script.is_file() and os.access(script, os.X_OK)):
        if not app.get("compact_watch_no_script_logged"):
            app["compact_watch_no_script_logged"] = True
            logger.warning("[compact-watch] {} request(s) parked — no compactor "
                           "installed at {}", len(requests), script)
        return None

    req = requests[0]
    info = _read_info(req)
    if info is None or not info.get("character") or not info.get("session"):
        failed = req.with_name(req.name[:-len(".request")] + ".failed")
        os.replace(req, failed)
        logger.warning("[compact-watch] unreadable request {} — marked .failed",
                       req.name)
        return f"bad request {req.name}"

    running = req.with_name(req.name[:-len(".request")] + ".running")
    os.replace(req, running)  # the claim
    info["claimed_ts"] = time.time()
    running.write_text(json.dumps(info, indent=1), encoding="utf-8")

    log_path = Path(config_loader.DATA_DIR) / "logs" / "compact-auto.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "ab") as logf:
            logf.write(f"\n── {time.strftime('%Y-%m-%dT%H:%M:%S')} auto-compact "
                       f"{info['character']}/{info['session']}\n".encode())
            proc = await asyncio.create_subprocess_exec(
                str(script), str(info["session"]),
                "--lane", "hearth", "--character", str(info["character"]),
                "--yes", "--request-file", str(running),
                stdout=logf, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "HEARTH_DATA": str(config_loader.DATA_DIR)},
            )
    except OSError as exc:
        failed = running.with_name(running.name[:-len(".running")] + ".failed")
        os.replace(running, failed)
        logger.warning("[compact-watch] spawn failed ({}) — {} marked .failed",
                       type(exc).__name__, failed.name)
        return f"spawn failed {failed.name}"

    # Reap without tethering: if the facade dies, the child (own session)
    # carries on and the request file still gets its honest ending from
    # --request-file handling in the compactor itself.
    reaper = asyncio.create_task(proc.wait())
    app.setdefault("compact_watch_reapers", set()).add(reaper)
    reaper.add_done_callback(app["compact_watch_reapers"].discard)
    logger.info("[compact-watch] auto-compaction started: {}/{} (pid {})",
                info["character"], info["session"], proc.pid)
    return f"started {info['character']}/{info['session']}"


async def submit(app, character: str, session: str) -> dict:
    """Manual initiation (the /admin/compact door): queue a request for
    (character, session) and run one tick immediately.

    A manual click is a human decision — it clears a prior ``.failed``
    breadcrumb and any stale parked request for the pair. Returns
    {"ok", "note"}: ok=True with the tick's action when it fired now;
    ok=True with a "queued" note when it parked (the watch retries);
    ok=False only when an active compaction already owns the pair.
    """
    child = app.get("bot_child") if hasattr(app, "get") else app["bot_child"]
    if child is not None and child.status().get("state") in ("starting", "running"):
        return {"ok": False, "note": "the voice bot is running — stop it first"}

    qdir = queue_dir()
    qdir.mkdir(parents=True, exist_ok=True)
    base = f"{character}.{session}"
    running = qdir / f"{base}.running"
    if running.exists():
        held = maintenance_lock.probe(character)
        if held is not None:
            return {"ok": False,
                    "note": f"already compacting ({maintenance_lock.describe(held)})"}
        # dead claim — the manual click supersedes it
        running.unlink(missing_ok=True)
    (qdir / f"{base}.failed").unlink(missing_ok=True)  # human retry re-arms

    payload = {"character": character, "session": session, "source": "manual",
               "requested": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())}
    (qdir / f"{base}.request").write_text(json.dumps(payload, indent=1),
                                          encoding="utf-8")
    note = await tick(app)
    if note and note.startswith("started"):
        return {"ok": True, "note": note}
    return {"ok": True,
            "note": "queued — runs once no compaction is active and the RAM "
                    "floor holds; the watch retries and the status line shows "
                    "progress"}


async def _loop(app) -> None:
    while True:
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the watch must outlive a bad pass
            logger.warning("[compact-watch] tick failed ({})", type(exc).__name__)
        await asyncio.sleep(INTERVAL_S)


async def start(app) -> None:
    app["compact_watch"] = asyncio.get_running_loop().create_task(_loop(app))


async def stop(app) -> None:
    task = app.get("compact_watch")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
