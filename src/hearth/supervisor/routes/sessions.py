"""routes/sessions.py — the resume shelf, a file out, a file in, a file archived,
a file destroyed.

Seven routes, one contract: the route layer never reads a line of what was
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

Bringing a file IN is the fourth route. **Deposit**
(`POST /admin/sessions/deposit`) takes a session file the person picked in the
browser — as a multipart upload, or as a JSON body when the panel already holds
the parsed object — checks it against `verbs.validate_session_payload`, and
writes it under a **freshly minted id**. The id never comes from the upload, so
a deposit can add a conversation to the shelf and can never replace one; the
reservation refuses a name already taken rather than overwriting it. The answer
is a count and an id, never a line of the file and never a path.

**Archive** (`POST /admin/sessions/archive`) and **unarchive** are the soft
verb, and the only pair that MOVES a file: into `sessions/<c>/.archive/` and
back out. Nothing is removed, nothing is overwritten, and the archived shelf is
readable through `GET /admin/sessions?archived=1` (or `all`) — the default
shelf still answers the live sessions only, which is what keeps an archived
conversation out of the resume picker and out of the fresh-start sweep.

**Destroy** (`POST /admin/sessions/destroy`) is the one hard verb, and the only
one that takes something away. It asks twice — without `confirm` it answers a
plan and touches nothing — and it is a SWEEP rather than an unlink: the file
and the session's memory (its record, each compaction epoch, and the indexed
facts behind them, through the same `curation.forget_session` the memory pane
uses) go in ONE act, memory first so a failed index update leaves everything
intact and re-runnable. Because confidentiality is the whole reason the verb
exists, it also says out loud what it CANNOT reach (`verbs.CANNOT_REACH`).
It is the only verb offered for a recall-only sitting, and the only one behind
an exposure check: same-machine callers always, everyone else only where
[serve.sessions] destroy_for_all says so.

They are also the first routes behind the **live-session guard** (`_guarded`).
The supervisor knows the bot is up and knows which companion `active.toml`
points at, but it cannot know which session id a `--new` sitting minted inside
the child — so the guard is the whole shelf: while the active companion is
running, every one of its session files is read-only, and another companion's
are free. An unreadable `active.toml` fails closed. Destroy uses the same
helper, and rename will.

The fence they share lives in `hearth.session.verbs` — id shape, no traversal,
no symlink escape, the resolved path still under the companion's sessions dir —
and so does the deposit gate, for the same reason: both are decisions about a
file, testable without an HTTP client.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web
from loguru import logger

from hearth.session import verbs as verbs_mod

from .. import switch as switch_mod

#: A reveal is a one-shot local command; if it has not returned by then,
#: something is wrong with the desktop, not with us.
REVEAL_TIMEOUT_S = 10.0

#: A minted id already carries the second; a second deposit inside the same
#: second gets a suffix rather than a refusal. Bounded, so a genuinely stuck
#: name answers 409 instead of spinning.
DEPOSIT_SUFFIXES = tuple(f"-{n}" for n in range(2, 10))


def _known_character(character: str) -> bool:
    return character in {c["name"] for c in switch_mod.choices()["characters"]}


async def _sessions(request: web.Request) -> web.Response:
    """Resume-picker source: SessionMeta ONLY — ids, names, counts, stamps.
    Conversation content is never read out (the list_sessions contract), and
    file paths are not exposed — session_id is the resume key. ?character=<name>
    lists another companion's shelf (validated against the switch picker's
    choices); absent, the ACTIVE companion's.

    ?archived= steers WHICH shelf: absent (the default) the live sessions only,
    which is what the resume picker wants; `1` the archived ones only; `all`
    both. Every row carries `archived`, so a mixed listing is readable without
    a second call."""
    from hearth.session import session_store  # lazy: mirrors the package gate idiom

    archived_q = (request.query.get("archived") or "").strip().lower()
    want_archived = archived_q in ("1", "true", "yes", "only", "all")
    only_archived = want_archived and archived_q != "all"
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
    metas = session_store.list_sessions(sdir, include_archived=want_archived)
    if only_archived:
        metas = [m for m in metas if m.archived]

    def _est_tokens(meta):
        try:  # bytes/4 — the same estimator the compaction trigger uses
            return meta.path.stat().st_size // 4
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
            "origin": m.origin,
            "archived": m.archived,
            "est_tokens": _est_tokens(m),
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


# ── bringing a file in: deposit ──────────────────────────────────────────────

async def _read_deposit_upload(request: web.Request):
    """Read the upload in whichever shape it arrived.

    Returns ``(character, data, error)``. ``data`` is the parsed session object;
    ``error`` is a ``(message, status)`` pair when the body could not be read as
    a session at all. Multipart is read chunk by chunk so the size cap can stop
    a big file before the whole of it is in memory; a JSON body is capped by its
    declared length and by the facade's own body cap behind that.
    """
    cap = verbs_mod.MAX_DEPOSIT_BYTES
    too_big = ("that file is larger than a session file is allowed to be", 413)
    if (request.content_length or 0) > cap:
        return None, None, too_big
    ctype = (request.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()

    if ctype == "multipart/form-data":
        character, blob = None, None
        try:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "character":
                    character = (await part.text()).strip()
                elif part.name == "file":
                    buf = bytearray()
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        buf += chunk
                        if len(buf) > cap:
                            return None, None, too_big
                    blob = bytes(buf)
                else:
                    await part.read()
        except Exception:  # noqa: BLE001 — a torn upload is a 400, not a traceback
            return None, None, ("the upload could not be read", 400)
        if blob is None:
            return character, None, ("no file in the upload", 400)
        try:
            return character, json.loads(blob.decode("utf-8")), None
        except (UnicodeDecodeError, ValueError):
            return character, None, ("not a JSON session file", 400)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None, None, ("not a JSON session file", 400)
    if not isinstance(body, dict):
        return None, None, ("not a JSON session file", 400)
    character = body.get("character")
    character = character.strip() if isinstance(character, str) else None
    if "session" not in body:
        return character, None, ("no session in the body", 400)
    return character, body.get("session"), None


async def _session_deposit(request: web.Request) -> web.Response:
    """POST /admin/sessions/deposit: bring a session file in.

    Two body shapes, one path through: multipart form-data with ``file`` (the
    JSON) and ``character``, or a JSON body ``{character, session}`` when the
    panel already holds the parsed object. The file is checked by
    ``verbs.validate_session_payload`` — this companion's, a voice it has, a
    persona that exists, no system message kept — and then written under an id
    minted HERE, never taken from the upload, so a deposit can only ever add to
    the shelf. The answer carries the new id and two counts; the content and the
    path stay where they are."""
    from hearth.session import session_store  # lazy: mirrors the package gate idiom

    character, data, err = await _read_deposit_upload(request)
    # A body too big, or one so unreadable that even the companion's name did
    # not survive it, answers about the BODY: "character required" would be a
    # true statement and a useless one.
    if err is not None and (err[1] == 413 or not character):
        return web.json_response({"ok": False, "error": err[0]}, status=err[1])
    if not character:
        return web.json_response({"ok": False, "error": "character required"}, status=400)
    if not _known_character(character):
        return web.json_response({"ok": False, "error": f"unknown character {character!r}"},
                                 status=404)
    if err is not None:
        return web.json_response({"ok": False, "error": err[0]}, status=err[1])

    try:
        payload = verbs_mod.validate_session_payload(data, character=character)
    except verbs_mod.SessionPayloadError as exc:
        return web.json_response({"ok": False, "error": exc.reason}, status=400)

    minted = session_store.new_session_id()
    path, session_id = None, None
    for suffix in ("",) + DEPOSIT_SUFFIXES:
        candidate = minted + suffix
        try:
            path = verbs_mod.reserve_session_path(character, candidate)
        except verbs_mod.SessionPathError as exc:
            if exc.reason != "session id already exists":
                return web.json_response({"ok": False, "error": exc.reason}, status=400)
            continue
        session_id = candidate
        break
    if path is None:
        return web.json_response(
            {"ok": False, "error": "a session with that id already exists — try again"},
            status=409)

    try:
        session_store.ensure_dir(path.parent)
        session_store._atomic_write_json(path, payload)
    except OSError as exc:
        return web.json_response(
            {"ok": False, "error": f"the session could not be written ({type(exc).__name__})"},
            status=500)

    messages = payload.get("messages") or []
    return web.json_response({
        "ok": True,
        "session_id": session_id,
        "character": character,
        "turns": sum(1 for m in messages if m.get("role") == "user"),
        "dropped_system_messages": verbs_mod.dropped_system_messages(data, payload),
    })


# ── the live-session guard, and the soft verb it first protects ──────────────

def _guarded(request: web.Request, character: str):
    """The one place a mutating session verb asks "may I touch this shelf?".

    Returns a 409 response to send back, or None to proceed. Process truth is
    the child's own `status()["state"]` — an adopted desk bot reads as running
    exactly like a managed one, and `starting`/`stopping` count as up. The
    rule itself is `verbs.live_guard`, so it can be reasoned about without a
    request in hand.

    Two ways this fails closed. A `bot_child` that is absent (only tests build
    an app without one) reads as down, because in that app nothing is running.
    An `active.toml` that cannot be read is the dangerous case: the companion
    could be the running one and we cannot tell, so the verb is refused rather
    than guessed at. Archive and unarchive use this today; destroy and rename-id
    will use the same helper.
    """
    from hearth.config import config_loader  # lazy: mirrors the package gate idiom

    child = request.app.get("bot_child")
    state = "down"
    if child is not None:
        try:
            state = str((child.status() or {}).get("state") or "down")
        except Exception:  # noqa: BLE001 — an unreadable child is treated as up
            state = "running"
    try:
        active = config_loader.load_active_selection()["character"]
    except Exception:  # noqa: BLE001 — cannot tell whose shelf this is → refuse
        return web.json_response({
            "ok": False,
            "error": (f"{character} may be running — the active companion could not "
                      f"be read; stop the companion first, its session files are "
                      f"read-only while it is up"),
        }, status=409)
    reason = verbs_mod.live_guard(character, state, active)
    if reason is None:
        return None
    return web.json_response({"ok": False, "error": reason}, status=409)


def _archive_request(request: web.Request, body):
    """The shared front half of archive/unarchive: character + session out of
    the body, the character known, and the shelf not read-only. Returns
    ``(character, session, response)`` — a response means stop here."""
    character = str((body or {}).get("character") or "")
    session = str((body or {}).get("session") or "").removesuffix(".json")
    if not character or not session:
        return None, None, web.json_response(
            {"ok": False, "error": "character and session required"}, status=400)
    if not verbs_mod.valid_session_id(session):
        return None, None, web.json_response(
            {"ok": False, "error": "invalid session id"}, status=400)
    if not _known_character(character):
        return None, None, web.json_response(
            {"ok": False, "error": f"unknown character {character!r}"}, status=404)
    return character, session, _guarded(request, character)


def _already(character: str, session: str, *, archived: bool):
    """Is the session already where the verb wanted to put it?

    Archiving a session that is already archived reaches ``resolve`` with
    nothing on the live shelf and would answer 404 — a true statement about the
    source and a wrong answer to the question that was asked. So before the 404
    is sent, the destination is checked: if the file is already there, the verb
    has nothing to do and says so with ``already``.
    """
    try:
        verbs_mod.resolve_session_path(character, session, archived=archived)
    except verbs_mod.SessionPathError:
        return False
    return True


async def _move(request: web.Request, *, archive: bool) -> web.Response:
    """The body of both verbs — the same act with the direction flipped."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a bad body is a 400, not a traceback
        body = {}
    character, session, resp = _archive_request(request, body)
    if resp is not None:
        return resp
    verb = verbs_mod.archive_session if archive else verbs_mod.unarchive_session
    try:
        verb(character, session)
    except verbs_mod.SessionPathError as exc:
        if exc.reason == "no such session":
            if _already(character, session, archived=archive):
                return web.json_response({"ok": True, "session_id": session,
                                          "archived": archive, "already": True})
            return web.json_response({"ok": False, "error": exc.reason}, status=404)
        if exc.reason == "session id already exists":
            # Both sides hold this name. Neither is overwritten — the person is
            # told, and decides which one they meant.
            where = "archived" if archive else "on the shelf"
            return web.json_response(
                {"ok": False,
                 "error": f"a session with this id is already {where}"}, status=409)
        status = 400 if exc.reason == "invalid session id" else 404
        return web.json_response({"ok": False, "error": exc.reason}, status=status)
    except OSError as exc:
        return web.json_response(
            {"ok": False,
             "error": f"the session could not be moved ({type(exc).__name__})"},
            status=500)
    return web.json_response({"ok": True, "session_id": session, "archived": archive})


async def _session_archive(request: web.Request) -> web.Response:
    """POST /admin/sessions/archive {character, session}: move a saved session
    into the companion's archive. It leaves the resume shelf and the fresh-start
    sweep, and nothing else happens to it — this is the soft verb, reversible by
    construction. 200 `{ok, session_id, archived: true}`, or `already: true`
    when it was archived before. Refused while the companion is up."""
    return await _move(request, archive=True)


async def _session_unarchive(request: web.Request) -> web.Response:
    """POST /admin/sessions/unarchive {character, session}: put an archived
    session back on the shelf. The mirror of archive, same guard, same
    refusals."""
    return await _move(request, archive=False)


# ── destroy: the one hard verb ───────────────────────────────────────────────

def _destroy_offered(request: web.Request) -> bool:
    """Who may destroy. Two ways to be allowed, and no third.

    The panel has no audience or role concept in code yet — one access key,
    one caller, and that caller is the operator. So the decision D1 asked for
    ("operator always; anyone else only behind a setting, off by default") is
    implemented with the only distinction the request actually carries: the
    person is at the machine Hearth runs on (a loopback peer), or the install
    has said out loud that destroy is offered to everyone who holds the key
    ([serve.sessions] destroy_for_all). Nothing here is an auth layer, and it
    is not pretending to be one — past the door every caller is equally
    trusted; this only keeps the irreversible verb off the phone by default.
    """
    if verbs_mod.is_loopback_peer(request.remote):
        return True
    try:
        cfg = getattr(request.app["deps"], "cfg", None) or {}
        return bool(dict(cfg.get("sessions") or {}).get("destroy_for_all"))
    except Exception:  # noqa: BLE001 — an unreadable config is not an offer
        return False


def _confirm_with(path, session: str) -> str:
    """The exact word the person has to type back: the session's NAME when it
    has one, its id otherwise. The name is SessionMeta's — a field, the same
    one the shelf answers — and never a line of the conversation."""
    from hearth.session import session_store  # lazy: mirrors the package gate idiom

    if path is None:
        return session
    meta = session_store._meta_of(path)
    name = getattr(meta, "name", None) if meta is not None else None
    return name.strip() if isinstance(name, str) and name.strip() else session


async def _session_destroy(request: web.Request) -> web.Response:
    """POST /admin/sessions/destroy {character, session, archived?, confirm?}:
    unlink the file AND forget the session's memory in one act.

    This is the only verb here that takes something away, so it is the only one
    that asks twice. Without `confirm` it answers a PLAN and touches nothing:
    what would go (the file, the memory record and each compaction epoch, the
    indexed facts behind them), the word to type back, and — said out loud,
    because confidentiality is the whole reason the verb exists — what destroy
    CANNOT reach. With `confirm`, the word has to match the session's name (or
    its id, when it has no name) exactly; anything else is a 409 and nothing is
    touched.

    On a match the memory goes FIRST. A failed index update then keeps
    everything, including the file, and the verb can be run again — the
    opposite order would leave a person with the conversation gone and the
    facts extracted from it still in the bank, which is the failure this verb
    exists to prevent. A sitting that banked nothing (recall-only, or memory
    off) has no record, and the memory step says `no-record` rather than
    pretending it did something.

    A file that is already gone while records remain is not an error: destroy
    finishes a sweep somebody started by hand.

    The answer carries ids and booleans. Nothing is logged but a single line
    naming the companion and the session id.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a bad body is a 400, not a traceback
        body = {}
    if not isinstance(body, dict):
        body = {}
    character = str(body.get("character") or "")
    session = str(body.get("session") or "").removesuffix(".json")
    if not character or not session:
        return web.json_response(
            {"ok": False, "error": "character and session required"}, status=400)
    if not verbs_mod.valid_session_id(session):
        return web.json_response({"ok": False, "error": "invalid session id"},
                                 status=400)
    if not _known_character(character):
        return web.json_response({"ok": False, "error": f"unknown character {character!r}"},
                                 status=404)
    if not _destroy_offered(request):
        return web.json_response({
            "ok": False,
            "error": ("destroy is not offered here — enable [serve.sessions] "
                      "destroy_for_all, or use the panel on the machine Hearth "
                      "runs on"),
        }, status=403)
    resp = _guarded(request, character)
    if resp is not None:
        return resp

    archived = bool(body.get("archived")) or \
        request.query.get("archived") in ("1", "true", "yes")
    try:
        path = verbs_mod.resolve_session_path(character, session, archived=archived)
    except verbs_mod.SessionPathError as exc:
        if exc.reason != "no such session":
            return web.json_response({"ok": False, "error": exc.reason}, status=400)
        if _already(character, session, archived=not archived):
            where = "archived" if not archived else "on the shelf"
            return web.json_response(
                {"ok": False, "error": f"no such session — that id is {where}"},
                status=404)
        if not verbs_mod.record_paths(character, session):
            return web.json_response({"ok": False, "error": "no such session"},
                                     status=404)
        path = None  # a sweep someone half-finished: no file, records still banked

    plan = verbs_mod.destroy_plan(character, session, archived=archived)
    confirm_with = _confirm_with(path, session)

    if "confirm" not in body:
        return web.json_response({
            "ok": True, "destroyed": False, "plan": plan,
            "confirm_with": confirm_with,
            "warning": ("destroy is permanent: it unlinks the file and forgets "
                        "the session's memory record, epochs and indexed facts "
                        "in one act"),
        })
    if str(body.get("confirm") or "") != confirm_with:
        return web.json_response(
            {"ok": False, "destroyed": False, "error": "confirmation did not match"},
            status=409)

    memory = {"forgotten": False, "index": "no-record"}
    if plan["memory"]["records"]:
        from .. import curation as curation_mod  # lazy: mirrors the package gate idiom

        result = await curation_mod.forget_session(request.app, character, session)
        status = int(result.pop("status", 200))
        if status != 200:
            # Memory first, and the file is still here: nothing is half-done.
            return web.json_response(
                {"ok": False, "destroyed": False, "stage": "memory",
                 "error": result.get("error") or "the memory forget did not finish"},
                status=status)
        memory = {"forgotten": bool(result.get("forgotten")),
                  "index": result.get("index") or "none"}
        if result.get("hint"):
            memory["hint"] = result["hint"]

    try:
        removed = verbs_mod.destroy_file(path) if path is not None else False
    except OSError as exc:
        return web.json_response(
            {"ok": False, "destroyed": False, "stage": "file",
             "error": f"the session file could not be removed ({type(exc).__name__})"},
            status=500)
    logger.info("[sessions] destroyed {}/{}", character, session)
    return web.json_response({
        "ok": True, "destroyed": True, "file": removed, "memory": memory,
        "cannot_reach": list(verbs_mod.CANNOT_REACH),
    })
