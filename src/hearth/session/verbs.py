"""session/verbs.py — pure path logic for the session file verbs.

The routes in ``supervisor/routes/sessions.py`` are the web half; the thinking
lives here, so the fence can be unit-tested without an HTTP client at all
(the same split as ``hearth.weights`` beside ``supervisor/models``).

The one rule every verb shares is **confinement**: a session id is a name, not
a path. It has to match ``SESSION_ID_RE``, it may only name a file directly
inside the companion's sessions dir (or its ``.archive/`` sibling), the file
may not be a symlink, and the resolved candidate has to still be under the
resolved root. Anything else raises ``SessionPathError`` — whose reason string
never carries the path, because a refusal must not become a way to map the
disk (the same posture as the shelf, which answers ids and never locations).

``reveal_argv`` is a fixed argv, never a shell string: the only variable part
is the path the fence already approved.

Deposit adds the second gate, and it is a gate on *content shape* rather than
on a path: ``validate_session_payload`` is what stands between an uploaded file
and the companion's sessions dir. The store's own ``load()`` checks only that
the file is an object with a messages list, which is the right check for a file
Hearth itself wrote; a file arriving from a browser has to answer more — that it
is this companion's, that it names a voice this companion actually has and a
persona file that exists, that every message carries a role the pipeline knows,
and that no system message rides in (the store never keeps one, so accepting one
would be a way to smuggle a prompt past the persona). The reasons it raises are
about the file, never about what the file says.

Archive is the soft verb: ``archive_session`` moves a file into the companion's
``.archive/`` and ``unarchive_session`` moves it back, both through the same
fence and both by ``os.replace`` — nothing is ever removed and nothing is ever
overwritten. ``live_guard`` is the rule the mutating verbs share: while a
companion is up, its WHOLE shelf is read-only, because the supervisor cannot
name the single file the bot holds.

Destroy is the one hard verb, and the two functions it needs live here in
their honest shape. ``destroy_plan`` answers what the act would take — the
file, the memory record and its compaction epochs, the backend document behind
them — and, in the same breath, ``CANNOT_REACH``: the places a trace may
survive that are not Hearth's to promise. ``destroy_file`` is the unlink
itself, and it returns False rather than raising when the file is already
gone, because a half-finished sweep must be finishable.

Rename comes in two shapes, and the difference between them is who else knows
the id. ``set_session_title`` is the normal one: a display name in the file's
own metadata, free to change as often as anyone likes, because nothing outside
the file reads it. ``rename_session_file`` moves the file itself, and the id IS
the key the memory record, the compaction queue and the hold marker use — so it
is offered only when ``session_references`` comes back empty, and refused with
the reasons when it does not.
"""

from __future__ import annotations

import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import Optional

#: A session id is a file stem, never a path: the same shape the store's own
#: verbs accept (``/admin/compact`` validates with this spelling too).
SESSION_ID_RE = re.compile(r"[A-Za-z0-9._-]+")

#: The archive convention (build-session-file-management §6.3), a dot-dir
#: beside the sessions themselves so the shelf answers "archived" from
#: location rather than from a field. Spelled here AND in ``session_store``
#: (which must not import this module to walk its own shelf); a test pins the
#: two spellings equal.
ARCHIVE_DIR = ".archive"

#: The biggest upload a deposit will read. A 100k-token sitting is about half a
#: megabyte of JSON, so this is roughly sixty of the longest conversations
#: anyone has had here — large enough never to be the thing that refuses a real
#: session, small enough that a mistaken upload is refused before it is parsed.
#: The facade's own body cap sits above it, so this is the number that answers.
MAX_DEPOSIT_BYTES = 32 * 1024 ** 2

#: The roles the pipeline writes and the store keeps. ``system`` is absent on
#: purpose: it is dropped, not refused (see ``validate_session_payload``).
DEPOSIT_ROLES = ("user", "assistant", "developer")

#: The memory postures a sitting can carry (``--memory``).
MEMORY_MODES = ("full", "recall-only", "off")

#: The current session schema; 1 is the pre-2026-08 shape (no character/persona).
DEPOSIT_SCHEMAS = (1, 2)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class SessionPathError(ValueError):
    """A session id that the fence refuses. ``reason`` never names a path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SessionPayloadError(ValueError):
    """An uploaded file the deposit gate refuses.

    ``reason`` is written for the person who picked the file, and it describes
    the FILE — never a line of what the file says, and never a path.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: A display title is a label a person types, so its rules are a person's
#: rules: it has to say something, it has to fit on a shelf row, and it may
#: not carry control characters (which would let a title dress itself up as
#: two lines, or as something it is not, wherever it is printed).
TITLE_MAX_CHARS = 120


class SessionTitleError(ValueError):
    """A display title the rename gate refuses. ``reason`` describes the title
    the person typed — never the file, never a path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def valid_session_id(session: Optional[str]) -> bool:
    """True for a plain file stem: no leading dot, no ``..``, no separators."""
    if not session:
        return False
    if session.startswith("."):
        return False
    if ".." in session:
        return False
    return bool(SESSION_ID_RE.fullmatch(session))


def sessions_root(character: str) -> Path:
    """The companion's sessions dir — ``characters/<character>/sessions/``."""
    from hearth.session import session_store  # lazy: keeps this module import-light

    return session_store.companion_sessions_dir(character)


def _fenced_path(character: str, session: str, *, archived: bool = False) -> Path:
    """The fence itself, with nothing said about whether the file exists.

    Id shape, no symlink at the candidate, and the resolved candidate still
    under the resolved root. Both public gates below are this check plus one
    question about existence — read means "and it is there", reserve means
    "and it is not" — so the confinement rule is written once.
    """
    if not valid_session_id(session):
        raise SessionPathError("invalid session id")
    root = sessions_root(character)
    base = root / ARCHIVE_DIR if archived else root
    candidate = base / f"{session}.json"
    if candidate.is_symlink():
        raise SessionPathError("session file is a link — refused")
    try:
        real_root = root.resolve()
        real = candidate.resolve()
    except OSError:
        raise SessionPathError("session file could not be resolved") from None
    if real_root not in real.parents:
        raise SessionPathError("session file is outside this companion's sessions")
    return real


def resolve_session_path(character: str, session: str, *, archived: bool = False) -> Path:
    """The one gate every file verb passes through.

    Returns the real path of ``<sessions root>[/.archive]/<session>.json``, or
    raises ``SessionPathError``. Refuses: a malformed id, a symlink at the
    candidate, a candidate that resolves outside the root, and anything that is
    not a regular file.
    """
    real = _fenced_path(character, session, archived=archived)
    if not real.is_file():
        raise SessionPathError("no such session")
    return real


def reserve_session_path(character: str, session: str, *, archived: bool = False) -> Path:
    """The same fence, for a name that must NOT be taken yet.

    Deposit writes under a freshly minted id and must never overwrite a saved
    conversation, so the reservation is the refusal: anything already at the
    name — a file, a directory, a dangling link — raises. This verb only ever
    hands back a path to write; it never moves or removes what it finds.
    """
    real = _fenced_path(character, session, archived=archived)
    if real.exists() or real.is_symlink():
        raise SessionPathError("session id already exists")
    return real


# ── archive / unarchive: the soft verb, a move and never a delete ────────────

def archive_session(character: str, session: str) -> Path:
    """Move a saved session into the companion's ``.archive/`` and return where
    it landed.

    Both halves pass the fence: the source has to BE there (unarchived), and the
    destination must NOT be taken. A name that exists on both sides raises
    rather than resolving the clash — an archive that could overwrite an archive
    would be a delete wearing the soft verb's name. The ``.archive/`` dir is
    created 0700 like the sessions dir itself, and the move is ``os.replace``:
    same filesystem, atomic, the bytes never read.
    """
    from hearth.session import session_store  # lazy: keeps this module import-light

    src = resolve_session_path(character, session)
    dest = reserve_session_path(character, session, archived=True)
    session_store.ensure_dir(dest.parent)
    os.replace(src, dest)
    return dest


def unarchive_session(character: str, session: str) -> Path:
    """The mirror: move an archived session back onto the shelf.

    Same two questions in the other order — it has to be in ``.archive/``, and
    the live name must be free.
    """
    src = resolve_session_path(character, session, archived=True)
    dest = reserve_session_path(character, session)
    os.replace(src, dest)
    return dest


# ── destroy: the one hard verb, and the honest half of it ───────────────────

#: What destroy CANNOT reach. Fixed text, said out loud in the preview and
#: again in the answer, because the whole point of the verb is confidentiality
#: and a verb that quietly leaves copies behind is worse than no verb at all.
#: Each line is a place a trace of the sitting may still exist after destroy
#: has done everything it can do.
CANNOT_REACH = (
    "lines in logs/bot.log (timings and ids only, never words)",
    "the model server's prompt cache in memory (cleared by its next restart)",
    "copies outside Hearth: Time Machine and APFS snapshots, your own backups, mirrors",
)


def record_paths(character: str, session: str) -> list:
    """Every memory record file of one session — the bare record plus each
    compaction epoch. Empty for a sitting that banked nothing (``off`` or
    ``recall-only``), and empty for a sweep somebody already half-finished."""
    from hearth.memory import records as records_mod  # lazy: import-light

    try:
        return list(records_mod.epoch_paths(records_mod.records_dir(character), session))
    except OSError:
        return []


def destroy_plan(character: str, session: str, *, archived: bool = False) -> dict:
    """What destroying this session would take with it — and what it would not.

    Pure: it reads locations and counts, never a line of the session or of the
    record. A file that is not there is not an error here — a sweep can be
    half-done (the file unlinked by hand, the memory record still banked), and
    destroy has to be able to finish it — so ``file`` is False and the plan
    still names the records that remain. A malformed id still raises.
    """
    try:
        resolve_session_path(character, session, archived=archived)
        present = True
    except SessionPathError as exc:
        if exc.reason != "no such session":
            raise
        present = False
    paths = record_paths(character, session)
    return {
        "session_id": session,
        "archived": bool(archived),
        "file": present,
        "memory": {"records": len(paths), "backend": bool(paths)},
        "cannot_reach": list(CANNOT_REACH),
    }


def destroy_file(path) -> bool:
    """Unlink one session file. True when this call removed it, False when it
    was already gone — destroy is re-runnable, so a missing file is an outcome
    rather than a failure."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True


# ── the live-session guard: whose shelf is read-only right now ───────────────

def live_guard(character: str, bot_state: str, active_character) -> Optional[str]:
    """The refusal reason when a mutating verb reaches the RUNNING companion's
    shelf, or None when the verb may proceed.

    The guard is coarse on purpose. The supervisor knows that a bot is up and
    which companion ``active.toml`` points at, but it does NOT know which
    session id that bot holds — a ``--new`` sitting mints its id inside the
    child and never tells the supervisor. So the honest fence is the whole
    shelf: while the active companion is up, every one of its session files is
    read-only, because any one of them could be the file being written. Another
    companion's shelf is untouched by that — nothing is holding it.

    Process truth, not page state: ``bot_state`` comes from the child's own
    ``status()`` (an adopted desk bot counts as running just as a managed one
    does), and anything that is not ``"down"`` — starting, running, stopping —
    counts as up.
    """
    if str(bot_state or "down") == "down":
        return None
    if active_character is None or character != active_character:
        return None
    return (f"{character} is running — stop the companion first; its session "
            f"files are read-only while it is up")



# ── the deposit gate: is this file a session, and is it THIS companion's? ────

def validate_session_payload(data, *, character: str) -> dict:
    """Check an uploaded session object and return what should be written.

    Raises ``SessionPayloadError`` with a reason about the file. What it checks,
    in the order a person would ask it:

    * it is a JSON object, and its ``schema`` is one this Hearth understands;
    * ``messages`` is a list of role/text objects. Every ``system`` message is
      **dropped** rather than refused — the store never keeps one, so a file
      carrying one is an older export, not an attack, and letting it through
      unchanged would be the way to put words in the persona's place. Any other
      role is refused, because the pipeline would not know what to do with it;
    * ``character``, when the file names one, must be this companion — a
      conversation is not transferable between companions, it is a record of
      one. A schema-1 file names none, and is stamped with the target instead;
    * ``voice`` must name a bundle this companion actually has, and ``persona``
      a persona file that exists — the load-side half of the voice binding: a
      session that resumes into a voice the companion cannot speak is a session
      that fails at the first turn instead of at the door;
    * ``memory_mode``, if stamped, is one of the three postures.

    ``held`` is forced true. A deposit is a deliberate act, and ``held`` is
    exactly what exempts a file from the ephemeral sweep, so a deposited
    recall-only sitting would otherwise be swept by the next fresh start.
    ``origin`` is stamped ``"deposit"`` so the shelf can say where a session
    came from. Unknown top-level keys are dropped: what is written is what this
    version of the store writes, and nothing else rides in.
    """
    from hearth.config import config_loader  # lazy: keeps this module import-light

    if not isinstance(data, dict):
        raise SessionPayloadError("not a session file")
    schema = data.get("schema", 1)
    if schema not in DEPOSIT_SCHEMAS or isinstance(schema, bool):
        raise SessionPayloadError("session schema is not one this version reads")

    raw = data.get("messages")
    if not isinstance(raw, list):
        raise SessionPayloadError("session has no list of messages")
    messages = []
    for m in raw:
        if not isinstance(m, dict):
            raise SessionPayloadError("a message is not an object")
        role = m.get("role")
        if role == "system":
            continue  # dropped, never stored — the persona is not the file's to carry
        if role not in DEPOSIT_ROLES:
            raise SessionPayloadError("a message carries a role this version does not read")
        if not isinstance(m.get("content"), str):
            raise SessionPayloadError("a message has no text")
        messages.append({"role": role, "content": m["content"]})

    owner = data.get("character")
    if owner is not None and owner != character:
        raise SessionPayloadError("session belongs to another companion")

    voice = data.get("voice")
    if not isinstance(voice, str) or voice not in config_loader.list_voices(character):
        raise SessionPayloadError("session names a voice this companion does not have")

    persona = data.get("persona")
    if persona in (None, ""):
        persona = "default"
    if not isinstance(persona, str):
        raise SessionPayloadError("session names a persona this companion does not have")
    try:
        exists = config_loader.persona_path(character, persona).is_file()
    except Exception:  # noqa: BLE001 — an invalid variant name is a refusal, not a 500
        exists = False
    if not exists:
        raise SessionPayloadError("session names a persona this companion does not have")

    memory_mode = data.get("memory_mode")
    if memory_mode is not None and memory_mode not in MEMORY_MODES:
        raise SessionPayloadError("session names a memory mode this version does not read")

    now = _now_iso()
    started = data.get("started")
    updated = data.get("updated")
    # Key order mirrors SessionStore.snapshot(), so a deposited file and a file
    # this Hearth wrote read the same way side by side.
    payload = {
        "schema": 2,
        "voice": voice,
        "persona": persona,
        "started": started if isinstance(started, str) else now,
        "updated": updated if isinstance(updated, str) else now,
        "held": True,
        "character": character,
        "origin": "deposit",
    }
    model = data.get("model")
    if isinstance(model, str):
        payload["model"] = model
    digest = data.get("prompt_sha256")
    if isinstance(digest, str) and _SHA256_RE.fullmatch(digest):
        payload["prompt_sha256"] = digest
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        payload["name"] = name.strip()
    if memory_mode is not None and memory_mode != "full":
        # Written only when non-default, the same rule snapshot() follows.
        payload["memory_mode"] = memory_mode
    payload["messages"] = messages
    return payload


def dropped_system_messages(data, payload: dict) -> int:
    """How many system messages the gate left behind — a count, never a word of
    them. The route says the number out loud so a person can see that the file
    they picked was not stored whole."""
    raw = (data or {}).get("messages") if isinstance(data, dict) else None
    return max(0, len(raw or []) - len(payload.get("messages") or []))


def _now_iso() -> str:
    from hearth.session import session_store  # lazy: one spelling of "now"

    return session_store._now_iso()


# ── rename: the title anyone may change, and the id almost nobody may ────────

def normalize_title(title) -> Optional[str]:
    """The title as it will be stored, or None for "remove the title".

    An empty string (or whitespace, or nothing at all) is the removal: a person
    clearing the box means "this session has no title", not "this session is
    called nothing". Anything else is stripped and then has to answer the two
    questions above — length, and no control characters. Raises
    ``SessionTitleError``.
    """
    if title is None:
        return None
    if not isinstance(title, str):
        raise SessionTitleError("a title has to be text")
    clean = title.strip()
    if not clean:
        return None
    if len(clean) > TITLE_MAX_CHARS:
        raise SessionTitleError(
            f"a title is at most {TITLE_MAX_CHARS} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
        raise SessionTitleError("a title cannot carry control characters")
    return clean


def set_session_title(character: str, session: str, title,
                      *, archived: bool = False) -> Optional[str]:
    """Set (or remove) one session's display title. Returns what was stored.

    **This is the ONE verb in this module that parses a session file.** Every
    other one moves, counts or unlinks bytes it never opens; this one has to
    read the object to write a field back into it. So the rule it keeps is
    narrower than "never read": what it parses it never returns, never logs and
    never inspects. ``messages`` is carried from the loaded dict to the written
    dict untouched and unread — it is not counted, not filtered, not looked at.
    The route above it answers the id and the title, and nothing else.

    The write goes through ``session_store._atomic_write_json``, the same
    writer the store itself uses (tmp at 0600 → fsync → ``os.replace``), so a
    crash mid-rename cannot leave a half-written conversation. Key order is
    preserved exactly as the file had it, and a title being added for the first
    time lands immediately before ``messages`` — so a titled file and a file
    this Hearth wrote read the same way side by side, and clearing a title
    leaves the file byte-identical to one that never had one.
    """
    import json

    from hearth.session import session_store  # lazy: keeps this module import-light

    clean = normalize_title(title)
    path = resolve_session_path(character, session, archived=archived)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise SessionPathError(
            f"the session file could not be read ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise SessionPathError("that file is not a session")

    out = {}
    for key, value in data.items():
        if key == "title":
            continue  # re-placed below, so its position never depends on history
        if key == "messages" and clean is not None:
            out["title"] = clean
        out[key] = value
    if clean is not None and "title" not in out:
        out["title"] = clean  # a file with no messages key: the title still lands
    session_store._atomic_write_json(path, out)
    return clean


def session_references(character: str, session: str) -> list:
    """Everything OUTSIDE the file that knows this session by its id.

    Human-readable reasons, never paths — the same posture as every refusal
    here. Empty means the id is the file's private business and the file may be
    renamed; anything in the list means a rename would orphan whatever is
    named, so the title is the rename that is offered instead.

    What counts as a reference is a hard reference: something that would be
    LEFT BEHIND, pointing at an id no file answers to any more.

    * the memory record and its compaction epochs
      (``records/<id>.json``, ``<id>.c*.json``) — and with them the backend
      document, which is keyed by the same id, so the record standing is the
      backend document standing;
    * a compaction breadcrumb in the queue (``<character>.<id>.request`` and
      its ``.running`` / ``.failed`` siblings) — a queued run names the pair
      and would look for a file that is gone;
    * the hold marker, when it names this id — the next close would write to
      the name it holds.

    What does NOT count, deliberately: a line in ``bot.log`` (timings and ids,
    already historical — a log is a record of what happened under a name, not a
    pointer to a file), the panel's intent slot (re-read every turn), and a fork
    or a copy of the conversation somewhere else (its own file with its own
    id). Those are stale-only: nothing breaks when the id moves, they simply
    keep saying what was true when they were written.
    """
    from hearth.session import compact_trigger, session_store  # lazy: import-light

    reasons = []

    paths = record_paths(character, session)
    if paths:
        epochs = sum(1 for p in paths if Path(p).stem != session)
        if epochs == 1:
            reasons.append("a memory record (and 1 compaction epoch)")
        elif epochs:
            reasons.append(f"a memory record (and {epochs} compaction epochs)")
        else:
            reasons.append("a memory record")

    try:
        qdir = compact_trigger.queue_dir()
        queued = any(p.suffix in (".request", ".running", ".failed")
                     for p in qdir.glob(f"{character}.{session}.*"))
    except OSError:
        queued = False
    if queued:
        reasons.append("a parked compaction request")

    try:
        marker = session_store.marker_path(sessions_root(character))
        held = marker.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        held = ""
    if held and held == session:
        reasons.append("the hold request names it")

    return reasons


def rename_session_file(character: str, session: str, new_id: str,
                        *, archived: bool = False) -> Path:
    """Move a session file to a new id, and refuse when anything else knows the
    old one.

    Both halves of the move pass the fence — the source has to be there, the
    destination must not be taken — and the shelf side never changes: an
    archived session is renamed inside the archive, a live one on the shelf.
    The move itself is ``os.replace``: same filesystem, atomic, the bytes never
    read.

    ``SessionStore.rename`` (the hold path's own bare rename, used at close
    time when a ``--hold`` names the session) is deliberately left alone: it
    runs inside the bot, on a session nothing else can be referencing yet.
    """
    if not valid_session_id(new_id):
        raise SessionPathError("invalid session id")
    refs = session_references(character, session)
    if refs:
        raise SessionPathError(
            "this session is referenced by " + ", ".join(refs))
    src = resolve_session_path(character, session, archived=archived)
    if new_id == session:
        return src
    dest = reserve_session_path(character, new_id, archived=archived)
    os.replace(src, dest)
    return dest


# ── who is asking: same machine, or across the tailnet? ──────────────────────

def is_loopback_peer(remote: Optional[str]) -> bool:
    """True when the request's peer address is this machine itself.

    Reveal only means something when the browser and Hearth share a screen; a
    phone over the tailnet gets the download instead. Covers 127.0.0.0/8, ::1
    and the IPv4-mapped form ``::ffff:127.x.x.x``.
    """
    if not remote:
        return False
    host = str(remote).strip()
    if host.startswith("[") and "]" in host:  # [::1]:54321
        host = host[1:host.index("]")]
    host = host.split("%", 1)[0]  # scope id (fe80::1%en0)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return bool(addr.is_loopback)


# ── reveal: a fixed argv, macOS only ─────────────────────────────────────────

def reveal_argv(path: Path) -> list:
    """The exact command that reveals a file in the Finder — fixed argv, no
    shell. Empty on any other platform, and the route then answers 501."""
    if sys.platform != "darwin":
        return []
    return ["/usr/bin/open", "-R", str(path)]
