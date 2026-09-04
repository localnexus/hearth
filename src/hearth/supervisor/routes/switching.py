"""routes/switching.py — switch-companion as one action: live handoff, or
supervised restart.

One POST does the whole thing: a registry-validated active.toml write, then
whichever way of applying it the situation allows. The routing is a registry
consult, not a guess — a LIVE handoff to the bot's /switch/live intent slot
when the bot is up and every CHANGED field declares a live path, and the
supervised restart otherwise. The body key "apply" steers it: "auto"
(default) | "live" (live or 409, never restarts) | "restart".

Order matters here and is the reason the whole verb is one file: validation
failures write nothing (400); the write happens before either path; the
restart is scheduled rather than awaited, so the response returns before the
SIGINT lands — a relay running inside the bot still gets a clean answer.

A memory-mode rider forces the restart path. The mode is the SITTING's
posture: it is set at boot and rides a live switch unchanged, so only a fresh
spawn can honor a different one.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio

import aiohttp
from aiohttp import web
from loguru import logger

from ..child import _MEMORY_MODES, _now_iso
from .. import switch as switch_mod

_FACADE_NOTE = ("untouched — a [serve.identity] pin keeps its own voice; unpinned "
                "LLM-leg params follow at the next facade restart")


async def _switch_get(request: web.Request) -> web.Response:
    """Current selection + what the picker can offer. Names only, never values."""
    app = request.app
    deps = app["deps"]
    current, err = switch_mod.read_selection()
    sel = ({k: current[k] for k in switch_mod.SELECTION_KEYS if k in current}
           if current else None)
    await app["bot_child"].reconcile()  # the page gates "live vs restart" on this
    return web.json_response({
        "supervisor": True,
        "current": sel,
        "current_error": err,
        "choices": switch_mod.choices(),
        "bot": app["bot_child"].status(),
        "switch": app["switch_state"]["last"],
        "facade": {"identity_pinned": bool(dict(deps.cfg).get("identity")),
                   "character": deps.character},
    })


async def _switch_live_get(request: web.Request) -> web.Response:
    """Read-through to the bot's own GET /switch/live (the switcher's second half).

    The shared switch card needs two things the daemon cannot know by itself:
    which models the LLM server holds RIGHT NOW (the ● marks — residency is what
    decides live-vs-restart on a model change) and the moment a live handoff
    actually lands (the bot applies it at the next turn boundary, not on POST).
    Both live in the bot's describe(); the panel reads them directly, and this
    is the same window for every client that can only reach the facade.

    Never an error: a down or older bot answers {"ok": false, "reason": ...} so
    the card degrades to plain names instead of breaking. Names and states only.
    """
    app = request.app
    deps = app["deps"]
    if not await app["bot_child"].reconcile():
        return web.json_response({"ok": False, "reason": "bot is down"})
    if deps.session is None:
        return web.json_response({"ok": False, "reason": "no probe session"})
    try:
        async with deps.session.get(
                app["panel_url"] + "/switch/live",
                timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 404:
                return web.json_response({"ok": False, "reason": "bot has no live-switch route"})
            try:
                data = await r.json()
            except Exception:  # noqa: BLE001 — a non-JSON body is not an answer
                return web.json_response({"ok": False, "reason": f"bot answered HTTP {r.status}"})
            if not isinstance(data, dict):
                return web.json_response({"ok": False, "reason": "malformed bot response"})
            data.setdefault("ok", r.status == 200)
            return web.json_response(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        return web.json_response({"ok": False, "reason": f"unreachable ({type(exc).__name__})"})


async def _switch_post(request: web.Request) -> web.Response:
    """Registry-validated active.toml write + supervised restart (background).

    Validation failures write NOTHING (400). The restart is scheduled, not
    awaited — the response returns before the SIGINT lands, so a relay running
    inside the bot itself still gets a clean answer. Watch GET /admin/state.
    """
    app = request.app
    try:
        body = await request.json()
    except Exception:  # empty body = no-op merge (revalidate + rewrite current)
        body = {}
    state = app["switch_state"]
    if state["task"] is not None and not state["task"].done():
        return web.json_response({"ok": False, "error": "a switch is already in progress",
                                  "switch": state["last"]}, status=409)
    # The optional memory-mode rider: validated HERE so a bad value writes
    # nothing and never stops the bot only to fail the restart.
    memory = str(body["memory"]) if body.get("memory") else None
    if memory is not None and memory not in _MEMORY_MODES:
        return web.json_response(
            {"ok": False,
             "errors": [f"unknown memory mode {memory!r} (full | recall-only | off)"]},
            status=400)
    current, cur_err = switch_mod.read_selection()
    if cur_err:
        return web.json_response({"ok": False, "errors": [cur_err]}, status=409)
    merged = switch_mod.merge_selection(current, body)
    errors = switch_mod.validate_selection(merged)
    if errors:
        return web.json_response({"ok": False, "errors": errors}, status=400)
    try:
        wrote = switch_mod.write_selection(merged)
    except (ValueError, OSError) as exc:
        return web.json_response({"ok": False, "errors": [str(exc)]}, status=409)
    child = app["bot_child"]
    # reconcile, not adopt: a stale "running" (adopted bot stopped at the desk)
    # would otherwise route a live-apply at a dead pid.
    running = await child.reconcile()
    # Stroke 3 routing: the registry consult. Live only when the bot is up and
    # every CHANGED field declares a live path; "apply" steers ("auto" default).
    apply_mode = str(body.get("apply") or "auto").lower()
    changed = switch_mod.changed_fields(wrote["previous"], merged)
    # A memory-mode rider forces the restart path: the mode is the SITTING's
    # posture (set at boot, rides a live switch unchanged) — only a fresh spawn
    # can honor a different one.
    live_eligible = bool(changed) and memory is None and all(
        k in switch_mod.live_capable_fields() for k in changed)
    live_result = None
    if apply_mode == "live" and memory is not None:
        return web.json_response(
            {"ok": False,
             "errors": ["a memory-mode change cannot ride a live switch — "
                        'it needs a restart (repost with "apply": "auto")'],
             "wrote": merged}, status=409)
    if apply_mode == "live" and not running:
        return web.json_response(
            {"ok": False, "errors": ['apply "live" needs a running bot'],
             "wrote": merged}, status=409)
    if running and apply_mode != "restart" and (live_eligible or apply_mode == "live"):
        live_result = await _try_live(app, merged, body)
        if live_result.get("ok"):
            state["last"] = {"phase": "live", "to": merged, "at": _now_iso(),
                             "error": None}
            logger.info("[supervisor] switch → {} (live handoff)",
                        {k: merged[k] for k in switch_mod.SELECTION_KEYS})
            return web.json_response({
                "ok": True, "wrote": merged, "previous": wrote["previous"],
                "kept_extras": wrote["extras"], "applied": "live",
                "live": {k: live_result.get(k) for k in
                         ("changed", "warnings", "applies") if k in live_result},
                "facade": _FACADE_NOTE,
            })
        if apply_mode == "live":
            state["last"] = {"phase": "live-refused", "to": merged, "at": _now_iso(),
                             "error": "; ".join(live_result.get("errors") or ["refused"])}
            return web.json_response(
                {"ok": False, "errors": live_result.get("errors") or ["live arm refused"],
                 "wrote": merged,
                 "hint": 'the selection IS written — repost with "apply": "auto" '
                         "for the restart path"}, status=409)
    restart = running or bool(body.get("start"))
    if restart:
        state["last"] = {"phase": "restarting", "to": merged,
                         "at": _now_iso(), "error": None}
        state["task"] = asyncio.get_running_loop().create_task(_do_restart(
            app,
            hold=bool(body.get("hold")),
            hold_name=(str(body["hold_name"]) if body.get("hold_name") else None),
            mode=str(body.get("mode") or "new"),
            name=(str(body["name"]) if body.get("name") else None),
            memory=memory,
        ))
    logger.info("[supervisor] switch → {} (restart: {})",
                {k: merged[k] for k in switch_mod.SELECTION_KEYS}, restart)
    resp = {
        "ok": True, "wrote": merged, "previous": wrote["previous"],
        "kept_extras": wrote["extras"],
        "applied": "restart" if restart else "none",
        "restart": ("scheduled — watch GET /admin/state" if restart else
                    'not scheduled — bot not running (pass "start": true to launch)'),
        "facade": _FACADE_NOTE,
    }
    if live_result is not None:
        resp["live_refused"] = live_result.get("errors") or ["live handoff failed"]
    return web.json_response(resp)


async def _try_live(app: web.Application, merged: dict, body: dict) -> dict:
    """Hand the bundle to the bot's /switch/live intent slot. → the bot's own
    response dict ({"ok": False, "errors": [...]} on any transport failure or
    an older bot without the route). The generous wait covers a cold memory-
    sidecar spin — the arm PREPARES the new companion's recall eagerly."""
    deps = app["deps"]
    if deps.session is None:
        return {"ok": False, "errors": ["no probe session — cannot reach the bot"]}
    payload = {k: merged[k] for k in switch_mod.SELECTION_KEYS}
    for key in ("hold", "hold_name", "mode", "name"):
        if body.get(key):
            payload[key] = body[key]
    try:
        async with deps.session.post(
                app["panel_url"] + "/switch/live", json=payload,
                timeout=aiohttp.ClientTimeout(total=40)) as r:
            if r.status == 404:
                return {"ok": False,
                        "errors": ["bot has no live-switch route (older build)"]}
            try:
                data = await r.json()
            except Exception:  # noqa: BLE001 — a non-JSON body is a refusal
                return {"ok": False, "errors": [f"bot answered HTTP {r.status}"]}
            if not isinstance(data, dict):
                return {"ok": False, "errors": ["malformed bot response"]}
            data.setdefault("ok", r.status == 200)
            return data
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        return {"ok": False, "errors": [f"live handoff failed ({type(exc).__name__})"]}


async def _do_restart(app: web.Application, *, hold, hold_name, mode, name,
                      memory=None) -> None:
    """stop (graceful ladder — the bot's own finalize/hold path runs) → start."""
    child = app["bot_child"]
    status = app["switch_state"]["last"]
    stopped = await child.stop(hold=hold, name=hold_name)
    if not stopped.get("ok"):
        status.update(phase="failed", error=stopped.get("error") or "stop failed",
                      at=_now_iso())
        return
    started = await child.start(mode=mode, name=name, memory=memory)
    if started.get("ok"):
        status.update(phase="done", error=None, at=_now_iso(), pid=started.get("pid"))
    else:
        status.update(phase="failed", error=started.get("error") or "start failed",
                      at=_now_iso())
