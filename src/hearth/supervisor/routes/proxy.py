"""routes/proxy.py — the catch-all forward to the panel, and the page shown
when it is down.

Registered LAST in build_mount, always: every real facade route wins, and
whatever is left over is the bot's own control panel at :65000. That ordering
is the whole design — this file has no idea which paths belong to whom.

The bearer stops here. _DROP_HEADERS strips Authorization and Cookie off every
forwarded request: the loopback panel is not behind the door, and it must
never be handed the key to it.

A dead upstream is not an error to a person who typed the address, so GET /
answers the offline page and every other path answers 503 with what to press.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio

import aiohttp
from aiohttp import web

PANEL_URL = "http://127.0.0.1:65000"

# Never forwarded to the loopback panel: the bearer stays at the one door.
_DROP_HEADERS = {"Host", "Authorization", "Cookie", "Content-Length",
                 "Transfer-Encoding", "Connection"}

_OFFLINE_PAGE = """<!doctype html><meta charset="utf-8">
<title>Hearth — offline</title>
<body style="font-family: system-ui; max-width: 34em; margin: 4em auto; line-height: 1.5">
<h1>Hearth is resting</h1>
<p>The voice bot is not running.
<a href="/admin/launch">Open the launch page</a> to start it.</p>
<p>API: <code>POST /admin/bot/start {"mode": "new"}</code> · state:
<code>GET /admin/state</code></p></body>"""


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
