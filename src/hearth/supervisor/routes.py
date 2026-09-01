"""supervisor/routes.py — the /admin surface + the panel reverse-proxy.

Mounted into the standalone facade app by serve/__main__.py iff
[serve.supervisor] enabled = true (ADR 007 stroke 1). Every route rides the
facade's existing bearer middleware (D7: one door; X-03 strict — header auth
only this stroke; browser-friendly auth is a named later refinement).
Responses carry names, states, and booleans only — never tokens, env values,
or file contents (POL-GL-039 posture, as `hearth.config.check` prints keys).

The catch-all proxy is registered LAST so every real facade route wins; any
other path forwards to the bot's control panel when the bot is up, and answers
an honest "offline — start me" when it is down.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import aiohttp
from aiohttp import web
from loguru import logger

from .child import STOP_GRACE_S, TERM_GRACE_S, BotChild

PANEL_URL = "http://127.0.0.1:65000"

# Never forwarded to the loopback panel: the bearer stays at the one door.
_DROP_HEADERS = {"Host", "Authorization", "Content-Length", "Transfer-Encoding", "Connection"}

_OFFLINE_PAGE = """<!doctype html><meta charset="utf-8">
<title>Hearth — offline</title>
<body style="font-family: system-ui; max-width: 34em; margin: 4em auto; line-height: 1.5">
<h1>Hearth is resting</h1>
<p>The voice bot is not running. Start it (bearer required):</p>
<pre>POST /admin/bot/start   {"mode": "new"}   # or "resume"</pre>
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
        app.router.add_get("/admin/state", _state)
        app.router.add_post("/admin/bot/start", _bot_start)
        app.router.add_post("/admin/bot/stop", _bot_stop)
        app.router.add_post("/admin/daemon/restart", _daemon_restart)
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
    llm = await _http_alive(deps.session, deps.lm_base_url.rstrip("/") + "/models",
                            headers={"Authorization": f"Bearer {deps.lm_token}"})
    audio = await _http_alive(deps.session, str(deps.cfg.get("audio_base_url") or ""))
    panel = await _http_alive(deps.session, app["panel_url"] + "/engine")
    return web.json_response({
        "supervisor": True,
        "bot": app["bot_child"].status(),
        "panel": {"url": app["panel_url"], "reachable": panel},
        "externals": {"llm": llm, "audio": audio},  # watched, never owned (ADR 007 §3)
    })


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
