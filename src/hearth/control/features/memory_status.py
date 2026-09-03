"""features/memory_status.py — the panel's memory tap: read-only status plus
ONE runtime knob (the per-turn-voice pause).

DROP-IN (the panel-extension seam, same shape as live_switch): bot.py imports
this module; registration is the import side effect, control.py takes zero
edits. The route reads the switcher's CURRENT seam per request, so a live
companion switch is honored without any push plumbing.

Read-only for memory STATE and CONTENT — the write-layer rule (signed (c)
2026-09-02): every memory mutation (curation, mode changes) lives on the
:65001 facade behind the token door. The one knob here is not that: a
RUNTIME-ONLY poke of the live seam's ``per_turn_voice`` gate (decision signed
2026-09-02, v2 web-control-config/build-per-turn-voice-hot-knob.md) — L2
plumbing-calibration in the knob taxonomy, writing no file and touching no
content. It lives here and not on the facade for a structural reason: the
facade is a separate process and cannot reach this process's seam object.
memory.toml stays the between-sessions truth — a restart or live switch
re-reads it and the poke dies with the seam it touched.

Wiring: bot.py main() calls ``attach(live_switcher, mode, ...)`` once engine
facts are resolved; until then the routes answer 503. ``mode`` is the
sitting's EFFECTIVE memory mode (engine_info["memory_mode"]): the --memory
value when a seam is attached or deliberately "off", None when memory isn't
configured. ``voice_prefetch_built`` records whether bot.py put the prefetch
processor in the pipeline at all (it only does when the seam attached with
BOTH per-turn gates on) — a sitting without the processor has nothing a
runtime poke could light, and the POST says so with a 409 instead of
pretending.

API:
    GET /memory → {ok, mode, attached, voice_prefetch_built, seam?}
        seam = MemorySeam.status(): {companion, backend, retain, recall_limit,
        per_turn:{chat, voice, limit}, open_recall, turn_recall} — the recall
        entries name the backend that ACTUALLY answered (a floor fallback
        never masquerades as the primary), and per_turn.voice is EFFECTIVE
        (computed from the live gates, so a poke shows up truthfully).
    POST /memory/per-turn-voice {"on": bool} → pause/resume the voice lane's
        prefetch-behind recall, effect next turn. Runtime-only.
"""

from __future__ import annotations

from aiohttp import web
from loguru import logger

from hearth.control.control_routes import PanelContext, register

_SWITCHER = None
_MODE: str | None = None
_VOICE_PREFETCH_BUILT = False


def attach(switcher, mode: str | None, voice_prefetch_built: bool = False) -> None:
    """bot.py main() hands the live pipeline's switcher over (one call)."""
    global _SWITCHER, _MODE, _VOICE_PREFETCH_BUILT
    _SWITCHER = switcher
    _MODE = mode
    _VOICE_PREFETCH_BUILT = voice_prefetch_built


@register
def memory_status_routes(ctx: PanelContext) -> web.RouteTableDef:  # noqa: ARG001 — seam signature
    routes = web.RouteTableDef()

    @routes.get("/memory")
    async def memory_status(_req: web.Request) -> web.Response:
        if _SWITCHER is None:
            return web.json_response({"ok": False, "error": "memory status not wired"},
                                     status=503)
        seam = _SWITCHER.current_seam
        body: dict = {"ok": True, "mode": _MODE, "attached": seam is not None,
                      "voice_prefetch_built": _VOICE_PREFETCH_BUILT}
        if seam is not None:
            body["seam"] = seam.status()
        return web.json_response(body)

    @routes.post("/memory/per-turn-voice")
    async def per_turn_voice_poke(req: web.Request) -> web.Response:
        if _SWITCHER is None:
            return web.json_response({"ok": False, "error": "memory status not wired"},
                                     status=503)
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001 — malformed body = an invalid request
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
            return web.json_response(
                {"ok": False, "error": 'JSON body {"on": true|false} required'},
                status=400)
        seam = _SWITCHER.current_seam
        if seam is None:
            return web.json_response(
                {"ok": False, "error": "no memory seam this sitting — nothing "
                                       "to poke"}, status=409)
        if not _VOICE_PREFETCH_BUILT:
            return web.json_response(
                {"ok": False, "error": "the prefetch processor is not in this "
                 "sitting's pipeline — set [memory.per_turn].voice = true in "
                 "config/memory.toml and restart to light the voice lane"},
                status=409)
        if not getattr(seam, "per_turn_enabled", False):
            # A live switch can land on a companion whose seam has the chat
            # gate off — the voice lane can never light through it.
            return web.json_response(
                {"ok": False, "error": "[memory.per_turn].enabled is off for "
                 "the current companion — the voice lane rides the chat gate"},
                status=409)
        on = bool(body["on"])
        seam.per_turn_voice = on
        logger.info("[memory] per-turn voice recall {} (runtime poke — "
                    "memory.toml untouched)", "resumed" if on else "paused")
        return web.json_response({
            "ok": True, "per_turn_voice": on,
            "effect": ("recall resumes at the next turn" if on else
                       "no new recall from the next turn; already-applied "
                       "extras are cleared then too"),
            "note": "runtime-only — memory.toml unchanged; a restart or live "
                    "switch returns to file truth"})

    return routes
