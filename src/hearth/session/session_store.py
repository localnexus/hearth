"""session_store.py — Tier 1 session continuity: per-turn snapshot + resume.

The entire durable conversational state is ``context.messages`` — a plain list of
role/content dicts (llm_context.py:122, 228). LM Studio is stateless per request,
so preserving that list IS continuity. The system prompt is injected per-request
from settings (base_llm.py:308-312) and is NEVER in ``messages``, so it is never
persisted here → zero duplication on reload.

Privacy: a session file (``characters/<name>/sessions/*.json`` under the data root)
is a full plaintext transcript.
It is local-only, gitignored, dir ``0700`` / files ``0600``, and **saved by
default** — a graceful ``./stop.sh`` keeps it; deleting is the explicit act
(``discard-held`` / ``discard-ephemeral``), and every delete is a true-delete
(this sensitive class is deleted, not retained). The one carve-out: a
**recall-only** sitting (``--memory recall-only``) stays transcript-ephemeral —
a graceful stop deletes its file unless it was explicitly held, so "leaves no
durable record" stays true of the privacy tier. ``held`` now means *explicitly
kept/named* (sticky, exempt from every sweep); ``--hold`` degenerates to "name
it now". The snapshot+os.replace model closes the file handle every turn, so
delete frees the file cleanly (no deleted-but-open-handle trap). Transcripts are
never exposed over the web ``/``.

CLI (used by start.sh / stop.sh; keeps the bash thin and the logic unit-tested):
    python session_store.py list
    python session_store.py request-hold [name]   # bot running: drop marker, bot honors in finally
    python session_store.py hold [name]            # no bot: name/keep the newest unnamed session
    python session_store.py discard-ephemeral      # sweep recall-only leftovers (everything else is saved)
    python session_store.py discard-held [name|--all]
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SCHEMA = 2  # 2 (2026-08): adds "character" + "persona"; schema-1 files load as persona "default"
_HOLD_MARKER = ".hold-request"  # stop.sh --hold drops this; the bot honors + consumes it in finally

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


def _dir(sessions_dir) -> Path:
    return Path(sessions_dir) if sessions_dir is not None else companion_sessions_dir()


# ── helpers ──────────────────────────────────────────────────────────────────

def prompt_sha256(system_instruction: str) -> str:
    """Stable SHA-256 of the persona prompt, for drift detection on resume."""
    return hashlib.sha256(system_instruction.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def new_session_id() -> str:
    """Session id = local session-start ISO timestamp (filesystem-safe ':' → '-')."""
    return "session-" + time.strftime("%Y-%m-%dT%H-%M-%S", time.localtime())


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
    character: Optional[str] = None
    persona: str = "default"              # which persona file was live ("default" = persona.md)
    memory_mode: str = "full"             # the sitting's memory posture (--memory); stamped into
                                          # snapshots when not "full" so a crash orphan resumed
                                          # later inherits it instead of getting banked by default

    def __post_init__(self) -> None:
        if self.sessions_dir is None:
            self.sessions_dir = companion_sessions_dir(self.character)

    @property
    def path(self) -> Path:
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
        }
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
        """Move the file to ``<new_id>.json`` (used when a hold names the session)."""
        old = self.path
        self.session_id = new_id
        new = self.path
        if old.exists() and old != new:
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


def inherit_memory_mode(flag_value: Optional[str], store: "SessionStore") -> str:
    """Resolve the sitting's memory mode and stamp the store with it.

    An explicit --memory value wins; with the flag absent, a resumed session's
    own stamp is inherited — a crashed recall-only sitting must not get banked
    just because the resume forgot the flag — and a fresh session is "full".
    The store is stamped either way, so every later snapshot carries the
    sitting's CURRENT posture."""
    mode = flag_value if flag_value is not None else (
        getattr(store, "memory_mode", None) or "full")
    store.memory_mode = str(mode)
    return store.memory_mode


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


def list_sessions(sessions_dir: Optional[Path] = None) -> list:
    """Return SessionMeta for every readable session file, newest first.

    Malformed/empty files are skipped (never crash). Content is never read out.
    """
    sessions_dir = _dir(sessions_dir)
    metas = []
    if not sessions_dir.exists():
        return metas
    for p in sorted(sessions_dir.glob("*.json")):
        try:
            data = load(p)
        except (ValueError, json.JSONDecodeError, OSError):
            continue  # malformed → skip; fresh/other files unaffected
        msgs = data.get("messages", [])
        turns = sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user")
        metas.append(SessionMeta(
            path=p,
            session_id=p.stem,
            model=data.get("model"),
            voice=data.get("voice"),
            name=data.get("name"),
            held=bool(data.get("held", False)),
            started=data.get("started"),
            updated=data.get("updated"),
            turns=turns,
            persona=str(data.get("persona") or "default"),
            character=data.get("character"),
            memory_mode=str(data.get("memory_mode") or "full"),
        ))
    metas.sort(key=lambda m: (m.updated or m.started or ""), reverse=True)
    return metas


def ephemeral_orphans(sessions_dir: Optional[Path] = None) -> list:
    """The sweepable class under saved-by-default: ONLY a recall-only sitting's
    leftover (crash/unclean death) is ephemeral — everything else is a saved
    conversation. Explicitly held recall-only files are exempt (the deliberate
    keep wins)."""
    return [m for m in list_sessions(sessions_dir)
            if not m.held and m.memory_mode == "recall-only"]


def held_sessions(sessions_dir: Optional[Path] = None) -> list:
    return [m for m in list_sessions(sessions_dir) if m.held]


def resolve_resume_arg(arg: str, sessions_dir: Optional[Path] = None):
    """Resolve a ``--resume <arg>`` to a path: explicit file · <arg>.json ·
    session-<arg>.json · a session whose ``name`` field == arg. None if no match."""
    sessions_dir = _dir(sessions_dir)
    p = Path(arg)
    if p.is_file():
        return p
    for cand in (
        sessions_dir / (arg if arg.endswith(".json") else f"{arg}.json"),
        sessions_dir / f"session-{arg}.json",
    ):
        if cand.exists():
            return cand
    for m in list_sessions(sessions_dir):
        if m.name == arg:
            return m.path
    return None


# ── discard verbs (all true-delete for this sensitive class) ────────

def discard_ephemeral(sessions_dir: Optional[Path] = None) -> list:
    """Fresh start: true-delete recall-only leftovers; every saved conversation
    (and every held file) is left untouched."""
    removed = []
    for m in ephemeral_orphans(sessions_dir):
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


def write_hold_request(name: Optional[str] = None, sessions_dir: Optional[Path] = None) -> None:
    """stop.sh --hold (bot running): mark hold intent; the bot consumes it in finally."""
    ensure_dir(sessions_dir)
    m = marker_path(sessions_dir)
    fd = os.open(m, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(name or "")


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


def hold_latest_orphan(name: Optional[str] = None, sessions_dir: Optional[Path] = None):
    """stop.sh --hold with no bot running: mark the newest not-yet-held session as
    held (optionally naming/renaming it). Under saved-by-default this is "name it
    now" for the latest conversation — and the explicit keep for a recall-only
    leftover. Returns its new id, or None if nothing qualifies."""
    orphans = [m for m in list_sessions(sessions_dir) if not m.held]
    if not orphans:
        return None
    m = orphans[0]  # newest first
    data = load(m.path)
    data["held"] = True
    target = m.path
    if name:
        data["name"] = name
        target = _dir(sessions_dir) / f"{name}.json"
    _atomic_write_json(target, data)
    if target != m.path:
        try:
            m.path.unlink()
        except OSError:
            pass
    return target.stem


# ── finalize (called from bot.py's shutdown finally) ─────────────────────────

def finalize(store: "SessionStore", messages) -> str:
    """Apply the shutdown keep-decision. Returns a short human status string.

    Saved-by-default: hold-request present → promote to held/named (keep).
    already held → keep (sticky). recall-only sitting → true-delete (the
    privacy tier stays transcript-ephemeral). otherwise → saved.
    """
    if store is None:
        return "no session"
    requested, name = read_hold_request(store.sessions_dir)
    if requested:
        if name:
            store.rename(name)
            store.name = name
        store.held = True
        store.snapshot(messages)
        return f"held → {store.path.name}"
    if store.held:
        return f"held session kept → {store.path.name}"
    if getattr(store, "memory_mode", "full") == "recall-only":
        if store.delete():
            return "recall-only sitting — transcript deleted (graceful stop)"
        return "recall-only sitting — no transcript to delete"
    if not store.path.exists() and not _persistable_messages(messages):
        return "empty sitting — nothing to save"
    store.snapshot(messages)
    return f"session saved → {store.path.name}"


# ── CLI (thin surface for start.sh / stop.sh) ────────────────────────────────

def _fmt_meta(m: "SessionMeta") -> str:
    tag = " [HELD]" if m.held else (
        " [recall-only]" if m.memory_mode == "recall-only" else "")
    nm = f" · {m.name}" if m.name else ""
    pv = f" · persona.{m.persona}.md" if m.persona not in (None, "", "default") else ""
    return f"{m.updated or m.started or '?'}  ·  {m.turns} turns  ·  {m.model}  ·  {m.voice}{pv}{nm}{tag}"


def _main(argv) -> int:
    if not argv:
        print("usage: session_store.py {list|request-hold|hold|discard-ephemeral|discard-held} [name]",
              file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
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
        write_hold_request(arg)
        print(f"hold requested{f' (name={arg})' if arg else ''} — bot will keep its session on stop")
        return 0
    if cmd == "hold":
        sid = hold_latest_orphan(arg)
        print(f"held: {sid}" if sid else "no unnamed session to hold")
        return 0
    if cmd == "discard-ephemeral":
        removed = discard_ephemeral()
        print(f"discarded {len(removed)} recall-only leftover(s)")
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
