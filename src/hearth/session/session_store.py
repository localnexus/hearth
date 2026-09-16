"""session_store.py — Tier 1 session continuity: per-turn snapshot + resume.

The entire durable conversational state is ``context.messages`` — a plain list of
role/content dicts (llm_context.py:122, 228). LM Studio is stateless per request,
so preserving that list IS continuity. The system prompt is injected per-request
from settings (base_llm.py:308-312) and is NEVER in ``messages``, so it is never
persisted here → zero duplication on reload.

Privacy: a session file (``characters/<name>/sessions/*.json`` under the data root)
is a full plaintext transcript.
It is local-only, gitignored, dir ``0700`` / files ``0600``.
Ephemeral by default: an unkept working file is deleted at a graceful stop.
Kept = ``held``. A file without a ``retain`` key is read as kept, unless it is
stamped **recall-only** (the old ephemeral class). The hold marker means "keep,
and name it" (for one release).
The snapshot+os.replace model closes the file handle every turn, so
delete frees the file cleanly (no deleted-but-open-handle trap). Transcripts are
never exposed over the web ``/``.

CLI (used by start.sh / stop.sh; keeps the bash thin and the logic unit-tested):
    python session_store.py list
    python session_store.py request-hold [name|path] [--cwd folder]  # bot running: drop marker, bot honors in finally
    python session_store.py hold [name|path] [--cwd folder]          # no bot: name/keep the newest unnamed session
    python session_store.py discard-ephemeral      # sweep unkept leftovers (kept conversations are never touched)
    python session_store.py discard-held [name|--all]

Locator rule: a bare name (no separator, no leading "." or "~") saves under the sessions
folder as today; anything else (an absolute path, "./x", "~/x", "a/b") is a path locator —
"this location, literally" — anchored at the operator's own folder (--cwd) when relative.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

SCHEMA = 2  # 2 (2026-08): adds "character" + "persona"; schema-1 files load as persona "default"
_HOLD_MARKER = ".hold-request"  # stop.sh --hold drops this; the bot honors + consumes it in finally
_RETAIN_MARKER = ".retain-request"  # the late switch: thrown while the bot runs, consumed in finalize
ORPHAN_EXPIRY_DAYS = 7  # an unkept orphan an unclean death left behind is quarantined this long
ARCHIVE_DIR = ".archive"  # the soft verb's dot-dir (session/verbs.py owns the move);
                          # hidden from every walker below, so an archived session
                          # is out of resume and out of the fresh-start sweep

DIR_MODE = 0o700
FILE_MODE = 0o600


# ── where sessions live: per companion, under the data root ──────────────────
#
# DATA/characters/<character>/sessions/ — the companion's own directory, so a
# conversation history travels (and is erased) with the companion it belongs to.
# Every function below takes an explicit `sessions_dir`; None means "the ACTIVE
# companion's" (resolved from config/active.toml at call time — the CLI verbs used
# by start.sh / stop.sh have no other way to know which companion is live).

def companion_sessions_dir(character: Optional[str] = None) -> Path:
    from hearth.config import config_loader  # lazy: keeps this module import-light
    if character is None:
        character = config_loader.load_active_selection()["character"]
    return config_loader.companion_state_dir(character, "sessions")


def all_sessions_dirs() -> list:
    """Every companion's sessions dir that exists under the data root (for `list`)."""
    from hearth.config import config_loader
    root = config_loader._DATA / "characters"  # the live anchor (tests relocate it)
    return sorted(p for p in root.glob("*/sessions") if p.is_dir())


def default_sessions_dir(character: Optional[str] = None) -> Path:
    """The dir a session for `character` saves/lists to when no explicit dir is given.

    `[session] dir` (config/active.toml) is a lever for the ACTIVE companion only —
    the pair changes together (re-anchoring, 2026-09-13): every OTHER companion keeps
    its built-in per-companion dir regardless of the setting. `character=None` means
    "the active one", same convention as `companion_sessions_dir`.
    """
    from hearth.config import config_loader
    active = config_loader.load_active_selection()["character"]
    if character is None or character == active:
        configured = config_loader.load_active_session_dir()
        if configured is not None:
            return configured
    return companion_sessions_dir(character)


def _dir(sessions_dir) -> Path:
    return Path(sessions_dir) if sessions_dir is not None else default_sessions_dir()


# ── helpers ──────────────────────────────────────────────────────────────────

def prompt_sha256(system_instruction: str) -> str:
    """Stable SHA-256 of the persona prompt, for drift detection on resume."""
    return hashlib.sha256(system_instruction.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def new_session_id() -> str:
    """Session id = local session-start ISO timestamp (filesystem-safe ':' → '-')."""
    return "session-" + time.strftime("%Y-%m-%dT%H-%M-%S", time.localtime())


def classify_locator(arg: str) -> str:
    """"path" iff `arg` names a location outside the sessions folder — absolute,
    or carrying a separator, or a leading "." / "~" — else "name". A trailing
    ".json" alone does not make a bare name a path."""
    if Path(arg).is_absolute() or os.sep in arg or arg.startswith((".", "~")):
        return "path"
    return "name"


def resolve_save_locator(arg: str, sessions_dir: Optional[Path] = None, *,
                         cwd: Optional[Path] = None) -> Path:
    """A save-time locator → the file it names. "name" behaves as it always has
    (under `sessions_dir`); "path" means "this location, literally" — anchored
    at `cwd` (default the process cwd) when relative, `.json` appended when the
    name part lacks it."""
    if classify_locator(arg) == "name":
        return _dir(sessions_dir) / (arg if arg.endswith(".json") else f"{arg}.json")
    p = Path(arg).expanduser()
    if not p.is_absolute():
        p = (cwd or Path.cwd()) / p
    p = p.resolve()
    if not p.name.endswith(".json"):
        p = p.with_name(p.name + ".json")
    return p


def out_of_tree_warning(target: Path, default_dir: Path) -> Optional[str]:
    """None when `target` resolves under `default_dir`; else a one-line, never-blocking
    warning (naming the enclosing git repo, when there is one) — writing outside the
    sessions folder is allowed, it just isn't silent."""
    target = Path(target).resolve()
    default_dir = Path(default_dir).resolve()
    try:
        target.relative_to(default_dir)
        return None
    except ValueError:
        pass
    msg = (f"[session] warning: {target} is outside the sessions folder; "
           "permissions travel with the file, the ignore rules do not")
    for parent in target.parents:
        if (parent / ".git").is_dir():
            msg += f" — it is inside the git repository at {parent}"
            break
    return msg


def ensure_parent(target: Path) -> None:
    """mkdir the parent of a save-time target (0700, best effort) — perms travel to a
    path locator's own folder even when it is nowhere near the sessions folder."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, DIR_MODE)
    except OSError:
        pass


def ensure_dir(sessions_dir: Optional[Path] = None) -> Path:
    """Create the sessions dir mode 0700 (and tighten perms if it pre-existed looser)."""
    sessions_dir = _dir(sessions_dir)
    sessions_dir.parent.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(mode=DIR_MODE, exist_ok=True)
    try:
        os.chmod(sessions_dir, DIR_MODE)
    except OSError:
        pass
    return sessions_dir


def _persistable_messages(messages) -> list:
    """Keep only JSON-plain standard messages.

    The aggregators write ``{"role": ..., "content": ...}`` dicts (roles: user /
    assistant / developer) — all JSON-serializable. Provider-specific messages
    (``LLMSpecificMessage``, e.g. tool calls) are objects, not dicts, and are NOT
    JSON-serializable — none occur in this tool-free pipeline, but we skip them
    defensively. ``system`` is filtered out too: it lives in settings, never in
    messages, so it can never be re-added on reload.
    """
    out = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "system":
            continue
        out.append(m)
    return out


def _atomic_write_json(path: Path, obj: dict) -> None:
    """Write ``<path>.tmp`` (0600 from creation) then os.replace onto the real name.

    Atomic same-fs rename → a mid-write crash can't corrupt the live file, and the
    handle is closed before rename (no long-lived append handle to strand on delete).
    """
    path = Path(path)
    ensure_parent(path)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, FILE_MODE)
    except OSError:
        pass


# ── the store (per-run object; snapshots every completed turn) ───────────────

@dataclass
class SessionStore:
    """One live session; ``snapshot()`` is called after every completed turn."""

    session_id: str
    model: str
    voice: str
    prompt_sha256: str
    sessions_dir: Optional[Path] = None   # None → the companion's dir (character, else active)
    started: str = field(default_factory=_now_iso)
    name: Optional[str] = None
    held: bool = False
    retain: bool = False  # the write switch: False = this working file is deleted at a
                          # graceful stop; True = kept on the shelf (and then held)
    forked_from: Optional[str] = None  # the kept file this working file was copied from
    banked_from: int = 0  # watermark: messages up to this index were already
                          # remembered when the original was kept; the memory
                          # tail starts after it
    title: Optional[str] = None  # a person's label (the same key verbs.set_session_title writes)
    character: Optional[str] = None
    persona: str = "default"              # which persona file was live ("default" = persona.md)
    memory_mode: str = "full"             # the sitting's memory posture (--memory); stamped into
                                          # snapshots when not "full" so a crash orphan resumed
                                          # later inherits it instead of getting banked by default
    _path_override: Optional[Path] = field(default=None, repr=False, compare=False)
    # set by rename() when a hold names the session with a PATH locator: the
    # file then lives outside sessions_dir, so the id→path formula no longer applies.

    def __post_init__(self) -> None:
        if self.sessions_dir is None:
            self.sessions_dir = companion_sessions_dir(self.character)

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        return Path(self.sessions_dir) / f"{self.session_id}.json"

    def snapshot(self, messages) -> None:
        """Atomically persist the full context after a completed turn."""
        ensure_dir(self.sessions_dir)
        payload = {
            "schema": SCHEMA,
            "model": self.model,
            "voice": self.voice,
            "persona": self.persona,
            "prompt_sha256": self.prompt_sha256,
            "started": self.started,
            "updated": _now_iso(),
            "held": self.held,
            "retain": self.retain,
        }
        if self.forked_from:
            payload["forked_from"] = self.forked_from
            payload["banked_from"] = self.banked_from
        if self.title:
            payload["title"] = self.title
        if self.character:
            payload["character"] = self.character
        if self.name:
            payload["name"] = self.name
        if self.memory_mode != "full":
            # Written only when non-default: a full sitting's files stay
            # byte-identical to before the stamp existed.
            payload["memory_mode"] = self.memory_mode
        payload["messages"] = _persistable_messages(messages)
        _atomic_write_json(self.path, payload)

    def rename(self, new_id: str) -> None:
        """Move the file to the locator's target (used when a hold names the session).

        ``new_id`` is routed through ``resolve_save_locator``: a bare name behaves as
        before (moves within ``sessions_dir``); a path locator moves the file to that
        location, literally, and ``self.path`` tracks it via ``_path_override``.
        """
        old = self.path
        target = resolve_save_locator(new_id, self.sessions_dir)
        self.session_id = target.stem
        self._path_override = target if target.parent != Path(self.sessions_dir) else None
        new = self.path
        if old.exists() and old != new:
            ensure_parent(new)
            os.replace(old, new)

    def delete(self) -> bool:
        """True-delete this session file (this sensitive class is deleted, not retained)."""
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False


# ── load / list / resolve ────────────────────────────────────────────────────

def load(path) -> dict:
    """Load + validate a session file. Raises ValueError on malformed shape."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError(f"malformed session file: {path}")
    return data


def fork_session(source: Path, *, model: str, voice: str, prompt_sha256: str,
                 character: Optional[str] = None, persona: str = "default",
                 sessions_dir: Optional[Path] = None) -> tuple:
    """Resume as a fork: a kept file is immutable to sittings, so resuming it
    copies it into a fresh working file rather than writing the original in
    place. ``source`` is opened for reading only. Returns
    ``(store, source_data)`` — the new store (already snapshotted, so the
    working file exists from the first second) and the source's loaded data
    (for the caller's drift warnings / descriptor)."""
    data = load(source)
    store = SessionStore(
        session_id=new_session_id(),
        model=model,
        voice=voice,
        prompt_sha256=prompt_sha256,
        sessions_dir=sessions_dir,
        character=character,
        persona=persona,
        started=data.get("started") or _now_iso(),
        retain=False,
        held=False,
        name=None,
        forked_from=Path(source).stem,
        banked_from=len(data.get("messages") or []),
        title=data.get("title") or None,
    )
    store.snapshot(data.get("messages") or [])
    return store, data


@dataclass
class SessionMeta:
    """Metadata-ONLY view for the picker/guard — NEVER carries message content."""

    path: Path
    session_id: str
    model: Optional[str]
    voice: Optional[str]
    name: Optional[str]
    held: bool
    started: Optional[str]
    updated: Optional[str]
    turns: int
    persona: str = "default"
    character: Optional[str] = None
    memory_mode: str = "full"  # the sitting's stamped posture ("full" when unstamped)
    retain: bool = True  # the write switch as read back; a held file is always kept
    title: Optional[str] = None  # the display name a person typed (session/verbs
                                 # set_session_title); None (unwritten) on every
                                 # file nobody has renamed, so those stay
                                 # byte-identical. `name` is the older, narrower
                                 # field the hold path writes as the FILE stem
    origin: Optional[str] = None  # "deposit" for a file brought in from outside;
                                  # None (unwritten) for a session born here, so
                                  # existing files stay byte-identical
    archived: bool = False  # NOT a file field — it is the LOCATION (.archive/),
                            # answered by where list_sessions found the file
    forked_from: Optional[str] = None  # the kept file this one was copied from, if any
    banked_from: int = 0  # watermark: messages already remembered before the fork


def _meta_of(p: Path, *, archived: bool = False):
    """One session file → its SessionMeta, or None when it is not readable as
    one. Content is parsed to count turns and is never carried out."""
    try:
        data = load(p)
    except (ValueError, json.JSONDecodeError, OSError):
        return None  # malformed → skip; fresh/other files unaffected
    msgs = data.get("messages", [])
    turns = sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user")
    held = bool(data.get("held", False))
    retain = data["retain"] if isinstance(data.get("retain"), bool) else (
        data.get("memory_mode") != "recall-only")
    retain = retain or held  # a held file is always kept
    return SessionMeta(
        path=p,
        session_id=p.stem,
        model=data.get("model"),
        voice=data.get("voice"),
        name=data.get("name"),
        held=held,
        started=data.get("started"),
        updated=data.get("updated"),
        turns=turns,
        persona=str(data.get("persona") or "default"),
        character=data.get("character"),
        memory_mode=str(data.get("memory_mode") or "full"),
        retain=retain,
        title=(data.get("title") or None) if isinstance(data.get("title"), str) else None,
        origin=data.get("origin") or None,
        archived=archived,
        forked_from=data.get("forked_from") or None,
        banked_from=int(data.get("banked_from") or 0),
    )


def list_sessions(sessions_dir: Optional[Path] = None, *,
                  include_archived: bool = False) -> list:
    """Return SessionMeta for every readable session file, newest first.

    Malformed/empty files are skipped (never crash). Content is never read out.

    ``.archive/`` is invisible by default, and that default is what keeps an
    archived conversation out of the resume picker and out of the fresh-start
    sweep: every other walker in this module (ephemeral_orphans, held_sessions,
    discard_*, hold_latest_orphan, the CLI's list) reads the shelf through this
    one function, and ``glob("*.json")`` does not descend. ``include_archived``
    adds the archived files, each marked ``archived=True`` — the only caller is
    the shelf route answering ``?archived=``.
    """
    sessions_dir = _dir(sessions_dir)
    metas = []
    if not sessions_dir.exists():
        return metas
    found = [(p, False) for p in sorted(sessions_dir.glob("*.json"))]
    if include_archived:
        archive = sessions_dir / ARCHIVE_DIR
        if archive.is_dir():
            found += [(p, True) for p in sorted(archive.glob("*.json"))]
    for p, archived in found:
        meta = _meta_of(p, archived=archived)
        if meta is not None:
            metas.append(meta)
    metas.sort(key=lambda m: (m.updated or m.started or ""), reverse=True)
    return metas


def ephemeral_orphans(sessions_dir: Optional[Path] = None) -> list:
    """Unkept working files an unclean death left behind — quarantined, not
    swept: shown at start, kept a week, then deleted."""
    return [m for m in list_sessions(sessions_dir) if not m.held and not m.retain]


def expired_orphans(sessions_dir: Optional[Path] = None, *,
                    now: Optional[datetime] = None) -> list:
    """The subset of ``ephemeral_orphans`` old enough to actually delete — a
    stamp (``updated`` or ``started``) that fails to parse is never treated as
    expired (no deleting on a guess)."""
    current = now if now is not None else datetime.now()
    out = []
    for m in ephemeral_orphans(sessions_dir):
        stamp = m.updated or m.started
        if not stamp:
            continue
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if current - when >= timedelta(days=ORPHAN_EXPIRY_DAYS):
            out.append(m)
    return out


def held_sessions(sessions_dir: Optional[Path] = None) -> list:
    return [m for m in list_sessions(sessions_dir) if m.held]


def resolve_resume_arg(arg: str, sessions_dir: Optional[Path] = None):
    """Resolve a ``--resume <arg>`` to a path: a "path" locator is tried directly
    (expanduser'ed, absolute or as given — retrieve-by-path); a "name" locator
    follows the three-step search: <arg>.json · session-<arg>.json · a session
    whose ``name`` field == arg. None if no match."""
    sessions_dir = _dir(sessions_dir)
    if classify_locator(arg) == "path":
        p = Path(arg).expanduser()
        return p if p.is_file() else None
    for cand in (
        sessions_dir / (arg if arg.endswith(".json") else f"{arg}.json"),
        sessions_dir / f"session-{arg}.json",
    ):
        # The name forms name a file DIRECTLY on the shelf: a "name" carrying a
        # separator (".archive/x") would otherwise be a way to resume out of the
        # archive, and archived sessions are hidden from resume.
        if cand.parent == sessions_dir and cand.exists():
            return cand
    for m in list_sessions(sessions_dir):
        if m.name == arg:
            return m.path
    return None


# ── discard verbs (all true-delete for this sensitive class) ────────

def discard_ephemeral(sessions_dir: Optional[Path] = None, *, expired_only: bool = True,
                      now: Optional[datetime] = None) -> list:
    """Fresh start: by default true-delete only EXPIRED unkept orphans (the
    quarantine window); every kept conversation is left untouched regardless.
    ``expired_only=False`` sweeps the whole unkept class — the old behavior,
    kept for an explicit act."""
    removed = []
    targets = expired_orphans(sessions_dir, now=now) if expired_only else ephemeral_orphans(sessions_dir)
    for m in targets:
        try:
            m.path.unlink()
            removed.append(m.session_id)
        except OSError:
            pass
    return removed


def discard_held(name: Optional[str] = None, sessions_dir: Optional[Path] = None) -> list:
    """Explicit discard-held verb: true-delete held sessions (all, or one by name/id)."""
    removed = []
    for m in held_sessions(sessions_dir):
        if name is None or m.name == name or m.session_id == name:
            try:
                m.path.unlink()
                removed.append(m.session_id)
            except OSError:
                pass
    return removed


# ── hold plumbing (stop-time intent → the bot's shutdown delete-decision) ────

def marker_path(sessions_dir: Optional[Path] = None) -> Path:
    return _dir(sessions_dir) / _HOLD_MARKER


def write_hold_request(name: Optional[str] = None, sessions_dir: Optional[Path] = None, *,
                       cwd: Optional[Path] = None) -> None:
    """stop.sh --hold (bot running): mark hold intent; the bot consumes it in finally.

    A path-locator name is stored as its FULLY RESOLVED ABSOLUTE target (so the bot's
    finalize, which reads the marker from its own working state, doesn't need to
    re-derive a relative anchor); a bare name is stored as given, same as today.
    """
    ensure_dir(sessions_dir)
    m = marker_path(sessions_dir)
    text = name or ""
    if name and classify_locator(name) == "path":
        target = resolve_save_locator(name, sessions_dir, cwd=cwd)
        warning = out_of_tree_warning(target, _dir(sessions_dir))
        if warning:
            print(warning)
        text = str(target)
    fd = os.open(m, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def read_hold_request(sessions_dir: Optional[Path] = None):
    """Consume the marker. Returns (requested: bool, name: Optional[str])."""
    m = marker_path(sessions_dir)
    if not m.exists():
        return (False, None)
    try:
        name = m.read_text(encoding="utf-8").strip() or None
    except OSError:
        name = None
    clear_hold_request(sessions_dir)
    return (True, name)


def clear_hold_request(sessions_dir: Optional[Path] = None) -> None:
    try:
        marker_path(sessions_dir).unlink()
    except FileNotFoundError:
        pass


def retain_marker_path(sessions_dir: Optional[Path] = None) -> Path:
    return _dir(sessions_dir) / _RETAIN_MARKER


def write_retain_request(retain: bool, name: Optional[str] = None,
                         sessions_dir: Optional[Path] = None, *,
                         session_id: Optional[str] = None) -> None:
    """The late switch: a supervisor throws this while the bot runs (the Stop
    card); finalize consumes it, and it is crash-safe because the next start
    can read it too. ``name`` is a LABEL (decision 008, "Id ≠ label") and is
    stored exactly as the person typed it — no locator resolution: finalize
    writes it to the file's ``title`` and never renames the file, so there is
    no path for a locator to name. The older hold marker keeps the other
    meaning (see write_hold_request) for one release.
    """
    ensure_dir(sessions_dir)
    m = retain_marker_path(sessions_dir)
    payload = {
        "retain": bool(retain),
        "name": (name.strip() or None) if isinstance(name, str) else None,
        "session_id": session_id or None,
        "at": _now_iso(),
    }
    text = json.dumps(payload)
    fd = os.open(m, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def read_retain_request(sessions_dir: Optional[Path] = None) -> Optional[dict]:
    """Consume the marker. Returns the parsed request dict, or None on a
    missing or unreadable marker."""
    m = retain_marker_path(sessions_dir)
    if not m.exists():
        return None
    try:
        data = json.loads(m.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    clear_retain_request(sessions_dir)
    if not isinstance(data, dict) or "retain" not in data:
        return None
    return data


def clear_retain_request(sessions_dir: Optional[Path] = None) -> None:
    try:
        retain_marker_path(sessions_dir).unlink()
    except FileNotFoundError:
        pass


def hold_latest_orphan(name: Optional[str] = None, sessions_dir: Optional[Path] = None, *,
                       cwd: Optional[Path] = None):
    """stop.sh --hold with no bot running: mark the newest not-yet-held session as
    held (optionally naming/renaming it — a path locator is honored, and the
    folder it needs travels with the write; see resolve_save_locator). This is
    "name it now" for the latest conversation — and the explicit keep for an
    unkept leftover. Returns its new id, or None if nothing qualifies."""
    orphans = [m for m in list_sessions(sessions_dir) if not m.held]
    if not orphans:
        return None
    m = orphans[0]  # newest first
    data = load(m.path)
    data["held"] = True
    data["retain"] = True
    target = m.path
    if name:
        data["name"] = name
        target = resolve_save_locator(name, sessions_dir, cwd=cwd)
        warning = out_of_tree_warning(target, _dir(sessions_dir))
        if warning:
            print(warning)
    _atomic_write_json(target, data)
    if target != m.path:
        try:
            m.path.unlink()
        except OSError:
            pass
    return target.stem


def keep_orphan(session_id: str, name: Optional[str] = None, sessions_dir: Optional[Path] = None, *,
                cwd: Optional[Path] = None) -> Optional[str]:
    """Promote ONE unkept orphan, by id (not "the newest"), onto the shelf —
    the Stop card's / CLI's "keep" verb. None when ``session_id`` does not name
    an unkept orphan (already kept, or no such session)."""
    orphans = {m.session_id: m for m in ephemeral_orphans(sessions_dir)}
    m = orphans.get(session_id)
    if m is None:
        return None
    data = load(m.path)
    data["held"] = True
    data["retain"] = True
    target = m.path
    if name:
        data["name"] = name
        target = resolve_save_locator(name, sessions_dir, cwd=cwd)
        warning = out_of_tree_warning(target, _dir(sessions_dir))
        if warning:
            print(warning)
    _atomic_write_json(target, data)
    if target != m.path:
        try:
            m.path.unlink()
        except OSError:
            pass
    return target.stem


# ── finalize (called from bot.py's shutdown finally) ─────────────────────────

def finalize(store: "SessionStore", messages, *, outcome: Optional[dict] = None) -> str:
    """Apply the shutdown keep-decision. Returns a short human status string.

    Ephemeral by default: an unkept working file is deleted at a graceful
    stop. A kept sitting (``store.retain`` or already ``held``) is snapshotted
    and marked ``held``. The write switch binds at close, so a late retain
    request — thrown while the bot runs (the Stop card), or the legacy hold
    marker, honored as "retain + name" for one release — can still flip it
    before this runs; a request naming a stale ``session_id`` is ignored.

    **The two markers mean different things by a name** (decision 008, "Id ≠
    label"). The retain marker's name is a LABEL: it is written to the file's
    ``title`` and the filename never moves — the Stop card's default label is
    "<character> · <date> <time>", which is not a legal session id, and a row
    whose id is not a legal id is a row no shelf verb can act on. The older
    hold marker's name still RENAMES the file, unchanged for one release.

    ``outcome``, when given, is filled with the machine-readable result for the
    close row (session/close_row.py): ``result`` ∈ none · held · held-unnamed ·
    kept · deleted · nothing-to-delete · empty, plus ``label`` for a retain
    request that carried one, ``hold_requested`` / ``hold_name`` / ``hold_ok``
    for a hold request (the rename lane only), and ``stale_request``.

    **Why the rename is caught and the snapshot is not** (spec-session-close-ux
    §4): a failed rename is **D7** — the user named the session, the name did
    not take, and the conversation is still written under its old name. That is
    survivable and must be *recorded*, not raised. A failed snapshot is **D5** —
    the conversation may not be on disk at all, §5's only provable UNSAFE band —
    and must keep propagating to close_path, which records it as such. Catching
    both together is what made the two indistinguishable before.
    """
    out = outcome if outcome is not None else {}
    if store is None:
        out["result"] = "none"
        return "no session"

    req = read_retain_request(store.sessions_dir)
    # WHICH marker spoke decides what a name means: the retain marker labels,
    # the legacy hold marker renames. See the docstring.
    labelling = req is not None
    if req is None:
        requested, name = read_hold_request(store.sessions_dir)
        if requested:
            req = {"retain": True, "name": name}
    if req is not None and req.get("session_id") and req["session_id"] != store.session_id:
        out["stale_request"] = True
        req = None

    name_requested = False
    renamed = True
    labelled = None
    name = None
    if req is not None:
        store.retain = bool(req["retain"])
        if req["retain"] and req.get("name") and labelling:
            # A label cannot fail: it is a field in the file the snapshot below
            # writes, set exactly the way the start path sets --keep-name.
            labelled = name = req["name"]
            store.title = name
            out["label"] = name
        elif req["retain"] and req.get("name"):
            name_requested = True
            name = req["name"]
            out["hold_requested"] = True
            out["hold_name"] = name or None
            out["hold_ok"] = True
            try:
                store.rename(name)
                store.name = name
            except Exception as exc:  # noqa: BLE001 — D7, not D5: see the docstring
                renamed = False
                out["hold_ok"] = False
                out["hold_error"] = type(exc).__name__
                logger.warning("[session] hold rename to {!r} failed ({}) — keeping the "
                               "conversation under its current name", name, type(exc).__name__)

    if store.retain or store.held:
        store.held = True
        store.retain = True
        store.snapshot(messages)
        forked_from = getattr(store, "forked_from", None)
        if forked_from:
            old = Path(store.sessions_dir) / f"{forked_from}.json"
            # A rename onto the original's own id lands on this same path via
            # os.replace above — that is a supersede by replacement, and
            # old == store.path then correctly skips the unlink (nothing left
            # to delete: the rename already consumed it).
            if old.exists() and old != store.path:
                old.unlink()
                out["superseded"] = forked_from
        if name_requested:
            out["result"] = "held" if renamed else "held-unnamed"
            return f"held → {store.path.name}" if renamed else (
                f"held → {store.path.name} (could not use the name {name!r})")
        out["result"] = "kept"
        return (f"conversation kept as {labelled!r} → {store.path.name}" if labelled
                else f"conversation kept → {store.path.name}")

    if not store.path.exists() and not _persistable_messages(messages):
        out["result"] = "empty"
        return "empty sitting — nothing to save"

    if store.delete():
        out["result"] = "deleted"
        return "conversation not kept — transcript deleted (graceful stop)"
    out["result"] = "nothing-to-delete"
    return "conversation not kept — no transcript to delete"


# ── CLI (thin surface for start.sh / stop.sh) ────────────────────────────────

def _fmt_meta(m: "SessionMeta") -> str:
    tag = " [HELD]" if m.held else (" [unkept]" if not m.retain else "")
    nm = f" · {m.name}" if m.name else ""
    pv = f" · persona.{m.persona}.md" if m.persona not in (None, "", "default") else ""
    return f"{m.updated or m.started or '?'}  ·  {m.turns} turns  ·  {m.model}  ·  {m.voice}{pv}{nm}{tag}"


def _parse_cwd(rest: list) -> tuple:
    """Pull an optional ``--cwd <folder>`` pair out of the remaining argv tokens."""
    cwd = None
    out = []
    it = iter(rest)
    for token in it:
        if token == "--cwd":
            value = next(it, None)
            if value is not None:
                cwd = Path(value)
        else:
            out.append(token)
    return out, cwd


def _main(argv) -> int:
    if not argv:
        print("usage: session_store.py {list|request-hold|hold|keep|discard-ephemeral|discard-held} "
              "[name|path] [--cwd folder]", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    cwd = None
    if cmd in ("request-hold", "hold"):
        rest, cwd = _parse_cwd(rest)
    arg = rest[0] if rest else None
    if cmd == "list":
        dirs = all_sessions_dirs()
        shown = 0
        for d in dirs:
            metas = list_sessions(d)
            if not metas:
                continue
            print(f"{d.parent.name}:")
            for m in metas:
                print(f"  {_fmt_meta(m)}")
            shown += len(metas)
        if not shown:
            print("(no sessions)")
        return 0
    if cmd == "request-hold":
        write_hold_request(arg, cwd=cwd)
        print(f"hold requested{f' (name={arg})' if arg else ''} — bot will keep its session on stop")
        return 0
    if cmd == "hold":
        sid = hold_latest_orphan(arg, cwd=cwd)
        print(f"held: {sid}" if sid else "no unnamed session to hold")
        return 0
    if cmd == "keep":
        if not rest:
            print("usage: session_store.py keep <id> [name]", file=sys.stderr)
            return 2
        stem = keep_orphan(rest[0], rest[1] if len(rest) > 1 else None)
        print(f"kept: {stem}" if stem else "no unkept conversation with that id")
        return 0
    if cmd == "discard-ephemeral":
        expired_only = arg != "--all"
        removed = discard_ephemeral(expired_only=expired_only)
        label = "expired unkept conversation(s)" if expired_only else "unkept conversation(s)"
        print(f"deleted {len(removed)} {label}")
        return 0
    if cmd == "discard-held":
        if arg in (None, "--all"):
            # Catastrophic all-wipe of the deliberately-kept class — gate it hard.
            # A forgotten name must never silently rm -rf every work-topic.
            held = held_sessions()
            if not held:
                print("no held sessions to discard")
                return 0
            if not sys.stdin.isatty():
                print("refusing to wipe ALL held sessions non-interactively — name one "
                      "(discard-held <name>) or run in a terminal to confirm", file=sys.stderr)
                return 2
            red, bold, off = "\033[1;31m", "\033[1m", "\033[0m"
            print(f"{red}⚠  DELETE ALL HELD SESSIONS{off} — {len(held)} deliberately-kept "
                  f"session(s), permanently, with NO recovery:")
            for m in held:
                print(f"    {_fmt_meta(m)}")
            try:
                reply = input(f"Type {bold}HEARTH{off} to confirm (anything else cancels): ").strip()
            except (EOFError, KeyboardInterrupt):
                reply = ""
            if reply != "HEARTH":
                print("cancelled — nothing deleted.")
                return 0
            removed = discard_held(None)
            print(f"discarded {len(removed)} held session(s)")
            return 0
        # Targeted single-name discard: explicit and low-risk — proceed.
        removed = discard_held(arg)
        print(f"discarded {len(removed)} held session(s)")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
