"""roster/branch.py — branch (fork): the memory CLI's fork verb behind the same
door.

A thin JSON skin over memory/fork.py's own plan/execute pair — identical
validation, selection and rollback, because it calls them rather than
reimplementing them. Nothing about forking is decided here.

The preview keeps SessionMeta discipline: ids, dates and names, never
transcript content, even behind the bearer.

ONE deliberate divergence from the CLI, curation.py's posture verbatim: the
backend REPLAY stays at the desk. Extraction over every record runs for
minutes, unbounded by request timeouts, so a non-floor fork answers "created"
plus the exact rebuild command to run.

One part of the /admin/roster arc; the package __init__ carries the map of the
whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio

from aiohttp import web
from loguru import logger


def _fork_preview(plan) -> dict:
    """The plan as JSON — SessionMeta discipline (ids/dates/names, never
    transcript content), mirroring what the CLI preview prints."""
    return {
        "source": plan.source, "target": plan.target, "juncture": plan.cutoff,
        "records": [{"session_id": r.session_id,
                     "when": (r.ended or r.started or "")[:16].replace("T", " "),
                     "name": r.name or None}
                    for _path, r in plan.records],
        "left_behind": plan.left_behind, "undated": plan.undated,
        "identity_files": len(plan.identity), "voices": plan.voices,
        "sessions": len(plan.sessions), "tier": plan.tier,
        "persona_note": "persona copies AS IT STANDS TODAY — edit it after "
                        "the fork if the juncture's differed",
        "intent_note": "the intent slot never copies (it belongs to the "
                       "source track)",
    }


async def _fork_route(request: web.Request) -> web.Response:
    """Preview-then-confirm over the CLI's plan/execute pair. The backend
    replay deliberately stays at the desk (see the module docstring)."""
    from hearth.memory import fork as fork_mod

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body = an invalid request
        body = None
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "errors": ["JSON body required"]},
                                 status=400)
    character = str(body.get("character") or "")
    target = str(body.get("as") or "")
    until = str(body.get("until") or "")
    include_sessions = bool(body.get("include_sessions"))
    try:
        plan = await asyncio.to_thread(fork_mod.plan, character, target, until,
                                       include_sessions)
    except fork_mod.ForkError as exc:
        return web.json_response({"ok": False, "errors": [str(exc)]}, status=400)

    preview = _fork_preview(plan)
    if not bool(body.get("yes")):
        return web.json_response({
            "ok": True, "created": False, **preview,
            "confirm": 'nothing written — repeat with "yes": true to create '
                       "the fork"})
    try:
        result = await asyncio.to_thread(fork_mod.execute, plan)
    except fork_mod.ForkError as exc:  # plan-to-execute race — create-only stands
        return web.json_response({"ok": False, "errors": [str(exc)]}, status=409)
    except Exception as exc:  # noqa: BLE001 — rolled back in execute
        logger.warning("[roster] fork failed ({})", type(exc).__name__)
        return web.json_response(
            {"ok": False, "errors": [f"fork failed ({type(exc).__name__}) — "
                                     "nothing was kept"]}, status=500)
    if plan.tier in (None, "floor", "none"):
        nxt = ("no backend replay needed — the floor reads record files "
               "directly" if plan.tier == "floor" else
               "no backend replay needed — no indexed backend for this tier")
    elif result["enrolled"]:
        nxt = (f"replay at the desk: `python -m hearth.memory rebuild "
               f"--character {plan.target}` — it runs the extraction model "
               "over each record (minutes), so it stays a CLI step; the "
               "records themselves are already in place")
    else:
        nxt = (f"enrollment did not land — enroll {plan.target!r} by hand, "
               f"then run `python -m hearth.memory rebuild --character "
               f"{plan.target}` at the desk")
    logger.info("[roster] forked {} -> {} at {}", plan.source, plan.target,
                plan.cutoff)
    return web.json_response({
        "ok": True, "created": True, **preview,
        "memory": result["memory"], "loader": "verified (startup loaders ran "
        "clean)", "next": nxt})
