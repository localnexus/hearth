"""routes/sessions.py — the resume shelf: SessionMeta only, never conversation
content.

The one route here is what the resume picker reads. It answers ids, names,
counts and stamps — the list_sessions contract — and never a line of what was
said. File paths stay out of the response too: session_id is the resume key,
so the shelf cannot double as a directory listing.

The token estimate is bytes/4, the same crude estimator the compaction trigger
uses; sharing it means the number a person sees is the number that decides.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

from aiohttp import web

from .. import switch as switch_mod


async def _sessions(request: web.Request) -> web.Response:
    """Resume-picker source: SessionMeta ONLY — ids, names, counts, stamps.
    Conversation content is never read out (the list_sessions contract), and
    file paths are not exposed — session_id is the resume key. ?character=<name>
    lists another companion's shelf (validated against the switch picker's
    choices); absent, the ACTIVE companion's."""
    from hearth.session import session_store  # lazy: mirrors the package gate idiom

    character = request.query.get("character") or None
    if character is not None:
        known = {c["name"] for c in switch_mod.choices()["characters"]}
        if character not in known:
            return web.json_response({"error": f"unknown character {character!r}"},
                                     status=404)
    try:
        sdir = session_store.companion_sessions_dir(character)
    except Exception as exc:  # noqa: BLE001 — an unreadable active.toml must answer, not raise
        return web.json_response(
            {"error": f"cannot resolve the active companion ({type(exc).__name__})"},
            status=409)
    metas = session_store.list_sessions(sdir)

    def _est_tokens(session_id: str):
        try:  # bytes/4 — the same estimator the compaction trigger uses
            return (sdir / f"{session_id}.json").stat().st_size // 4
        except OSError:
            return None

    return web.json_response({
        "character": character or sdir.parent.name,
        "sessions": [{
            "session_id": m.session_id,
            "name": m.name,
            "held": m.held,
            "started": m.started,
            "updated": m.updated,
            "turns": m.turns,
            "persona": m.persona,
            "memory_mode": m.memory_mode,
            "model": m.model,
            "voice": m.voice,
            "est_tokens": _est_tokens(m.session_id),
        } for m in metas],
    })
