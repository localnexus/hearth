"""routes/sessions.py — the resume shelf, and getting a session file out.

Three routes, one contract: the route layer never reads a line of what was
said. The shelf answers ids, names, counts and stamps (the list_sessions
contract); reveal hands a path to the Finder and never to the response;
download streams the bytes straight off disk without parsing them. File paths
stay out of every response — session_id is the resume key, so the shelf cannot
double as a directory listing, and a refusal cannot double as a map of it.

The token estimate is bytes/4, the same crude estimator the compaction trigger
uses; sharing it means the number a person sees is the number that decides.

Getting a file out has two shapes because the panel has two audiences.
**Reveal** (`POST /admin/sessions/reveal`) only means something when the
browser sits on the same machine as Hearth, so it is refused with a 409 that
says "use download instead" when the request's peer is not loopback. **Download**
(`GET /admin/sessions/file`) works from anywhere the panel reaches: the file is
the user's own writing, and it carries the conversation but never the persona
(the store drops every system message before the write), so nothing has to be
stripped on the way out.

The fence both share lives in `hearth.session.verbs` — id shape, no traversal,
no symlink escape, the resolved path still under the companion's sessions dir.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from hearth.session import verbs as verbs_mod

from .. import switch as switch_mod

#: A reveal is a one-shot local command; if it has not returned by then,
#: something is wrong with the desktop, not with us.
REVEAL_TIMEOUT_S = 10.0


def _known_character(character: str) -> bool:
    return character in {c["name"] for c in switch_mod.choices()["characters"]}


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


# ── getting the file out: reveal (same machine) · download (anywhere) ────────

async def _session_reveal(request: web.Request) -> web.Response:
    """POST /admin/sessions/reveal {character, session}: show the file in the
    Finder. Only meaningful when the browser is on this machine, so a
    non-loopback peer is answered 409 and pointed at the download instead. The
    argv is fixed and the path never reaches the response."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a bad body is a 400, not a traceback
        body = {}
    character = str((body or {}).get("character") or "")
    session = str((body or {}).get("session") or "").removesuffix(".json")
    if not character or not session:
        return web.json_response({"ok": False, "error": "character and session required"},
                                 status=400)
    if not _known_character(character):
        return web.json_response({"ok": False, "error": f"unknown character {character!r}"},
                                 status=404)
    if not verbs_mod.is_loopback_peer(request.remote):
        return web.json_response({
            "ok": False,
            "error": ("reveal only works when the browser is on the same machine "
                      "as Hearth — use download instead"),
        }, status=409)
    archived = request.query.get("archived") in ("1", "true", "yes") or \
        bool((body or {}).get("archived"))
    try:
        path = verbs_mod.resolve_session_path(character, session, archived=archived)
    except verbs_mod.SessionPathError as exc:
        status = 400 if exc.reason == "invalid session id" else 404
        return web.json_response({"ok": False, "error": exc.reason}, status=status)
    argv = verbs_mod.reveal_argv(path)
    if not argv:
        return web.json_response(
            {"ok": False, "error": "revealing a file in the file manager is a macOS "
                                   "action — use download here"},
            status=501)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    except OSError as exc:
        return web.json_response(
            {"ok": False, "error": f"could not reveal the file ({type(exc).__name__})"},
            status=500)
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=REVEAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return web.json_response({"ok": False, "error": "reveal timed out"}, status=504)
    if rc != 0:
        return web.json_response({"ok": False, "error": f"reveal exited {rc}"}, status=500)
    return web.json_response({"ok": True, "session_id": session})


async def _session_file(request: web.Request) -> web.StreamResponse:
    """GET /admin/sessions/file?character=&session=[&archived=1]: the session
    file itself, as a download. The bytes are streamed by FileResponse — this
    route never opens, parses, or logs the content."""
    character = request.query.get("character") or ""
    session = (request.query.get("session") or "").removesuffix(".json")
    if not character or not session:
        return web.json_response({"ok": False, "error": "character and session required"},
                                 status=400)
    if not _known_character(character):
        return web.json_response({"ok": False, "error": f"unknown character {character!r}"},
                                 status=404)
    archived = request.query.get("archived") in ("1", "true", "yes")
    try:
        path = verbs_mod.resolve_session_path(character, session, archived=archived)
    except verbs_mod.SessionPathError as exc:
        status = 400 if exc.reason == "invalid session id" else 404
        return web.json_response({"ok": False, "error": exc.reason}, status=status)
    return web.FileResponse(path, headers={
        "Content-Type": "application/json",
        "Content-Disposition": f'attachment; filename="{character}-{session}.json"',
    })
