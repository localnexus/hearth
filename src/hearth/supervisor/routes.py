"""supervisor/routes.py — the /admin surface + the panel reverse-proxy.

Mounted into the standalone facade app by serve/__main__.py iff
[serve.supervisor] enabled = true. Every route rides the facade's existing
bearer middleware (one door; header auth only for now — browser-friendly auth
is a named later refinement). Responses carry names, states, and booleans
only — never tokens, env values, or file contents (the same posture as
`hearth.config.check`, which prints keys and not values).

The catch-all proxy is registered LAST so every real facade route wins; any
other path forwards to the bot's control panel when the bot is up, and answers
an honest "offline — start me" when it is down.

/admin/switch is switch-companion as ONE
action: a registry-validated active.toml write + a supervised warm restart
(the mechanics live in switch.py; the restart runs as a background task so
the response returns before the SIGINT lands on the bot).

The router then routes each switch: a registry-consulted
(switch.live_capable_fields) LIVE handoff to the bot's /switch/live intent
slot when the bot is up and every changed field has a live path — the reply
then says applied: "live" and the bot swaps at its next turn boundary —
falling back to the supervised restart otherwise. The optional body key
"apply" steers it: "auto" (default) | "live" (live or 409, never restarts) |
"restart" (force the supervised-restart path).

The operator can also declare watched externals and actuators:
[serve.supervisor.watch.<name>] URLs join /admin/state's
externals, and [serve.supervisor.actuators.<name>] commands — operator-fixed
argv, bounded, output to log files, never children — run via
POST /admin/actuators/<name>/run (GET /admin/actuators lists them). Warm stop
stays the default everywhere; a cold model stop happens only as a declared,
deliberately pressed actuator (§4).
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import aiohttp
from aiohttp import web
from loguru import logger

from .child import STOP_GRACE_S, TERM_GRACE_S, BotChild, _now_iso
from . import actuators as actuators_mod
from . import switch as switch_mod

PANEL_URL = "http://127.0.0.1:65000"

_FACADE_NOTE = ("untouched — a [serve.identity] pin keeps its own voice; unpinned "
                "LLM-leg params follow at the next facade restart")

# Never forwarded to the loopback panel: the bearer stays at the one door.
_DROP_HEADERS = {"Host", "Authorization", "Content-Length", "Transfer-Encoding", "Connection"}

_OFFLINE_PAGE = """<!doctype html><meta charset="utf-8">
<title>Hearth — offline</title>
<body style="font-family: system-ui; max-width: 34em; margin: 4em auto; line-height: 1.5">
<h1>Hearth is resting</h1>
<p>The voice bot is not running. Start it (bearer required):</p>
<pre>POST /admin/bot/start   {"mode": "new"}   # or "resume"
POST /admin/switch      {"character": "…"}  # switch companion: writes active.toml + restarts</pre>
<p>State: <code>GET /admin/state</code></p></body>"""


def build_mount(sup_cfg: dict):
    """→ mount(app) for serve_app.start(..., mount=...). Reads [serve.supervisor]."""

    def mount(app: web.Application) -> None:
        deps = app["deps"]
        overlay = {"LM_BASE_URL": deps.lm_base_url}
        if deps.lm_token and deps.lm_token != "lm-studio":
            overlay["LM_API_TOKEN"] = deps.lm_token
        overlay.update({str(k): str(v) for k, v in dict(sup_cfg.get("env") or {}).items()})

        from hearth.config import config_loader  # lazy: mirror the package gate idiom

        child = BotChild(
            env_overlay=overlay,
            log_path=config_loader.DATA_DIR / "logs" / "bot.log",
            stop_grace_s=float(sup_cfg.get("stop_grace_s", STOP_GRACE_S)),
            term_grace_s=float(sup_cfg.get("term_grace_s", TERM_GRACE_S)),
        )
        app["bot_child"] = child
        app["panel_url"] = str(sup_cfg.get("panel_url") or PANEL_URL).rstrip("/")
        # Stroke 4: watched externals + declared actuators (never children).
        app["watches"] = {str(n): str(dict(w or {}).get("url") or "")
                          for n, w in dict(sup_cfg.get("watch") or {}).items()}
        app["actuators"] = actuators_mod.ActuatorSet(
            dict(sup_cfg.get("actuators") or {}),
            log_dir=config_loader.DATA_DIR / "logs" / "actuators",
        )
        app.router.add_get("/admin/state", _state)
        app.router.add_post("/admin/bot/start", _bot_start)
        app.router.add_post("/admin/bot/stop", _bot_stop)
        app.router.add_post("/admin/daemon/restart", _daemon_restart)
        # Mutated IN PLACE at runtime (the app mapping is frozen after startup):
        # last = the most recent switch-intent's phase/outcome; task = the
        # in-flight supervised restart (one at a time).
        app["switch_state"] = {"last": None, "task": None}
        app.router.add_get("/admin/switch", _switch_get)
        app.router.add_post("/admin/switch", _switch_post)
        app.router.add_get("/admin/actuators", _actuators_get)
        app.router.add_post("/admin/actuators/{name}/run", _actuator_run)
        # LAST on purpose: registered facade routes always win over the proxy.
        app.router.add_route("*", "/{tail:.*}", _panel_proxy)
        app.on_startup.append(_adopt_on_start)
        app.on_cleanup.append(_release)
        logger.info("[supervisor] daemon face mounted (panel {})", app["panel_url"])

    return mount


async def _adopt_on_start(app: web.Application) -> None:
    # Adopt-don't-collide: a bot that predates (or outlived) this daemon is
    # reported, never killed or duplicated.
    await app["bot_child"].adopt()


async def _release(app: web.Application) -> None:
    # Daemon shutdown ABANDONS the child by design (own process group): a
    # daemon restart must never cost a live conversation. Re-adopted on start.
    app["bot_child"].close()


# ── handlers (all behind the facade bearer middleware) ────────────────────────

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
    return web.json_response({
        "supervisor": True,
        "bot": app["bot_child"].status(),
        "panel": {"url": app["panel_url"], "reachable": panel},
        "externals": results,
        "switch": app["switch_state"]["last"],
        "actuators": app["actuators"].names(),  # names only; details on /admin/actuators
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


async def _bot_start(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # empty body = defaults
        body = {}
    result = await request.app["bot_child"].start(
        mode=str(body.get("mode") or "new"),
        name=(str(body["name"]) if body.get("name") else None),
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


async def _daemon_restart(request: web.Request) -> web.Response:
    # Deliberate unsuccessful exit: launchd KeepAlive (on-failure) relaunches the
    # daemon; the bot child survives in its own process group and is re-adopted.
    # Under a plain terminal run this simply exits — documented behavior.
    logger.info("[supervisor] daemon restart requested — exiting for the keeper")
    asyncio.get_running_loop().call_later(0.3, os._exit, 3)
    return web.json_response({"ok": True, "restarting": True})


# ── switch-companion, one action ─────────────────────────────────────────────

async def _switch_get(request: web.Request) -> web.Response:
    """Current selection + what the picker can offer. Names only, never values."""
    app = request.app
    deps = app["deps"]
    current, err = switch_mod.read_selection()
    sel = ({k: current[k] for k in switch_mod.SELECTION_KEYS if k in current}
           if current else None)
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
    running = child.state in ("starting", "running") or await child.adopt()
    # Stroke 3 routing: the registry consult. Live only when the bot is up and
    # every CHANGED field declares a live path; "apply" steers ("auto" default).
    apply_mode = str(body.get("apply") or "auto").lower()
    changed = switch_mod.changed_fields(wrote["previous"], merged)
    live_eligible = bool(changed) and all(
        k in switch_mod.live_capable_fields() for k in changed)
    live_result = None
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


async def _do_restart(app: web.Application, *, hold, hold_name, mode, name) -> None:
    """stop (graceful ladder — the bot's own finalize/hold path runs) → start."""
    child = app["bot_child"]
    status = app["switch_state"]["last"]
    stopped = await child.stop(hold=hold, name=hold_name)
    if not stopped.get("ok"):
        status.update(phase="failed", error=stopped.get("error") or "stop failed",
                      at=_now_iso())
        return
    started = await child.start(mode=mode, name=name)
    if started.get("ok"):
        status.update(phase="done", error=None, at=_now_iso(), pid=started.get("pid"))
    else:
        status.update(phase="failed", error=started.get("error") or "start failed",
                      at=_now_iso())


async def _panel_proxy(request: web.Request) -> web.Response:
    app = request.app
    deps = app["deps"]
    url = app["panel_url"] + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k not in _DROP_HEADERS}
    body = await request.read()
    try:
        async with deps.session.request(
            request.method, url, headers=headers, data=body,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as upstream:
            payload = await upstream.read()
            return web.Response(body=payload, status=upstream.status,
                                content_type=upstream.content_type,
                                charset=upstream.charset)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        if request.path == "/" and request.method == "GET":
            return web.Response(text=_OFFLINE_PAGE, content_type="text/html")
        return web.json_response(
            {"error": "voice bot offline", "bot": app["bot_child"].status(),
             "hint": "POST /admin/bot/start"},
            status=503,
        )
