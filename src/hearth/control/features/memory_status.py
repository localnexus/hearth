"""features/memory_status.py — the panel's read-only memory status tap.

DROP-IN (the panel-extension seam, same shape as live_switch): bot.py imports
this module; registration is the import side effect, control.py takes zero
edits. The route reads the switcher's CURRENT seam per request, so a live
companion switch is honored without any push plumbing.

Read-only by design — the write-layer rule (signed (c) 2026-09-02): :65000
renders memory STATE; every memory mutation (curation, mode changes) lives on
the :65001 facade behind the token door. Responses carry names, counts, gate
booleans, and timestamps only — never message content, never the cue text,
never secret values (the seam's own status() enforces the same discipline).

Wiring: bot.py main() calls ``attach(live_switcher, mode)`` once engine facts
are resolved; until then the route answers 503. ``mode`` is the sitting's
EFFECTIVE memory mode (engine_info["memory_mode"]): the --memory value when a
seam is attached or deliberately "off", None when memory isn't configured.

API:
    GET /memory → {ok, mode, attached, seam?}
        seam = MemorySeam.status(): {companion, backend, retain, recall_limit,
        per_turn:{chat, voice, limit}, open_recall, turn_recall} — the recall
        entries name the backend that ACTUALLY answered (a floor fallback
        never masquerades as the primary).
"""

from __future__ import annotations

from aiohttp import web

from hearth.control.control_routes import PanelContext, register

_SWITCHER = None
_MODE: str | None = None


def attach(switcher, mode: str | None) -> None:
    """bot.py main() hands the live pipeline's switcher over (one call)."""
    global _SWITCHER, _MODE
    _SWITCHER = switcher
    _MODE = mode


@register
def memory_status_routes(ctx: PanelContext) -> web.RouteTableDef:  # noqa: ARG001 — seam signature
    routes = web.RouteTableDef()

    @routes.get("/memory")
    async def memory_status(_req: web.Request) -> web.Response:
        if _SWITCHER is None:
            return web.json_response({"ok": False, "error": "memory status not wired"},
                                     status=503)
        seam = _SWITCHER.current_seam
        body: dict = {"ok": True, "mode": _MODE, "attached": seam is not None}
        if seam is not None:
            body["seam"] = seam.status()
        return web.json_response(body)

    return routes
