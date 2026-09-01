"""features/companion.py — the panel's companion switcher (relay to the daemon).

DROP-IN (the panel-extension seam): bot.py imports this module; registration is
the import side effect, control.py takes zero edits. The switch itself runs in
the SUPERVISOR DAEMON (ADR 007 stroke 2 — one door, one owner of the restart):
these routes only RELAY the panel's clicks to the standalone facade's /admin
surface, presenting the facade bearer server-side so the browser never holds it.

Registration self-gates to the safe topology, otherwise the contributor
returns an EMPTY route table and the page's switcher section stays hidden:
  • config/serve.toml [serve] enabled AND [serve.supervisor] enabled = true
    (no daemon configured ⇒ nothing to relay to), and
  • the panel binds loopback (WEB_HOST default) — the relay must never widen a
    LAN-exposed panel into an unauthenticated control door (X-03: non-localhost
    surfaces present the bearer at :65001 themselves).

Secrets (POL-GL-039): the bearer is resolved from serve.toml's token_source
PATH (or SERVE_TOKEN env) via the facade's own resolver, held in a closure,
and never logged, echoed, or returned in any response.

API (mirrors the daemon, names only):
    GET  /companion         → GET  <daemon>/admin/switch   (current + choices + bot state)
    POST /companion/switch  → POST <daemon>/admin/switch   (allowlisted body keys;
                              "apply" steers live vs restart — ADR 007 stroke 3)
Daemon down ⇒ 502 {"error": "supervisor unreachable"} — the page hides itself.
"""

from __future__ import annotations

import os

import aiohttp
from aiohttp import web
from loguru import logger

from hearth.config import config_loader
from hearth.control.control_routes import PanelContext, register

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_BODY_KEYS = ("character", "model", "voice", "persona",
              "hold", "hold_name", "mode", "name", "start", "apply")


def _daemon() -> tuple:
    """→ (base_url, bearer) when the relay may register, else (None, None)."""
    try:
        cfg = config_loader.load_serve_config()
    except config_loader.ConfigError as exc:
        logger.warning("[companion] serve.toml unreadable ({}) — switcher inert",
                       type(exc).__name__)
        return None, None
    if not cfg or not dict(cfg.get("supervisor") or {}).get("enabled"):
        return None, None
    if os.environ.get("WEB_HOST", "127.0.0.1") not in _LOOPBACK:
        logger.info("[companion] panel is LAN-exposed — switcher inert "
                    "(use the daemon's authed /admin/switch directly)")
        return None, None
    from hearth.serve.app import _resolve_bearer  # lazy; value stays in-closure

    try:
        bearer = _resolve_bearer(cfg)
    except config_loader.ConfigError as exc:
        logger.warning("[companion] bearer unresolvable ({}) — switcher inert",
                       type(exc).__name__)
        return None, None
    return f"http://127.0.0.1:{int(cfg['port'])}", bearer


async def _relay(method: str, url: str, bearer: str, payload=None) -> web.Response:
    try:
        # POST waits out a LIVE arm: the daemon's handoff prepares the new
        # companion's recall eagerly (a cold memory sidecar can take ~15 s) and
        # itself waits 40 s — stay above that so the refusal reason reaches us.
        timeout = aiohttp.ClientTimeout(total=45 if method == "POST" else 5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, json=payload,
                headers={"Authorization": f"Bearer {bearer}"},
            ) as upstream:
                body = await upstream.read()
                return web.Response(body=body, status=upstream.status,
                                    content_type="application/json")
    except (aiohttp.ClientError, OSError):
        return web.json_response(
            {"error": "supervisor unreachable — is the standalone facade up?"},
            status=502)


@register
def companion_routes(ctx: PanelContext) -> web.RouteTableDef:  # noqa: ARG001 — seam signature
    routes = web.RouteTableDef()
    base, bearer = _daemon()
    if base is None:
        return routes  # inert: no routes; the page's section stays hidden

    @routes.get("/companion")
    async def companion_state(_req: web.Request) -> web.Response:
        return await _relay("GET", f"{base}/admin/switch", bearer)

    @routes.post("/companion/switch")
    async def companion_switch(req: web.Request) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            body = {}
        allowed = {k: body[k] for k in _BODY_KEYS if k in body}
        return await _relay("POST", f"{base}/admin/switch", bearer, payload=allowed)

    logger.info("[companion] switcher relay active → {}/admin/switch", base)
    return routes
