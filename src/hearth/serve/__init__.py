"""serve/ — the /v1 facade: one authed door for every phone-path consumer.

A thin adapter: chat clients, a streaming client's LLM leg, and any future /v1
speaker all enter here; persona composition happens in exactly one place, and
every conversation crosses one tap.

Gate: config/serve.toml (config_loader.load_serve_config) — absent or
enabled=false ⇒ maybe_attach returns None having imported nothing beyond this
docstring; no new sockets, no prompt-slot participation, appliance
byte-identical.

Attach seam mirrors openclaw_bridge.maybe_attach (bot.py one-liner). The
package also runs standalone — `uv run python -m hearth.serve` — for sidecar use
(facade up while the voice appliance is down); see __main__.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from hearth.config import config_loader

if TYPE_CHECKING:
    from aiohttp import web

    from hearth.config.config_loader import ActiveConfig


async def maybe_attach(
    active: "ActiveConfig", lm_base_url: str = "", lm_token: str = ""
) -> Optional["web.AppRunner"]:
    """Start the facade iff config/serve.toml enables it. The single bot.py seam.

    Disabled/absent ⇒ None, nothing imported or bound. Enabled ⇒ its own
    aiohttp Application on its own host:port (NOT the control panel's app —
    the bearer middleware guards facade routes only, and a later tailnet bind
    of the facade must never drag the unauthenticated panel with it).
    Returns the AppRunner (caller cleans up on exit) — or None when the bind
    fails environmentally (port busy: a standalone `python -m hearth.serve` already
    running), because the voice appliance must not die for the facade.
    """
    cfg = config_loader.load_serve_config()
    if not cfg:
        return None
    from . import app as serve_app  # lazy: the facade stack loads only past the gate

    return await serve_app.start(active, cfg, lm_base_url, lm_token)
