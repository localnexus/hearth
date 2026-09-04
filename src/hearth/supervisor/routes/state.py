"""routes/state.py — what the daemon can see, and the actuators it can fire.

Everything here is WATCHED, never owned: the LLM server, the audio engine, the
panel and every declared [serve.supervisor.watch.<name>] URL are probed
concurrently and reported, and a declared name never shadows a built-in. Any
HTTP answer at all counts as alive — a 404 still proves a process is there.

/admin/state reports process truth rather than cached truth: it reconciles the
bot child first, so a bot started or stopped at the desk shows up within one
poll of the launch page.

Actuators are the one thing the daemon can push. They are operator-declared,
fixed-argv, bounded, and never children of this process; the responses carry
their names and records, never their commands and never their output.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp
from aiohttp import web

from hearth.session import maintenance_lock

from .. import actuators as actuators_mod


async def _http_alive(session, url: str, headers: Optional[dict] = None):
    """True/False reachability; None when no probe session exists (tests)."""
    if session is None or not url:
        return None
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=2)) as r:
            await r.read()
            return True  # ANY http answer = the process is there (404 included)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return False


async def _state(request: web.Request) -> web.Response:
    app = request.app
    deps = app["deps"]
    # Watched, never owned: the built-ins plus every declared
    # [serve.supervisor.watch.<name>] URL, probed concurrently. A declared
    # name never shadows a built-in.
    probes = {
        "llm": _http_alive(deps.session, deps.lm_base_url.rstrip("/") + "/models",
                           headers={"Authorization": f"Bearer {deps.lm_token}"}),
        "audio": _http_alive(deps.session, str(deps.cfg.get("audio_base_url") or "")),
        "panel": _http_alive(deps.session, app["panel_url"] + "/engine"),
    }
    for name, url in app.get("watches", {}).items():
        probes.setdefault(name, _http_alive(deps.session, url))
    results = dict(zip(probes, await asyncio.gather(*probes.values())))
    panel = results.pop("panel")
    # Process truth, not cached truth: a desk-started bot appears (adopted) and
    # a desk-stopped adopted bot disappears within one poll of the launch page.
    await app["bot_child"].reconcile()
    return web.json_response({
        "supervisor": True,
        "bot": app["bot_child"].status(),
        "panel": {"url": app["panel_url"], "reachable": panel},
        "externals": results,
        "switch": app["switch_state"]["last"],
        "actuators": app["actuators"].names(),  # names only; details on /admin/actuators
        # Held session-maintenance locks (op/character/session/started — names
        # only): the launch page renders in-progress compactions from this.
        "maintenance": maintenance_lock.held_locks(),
    })


async def _actuators_get(request: web.Request) -> web.Response:
    """The declared actuators: note/running/last record, plus a reachability
    probe for those that declare one. Never commands, never output."""
    app = request.app
    acts = app["actuators"]
    out = acts.status()
    urls = acts.probe_urls()
    if urls:
        alive = await asyncio.gather(*(
            _http_alive(app["deps"].session, url) for url in urls.values()))
        for name, up in zip(urls, alive):
            out[name]["probe"] = up
    return web.json_response({"actuators": out})


async def _actuator_run(request: web.Request) -> web.Response:
    """Run one declared actuator, bounded; the honest record comes back when
    it finishes (a slow bring-up holds the request — that IS the spinner)."""
    name = request.match_info["name"]
    acts = request.app["actuators"]
    if name not in acts:
        return web.json_response({"error": f"unknown actuator {name!r}"}, status=404)
    try:
        record = await acts.run(name)
    except actuators_mod.ActuatorBusy:
        return web.json_response({"error": f"{name} is already running"}, status=409)
    return web.json_response({"name": name, **record})
