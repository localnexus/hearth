"""roster/page.py — the read side: the wizard's shell and the roster listing
behind it.

The shell is auth-exempt and contentless, like /admin/launch: it ships markup
and no facts. Everything a visitor can actually see arrives from /state, which
IS authed — names, voices, personas, the tier map, the active selection, and
whether ffmpeg is on the box.

roster_page.html lives beside this module and is read once at import
(HEARTH_DEV_RELOAD=1 inverts that per process — see ui/pages.py).

One part of the /admin/roster arc; the package __init__ carries the map of the
whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web

from hearth.ui import admin_shell, brand, pages, roster_sections

from .. import switch as switch_mod

from .bundle import ffmpeg_path

# The page keeps its markup and the wiring that starts it; onboarding, the
# persona/voice editor and the branch card are three files under ui/ — see
# ui/roster_sections.py, which carries the one ordering rule between them.
_PAGE = pages.Page(Path(__file__).parent / "roster_page.html",
                   pages.chain(roster_sections.splice, admin_shell.splice,
                               brand.splice))


async def _page(_req: web.Request) -> web.Response:
    return web.Response(text=_PAGE(), content_type="text/html")


async def _state(request: web.Request) -> web.Response:
    """Roster listing — names only (voices, personas, tier map, active pick)."""
    from hearth.config import config_loader

    def _build() -> dict:
        chars = switch_mod.choices()["characters"]
        mem = config_loader.load_memory_config()
        active: dict = {}
        try:
            sel = config_loader.load_active_selection()
            active = {"character": sel.get("character"), "voice": sel.get("voice")}
        except Exception:  # noqa: BLE001 — an unreadable active.toml is display-only here
            pass
        for c in chars:
            c["memory_backend"] = (
                None if mem is None else
                str(dict(mem.get("companions") or {}).get(
                    c["name"], mem.get("backend", "floor"))))
        return {"characters": chars, "active": active,
                "memory_enabled": mem is not None,
                "ffmpeg": bool(ffmpeg_path())}

    return web.json_response(await asyncio.to_thread(_build))
