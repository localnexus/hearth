"""features/live_switch.py — the bot half of the live companion switch (ADR 007 stroke 3).

DROP-IN (the panel-extension seam): bot.py imports this module; registration is
the import side effect, control.py takes zero edits. The routes expose the
pipeline's LiveSwitcher (pipeline/switcher.py) on the panel so the supervisor
daemon — or the panel page itself, same loopback trust class as /say — can arm
a live switch intent that applies at the next turn boundary.

The daemon's /admin/switch is still the one product door for switching: it
validates + writes active.toml, then HANDS the bundle here when every changed
piece has a live path (registry-routed), falling back to the stroke-2
supervised restart otherwise. A direct POST here also converges active.toml —
the apply path writes the selection iff the file doesn't already carry it.

Wiring: bot.py main() calls ``attach(live_switcher)`` once the switcher's
late-bound deps (engine_info, recorder) exist; until then the routes answer
503. Responses carry names, states, and warnings only (POL-GL-039).

API:
    GET  /switch/live  → {armed, pending, current, last, resident_models}
    POST /switch/live  → prepare + arm (applies at the next turn boundary);
                         refusals arm nothing (400 invalid/no-op · 409 busy
                         or model-residency)
"""

from __future__ import annotations

from aiohttp import web

from hearth.control.control_routes import PanelContext, register

_SWITCHER = None


def attach(switcher) -> None:
    """bot.py main() hands the live pipeline's switcher over (one call)."""
    global _SWITCHER
    _SWITCHER = switcher


@register
def live_switch_routes(ctx: PanelContext) -> web.RouteTableDef:  # noqa: ARG001 — seam signature
    routes = web.RouteTableDef()

    @routes.get("/switch/live")
    async def live_status(_req: web.Request) -> web.Response:
        if _SWITCHER is None:
            return web.json_response({"ok": False, "error": "live switch not wired"},
                                     status=503)
        return web.json_response(await _SWITCHER.describe())

    @routes.post("/switch/live")
    async def live_arm(req: web.Request) -> web.Response:
        if _SWITCHER is None:
            return web.json_response({"ok": False, "error": "live switch not wired"},
                                     status=503)
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001 — empty body = no-op merge
            body = {}
        res = await _SWITCHER.prepare(dict(body))
        status = 200 if res.get("ok") else int(res.pop("code", 400))
        return web.json_response(res, status=status)

    return routes
