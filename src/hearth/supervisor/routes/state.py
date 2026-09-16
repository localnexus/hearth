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
An actuator declared with guard = "companion" is refused (409, with the
guard named) while a companion is running, unless the press says ?force=1 —
a live session owns its model's residency, so freeing it is a confirmed act.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiohttp
from aiohttp import web
from loguru import logger

from hearth.session import close_phase, maintenance_lock

from .. import actuators as actuators_mod
from .. import compact_watch
from .. import firstrun


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


async def _route_of(session, panel_url: str):
    """The live sitting's audio route, read from the bot's own ``/route``.

    The bot is the only thing that knows it — the transport that holds the
    route lives in that process — so the facade fetches it the way it probes
    everything else it does not own. ``None`` whenever the answer is not a
    route object: no companion, an older build without the route, a timeout.
    """
    if session is None or not panel_url:
        return None
    try:
        async with session.get(panel_url + "/route",
                               timeout=aiohttp.ClientTimeout(total=2)) as r:
            if r.status != 200:
                return None
            doc = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("kind") else None


async def _state(request: web.Request) -> web.Response:
    app = request.app
    # One access-count line per minute for this route: names and numbers
    # only, never the client address or a header.
    hits = app.setdefault("state_hits", {"n": 0, "since": time.monotonic()})
    hits["n"] += 1
    if time.monotonic() - hits["since"] >= 60.0:
        logger.info("[supervisor] /admin/state: {} requests in the last {} s",
                    hits["n"], int(time.monotonic() - hits["since"]))
        hits["n"] = 0
        hits["since"] = time.monotonic()
    deps = app["deps"]
    # Watched, never owned: the built-ins plus every declared
    # [serve.supervisor.watch.<name>] URL, probed concurrently. A declared
    # name never shadows a built-in.
    probes = {
        "llm": _http_alive(deps.session, deps.lm_base_url.rstrip("/") + "/models",
                           headers={"Authorization": f"Bearer {deps.lm_token}"}),
        "audio": _http_alive(deps.session, str(deps.cfg.get("audio_base_url") or "")),
        "panel": _http_alive(deps.session, app["panel_url"] + "/engine"),
        # Two file facts (firstrun/detect.py): a built-in, so a declared watch
        # can never shadow it.
        "first_run": asyncio.to_thread(firstrun.detect),
    }
    for name, url in app.get("watches", {}).items():
        probes.setdefault(name, _http_alive(deps.session, url))
    results = dict(zip(probes, await asyncio.gather(*probes.values())))
    panel = results.pop("panel")
    first_run = results.pop("first_run")
    # Process truth, not cached truth: a desk-started bot appears (adopted) and
    # a desk-stopped adopted bot disappears within one poll of the launch page.
    await app["bot_child"].reconcile()
    # The close ladder's breadcrumb, pid-matched so a stale file from an
    # earlier close is never mistaken for the current one; null unless the
    # current child wrote one.
    close = await asyncio.to_thread(close_phase.read, pid=app["bot_child"].pid)
    # Where this sitting listens and speaks. Fetched rather than mirrored: the
    # taps that hold it are in the bot, and a phase-A change should not stand
    # up a second mirror to carry seven fields the bot can already answer.
    bot_status = app["bot_child"].status()
    if bot_status.get("state") in ("starting", "running"):
        bot_status["route"] = await _route_of(deps.session, app["panel_url"])
    else:
        bot_status["route"] = None
    return web.json_response({
        "supervisor": True,
        # What would relaunch the facade after /admin/daemon/restart; None on a
        # terminal run — the launch page draws its Restart button from this.
        "keeper": app.get("keeper"),
        "bot": bot_status,
        "close": close,
        "panel": {"url": app["panel_url"], "reachable": panel},
        "externals": results,
        "switch": app["switch_state"]["last"],
        "actuators": app["actuators"].names(),  # names only; details on /admin/actuators
        # Held session-maintenance locks (op/character/session/started — names
        # only): the launch page renders in-progress compactions from this.
        "maintenance": maintenance_lock.held_locks(),
        # The compaction queue (names and states only). The lock above covers
        # a compaction in flight; this covers the ones that are parked, and
        # the ones that FAILED — which nothing else on this page can show.
        "compact_queue": compact_watch.queue_status(),
        # The first-run entry condition: {needs_model, fresh}, or null when
        # the tree is too broken to say. The launch page offers the walk on
        # either and parks Start on the first.
        "first_run": first_run,
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
    if acts.guard(name) == "companion" and request.query.get("force") != "1":
        # Process truth first: a desk-started companion counts too.
        child = request.app["bot_child"]
        await child.reconcile()
        if child.status().get("state") in ("starting", "running"):
            return web.json_response(
                {"error": f"{name} is held while a companion is running — "
                          "the next turn would pay for what it frees; "
                          "press again to confirm",
                 "guard": "companion", "companion": child.status().get("state")},
                status=409)
    try:
        record = await acts.run(name)
    except actuators_mod.ActuatorBusy:
        return web.json_response({"error": f"{name} is already running"}, status=409)
    return web.json_response({"name": name, **record})
