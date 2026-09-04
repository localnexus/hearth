"""routes/lifecycle.py — the bot's start and stop, compaction, and the daemon's
own restart.

The bot routes are thin on purpose: BotChild owns the graceful ladder and the
adopt-don't-collide rule, and these hand it arguments. The one piece of policy
that lives here is the start-door guard — a compaction holding a maintenance
lock answers 409 with the holder rather than racing it. Advisory only; the
bot's own lock acquire at startup is the arbiter.

/admin/compact validates and hands to compact_watch: a human click clears a
.failed breadcrumb and fires when the stage is free, otherwise it queues.

The daemon restart is a deliberate unsuccessful exit — launchd's KeepAlive
relaunches the daemon while the bot child, in its own process group, survives
and is re-adopted. Under a plain terminal run it simply exits, documented.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import os

from aiohttp import web
from loguru import logger

from hearth.session import maintenance_lock

from .. import compact_watch
from .. import switch as switch_mod


async def _bot_start(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # empty body = defaults
        body = {}
    # Start-door guard (design: auto-compaction-on-close): while any
    # compaction holds a maintenance lock, refuse with the holder's info.
    # Advisory UX — the bot's own lock acquire at startup is the arbiter.
    busy = maintenance_lock.held_locks(op="compact")
    if busy:
        return web.json_response({
            "ok": False,
            "error": (f"{maintenance_lock.describe(busy[0])} "
                      f"({busy[0].get('character', '?')}) — "
                      "try again in a few minutes"),
            "maintenance": busy,
        }, status=409)
    result = await request.app["bot_child"].start(
        mode=str(body.get("mode") or "new"),
        name=(str(body["name"]) if body.get("name") else None),
        memory=(str(body["memory"]) if body.get("memory") else None),
    )
    return web.json_response(result, status=200 if result.get("ok") else 409)


async def _bot_stop(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await request.app["bot_child"].stop(
        hold=bool(body.get("hold")),
        name=(str(body["name"]) if body.get("name") else None),
    )
    return web.json_response(result, status=200 if result.get("ok") else 500)


async def _compact_start(request: web.Request) -> web.Response:
    """POST /admin/compact {character, session}: manual compaction initiation
    (design: auto-compaction-on-close, the :65001 knob). Validates, then
    hands to compact_watch.submit — a human click clears a .failed breadcrumb
    and fires immediately when the stage is free, else queues for the watch."""
    from hearth.session import session_store  # lazy: mirrors the package gate idiom
    import re

    try:
        body = await request.json()
    except Exception:
        body = {}
    character = str(body.get("character") or "")
    session = str(body.get("session") or "").removesuffix(".json")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", character or "") or \
            not re.fullmatch(r"[A-Za-z0-9._-]+", session or ""):
        return web.json_response({"ok": False, "error": "character and session required"},
                                 status=400)
    known = {c["name"] for c in switch_mod.choices()["characters"]}
    if character not in known:
        return web.json_response({"ok": False, "error": f"unknown character {character!r}"},
                                 status=404)
    sdir = session_store.companion_sessions_dir(character)
    if not (sdir / f"{session}.json").is_file():
        return web.json_response({"ok": False, "error": f"no session {session!r} for {character}"},
                                 status=404)
    result = await compact_watch.submit(request.app, character, session)
    return web.json_response(result, status=200 if result.get("ok") else 409)


async def _daemon_restart(request: web.Request) -> web.Response:
    # Deliberate unsuccessful exit: launchd KeepAlive (on-failure) relaunches the
    # daemon; the bot child survives in its own process group and is re-adopted.
    # Under a plain terminal run this simply exits — documented behavior.
    logger.info("[supervisor] daemon restart requested — exiting for the keeper")
    asyncio.get_running_loop().call_later(0.3, os._exit, 3)
    return web.json_response({"ok": True, "restarting": True})
