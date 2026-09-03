"""supervisor/curation.py — /admin/memory: record-level curation behind the door.

The web half of the memory CLI (``python -m hearth.memory``), the
record-level-curation follow-on unparked under the write-layer rule (signed
(c) 2026-09-02): :65000 only displays and links; the curation VERBS live
here, on the facade, behind the bearer door like every /admin route.

Preview-then-confirm mirrors the CLI exactly. A forget without ``"yes": true``
answers the digest of what WOULD be deleted and mutates nothing; the confirm
call deletes **backend-first** (a failed index update keeps the record, so the
verb is safely re-runnable — never half-forgotten). Digest discipline
throughout: responses carry SessionMeta + the same extractive digest the CLI
prints (the signed §4 posture) — never the transcript itself.

Backend access goes through the facade's OWN memory glue
(``deps.memory.curation_backend``): the same per-companion backend ServeMemory
keeps for its life, so curation never spawns a second sidecar against the same
pg0 store. With ``[memory.serve]`` disabled the GET views still answer (pure
record-file metadata) but the verbs answer 409 naming the desk CLI.

One deliberate divergence from the CLI: ``character`` is REQUIRED on the
forget verb (no default-to-active) — forgetting the wrong companion's session
is the failure mode, and a web client, unlike an operator at the desk, has no
ambient "active companion" in view.

``rebuild --clean`` is deliberately NOT exposed: wipe-then-replay runs the
extraction model over every record (minutes, unbounded by request timeouts) —
an at-the-desk install action. The forget path names the CLI command when it
detects pre-keyed leftovers, exactly as the CLI does.

API (mounted by routes.build_mount iff [serve.supervisor] enabled; authed):
    GET  /admin/memory                       → per-companion record counts (+ backend map)
    GET  /admin/memory/records?character=<c> → one companion's records, digest view
    GET  /admin/memory/facts?character=<c>   → the bank's indexed-fact count (lazy:
         one deliberate backend call — the count parked off the display routes
         lands here, where a curation view justifies it; null when the lane is
         down, the companion maps "none", or the backend keeps no index)
    POST /admin/memory/forget {character, session, yes?}
         yes absent/false → preview only; yes true → backend facts, then the record
    GET  /admin/memory/ui                    → the review-and-prune pane (static
         chrome, auth-exempt beside /admin/launch and /admin/roster — every fact
         it shows arrives via the authed routes above)
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from aiohttp import web

from . import switch as switch_mod

_PAGE = (Path(__file__).parent / "memory_page.html").read_text(encoding="utf-8")

# Record files are <session_id>.json — the id must stay a bare filename.
_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _known_characters() -> set[str]:
    return {c["name"] for c in switch_mod.choices()["characters"]}


def _record_summary(record) -> dict:
    """SessionMeta + digest — the CLI `records` line, as JSON."""
    from hearth.memory.backend import digest_record

    digest = digest_record(record)
    if len(digest) > 100:
        digest = digest[:99] + "…"
    return {
        "session_id": record.session_id,
        "when": (record.ended or record.started or "")[:16].replace("T", " "),
        "name": record.name or None,
        "user_turns": sum(1 for m in record.messages if m.get("role") == "user"),
        "digest": digest,
    }


async def _overview(request: web.Request) -> web.Response:
    """Per-companion curation overview: record COUNTS (a directory glob —
    nothing is parsed) plus the backend each name resolves to when the memory
    glue is up (None otherwise: counts stay useful, routing stays honest)."""
    from hearth.memory import records as records_mod

    glue = request.app["deps"].memory

    def _scan() -> list[dict]:
        out = []
        for name in sorted(_known_characters()):
            count = len(list(records_mod.records_dir(name).glob("*.json")))
            out.append({
                "character": name,
                "records": count,
                "backend": glue.backend_name_for(name) if glue is not None else None,
            })
        return out

    return web.json_response({
        "memory_lane": glue is not None,  # verbs need it; the views don't
        "companions": await asyncio.to_thread(_scan),
    })


async def _records(request: web.Request) -> web.Response:
    """One companion's records, newest first — the CLI `records` view."""
    from hearth.memory import records as records_mod

    character = request.query.get("character") or ""
    if character not in _known_characters():
        return web.json_response({"error": f"unknown character {character!r}"},
                                 status=404)

    def _list() -> list[dict]:
        return [_record_summary(r)
                for r in records_mod.iter_records(character, newest_first=True)]

    return web.json_response({"character": character,
                              "records": await asyncio.to_thread(_list)})


async def _facts(request: web.Request) -> web.Response:
    """One companion's indexed-fact count — the lazy gauge. Costs a real
    backend call (thread hop), so it lives on its own route the pane hits only
    on a deliberate selection, never on a poll. Honest nulls everywhere a
    count doesn't exist; a backend without the capability (the floor derives
    from records directly — there IS no separate index) answers null too."""
    character = request.query.get("character") or ""
    if character not in _known_characters():
        return web.json_response({"error": f"unknown character {character!r}"},
                                 status=404)
    base = {"character": character, "facts": None}
    glue = request.app["deps"].memory
    if glue is None:
        return web.json_response(
            {**base, "note": "facade memory lane disabled ([memory.serve])"})
    backend = await asyncio.to_thread(glue.curation_backend, character)
    if backend is None:
        return web.json_response(
            {**base, "backend": "none", "note": "companion mapped \"none\" — no index"})
    counter = getattr(backend, "fact_count", None)
    if counter is None:
        return web.json_response(
            {**base, "backend": backend.name,
             "note": f"backend {backend.name!r} keeps no fact index"})
    try:
        counted = await asyncio.to_thread(counter, character)
    except Exception as exc:  # noqa: BLE001 — a failed count is a note, not a crash
        return web.json_response(
            {**base, "backend": backend.name,
             "error": f"fact count failed ({type(exc).__name__})"}, status=502)
    return web.json_response({**base, "backend": backend.name, **dict(counted)})


async def _page(_req: web.Request) -> web.Response:
    """The review-and-prune pane — static chrome (see memory_page.html's
    security contract; the serve middleware exempts exactly this path)."""
    return web.Response(text=_PAGE, content_type="text/html")


async def _forget(request: web.Request) -> web.Response:
    """Preview-then-confirm forget of ONE session: digest first, mutation only
    on ``"yes": true`` — backend facts before the record file (CLI ordering)."""
    from hearth.memory import records as records_mod

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body = an invalid request
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body required"}, status=400)
    character = str(body.get("character") or "")
    session_id = str(body.get("session") or "")
    if character not in _known_characters():
        return web.json_response({"error": f"unknown character {character!r} "
                                           "(explicit character required)"}, status=404)
    if not _SESSION_RE.fullmatch(session_id):
        return web.json_response({"error": "invalid session id"}, status=400)

    path = records_mod.records_dir(character) / f"{session_id}.json"
    if not path.is_file():
        return web.json_response(
            {"error": f"no memory record {session_id!r} for {character!r}",
             "hint": "GET /admin/memory/records lists what exists"}, status=404)

    def _preview() -> dict:
        try:
            return _record_summary(records_mod.load_record(path))
        except (ValueError, OSError, json.JSONDecodeError):
            return {"session_id": session_id,
                    "digest": "(malformed record — no digest available)"}

    preview = await asyncio.to_thread(_preview)
    if not bool(body.get("yes")):
        return web.json_response({
            "ok": True, "forgotten": False, "preview": preview,
            "confirm": "forget deletes this record AND the session's indexed "
                       'facts, permanently — repeat with "yes": true',
        })

    glue = request.app["deps"].memory
    if glue is None:
        return web.json_response(
            {"error": "facade memory lane disabled ([memory.serve]) — curate at "
                      "the desk: python -m hearth.memory forget --session "
                      f"{session_id} --character {character}"}, status=409)

    # Backend first, record second — a failed index update keeps the record.
    backend = await asyncio.to_thread(glue.curation_backend, character)
    excised = None
    if backend is not None:
        try:
            excised = await asyncio.to_thread(backend.forget, character, session_id)
        except Exception as exc:  # noqa: BLE001 — report; the record stays put
            return web.json_response(
                {"ok": False,
                 "error": f"backend forget failed ({type(exc).__name__}) — "
                          "record kept, nothing deleted"}, status=502)
    await asyncio.to_thread(path.unlink)
    result = {"ok": True, "forgotten": True, "preview": preview,
              "backend": getattr(backend, "name", None)}
    if backend is None:
        result["index"] = "none"  # companion mapped "none" — no index existed
    elif excised:
        result["index"] = "excised"
    else:
        result["index"] = "leftover-facts"
        result["hint"] = ("backend holds facts stored before keyed retain: run "
                          "`python -m hearth.memory rebuild --clean --yes "
                          f"--character {character}` at the desk")
    return web.json_response(result)


def add_routes(app: web.Application) -> None:
    """Called by routes.build_mount — same door, same middleware."""
    app.router.add_get("/admin/memory", _overview)
    app.router.add_get("/admin/memory/records", _records)
    app.router.add_get("/admin/memory/facts", _facts)
    app.router.add_get("/admin/memory/ui", _page)
    app.router.add_post("/admin/memory/forget", _forget)
