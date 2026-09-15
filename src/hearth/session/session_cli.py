"""
session_cli.py — interactive session-resume CLI (startup, TTY-facing).

Extracted from bot.py. This is the operator-facing
front end of session continuity: the metadata-only choosers and the --resume/--new
resolution that runs BEFORE model load so its guards fail fast. The persistence
mechanism itself lives in session_store.py; this module only drives it and talks to
the terminal.

Kept deliberately free of bot.py's config: the three identity values a resolution
needs (the live model id, voice tag, and datetime-free prompt fingerprint) are
passed IN by the caller rather than imported, so this module never re-loads config
and stays a pure UI/orchestration layer.

    resolve_session(args, lm_model, voice_tag, prompt_fingerprint)
        → (SessionStore, resume_messages | None, descriptor)

PRIVACY: every chooser lists sessions by METADATA ONLY — conversation content is
never printed.
"""

from __future__ import annotations

import os

from hearth.session import session_store


def _fmt_session(m: "session_store.SessionMeta") -> str:
    """One metadata line — NEVER includes conversation content."""
    nm = f" · {m.name}" if m.name else ""
    return f"{m.updated or m.started or '?'} · {m.turns} turns · {m.model} · {m.voice}{nm}"


def _class_tag(m: "session_store.SessionMeta") -> str:
    """Saved-by-default classes: named/held · recall-only leftover · plain saved."""
    if m.held:
        return " [HELD]"
    if m.memory_mode == "recall-only":
        return "  (recall-only leftover)"
    return ""


def _pick_session(cands: list):
    """Interactive picker: list candidates by METADATA ONLY, let the operator choose."""
    print("Multiple resumable sessions (metadata only — conversation content is never shown):")
    for i, m in enumerate(cands, 1):
        tag = _class_tag(m)
        print(f"  {i}. {_fmt_session(m)}{tag}")
    print("  0. start fresh")
    try:
        choice = input("resume which #? ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(cands):
            return cands[idx - 1].path
    return None


# Sentinel: the operator explicitly chose "new session" in the bare-start menu
# (distinct from cancelling, which leaves everything untouched).
_NEW_SESSION = object()


def _startup_menu(cands: list):
    """Bare-start chooser (TTY only). Lists 'new session' + every resumable session
    by METADATA ONLY, and lets the operator pick. Returns:
      • a Path        → resume that session,
      • _NEW_SESSION  → start fresh (saved sessions kept; recall-only leftovers swept),
      • None          → cancel (start nothing, discard nothing).
    Replaces the hard exit(2) guard when stdin is interactive; the guard still applies
    non-interactively so automation (launchd / web control) never blocks or silently
    discards.
    """
    print("Resumable sessions (metadata only — conversation content is never shown):")
    print("  0. new session   (start fresh — saved sessions are kept)")
    for i, m in enumerate(cands, 1):
        tag = _class_tag(m)
        print(f"  {i}. {_fmt_session(m)}{tag}")
    try:
        choice = input("choose #  (0 = new · Enter/Ctrl-C = cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if choice == "0":
        return _NEW_SESSION
    if choice.isdigit() and 1 <= int(choice) <= len(cands):
        return cands[int(choice) - 1].path
    if choice != "":
        print(f"[session] '{choice}' isn't an option — cancelling (nothing started or discarded).")
    return None


def _warn_resume_mismatch(data: dict, current_psha: str, lm_model: str, voice_tag: str,
                          persona: str = "default") -> None:
    """Warn (do NOT block) on model/voice/persona-file/persona-prompt drift since save."""
    saved_persona = str(data.get("persona") or "default")
    if saved_persona != (persona or "default"):
        print(f"[session] ⚠ persona variant drift: session={saved_persona} · current={persona} (resuming anyway)")
    if data.get("model") and data["model"] != lm_model:
        print(f"[session] ⚠ model drift: session={data['model']} · current={lm_model} (resuming anyway)")
    if data.get("voice") and data["voice"] != voice_tag:
        print(f"[session] ⚠ voice drift: session={data['voice']} · current={voice_tag} (resuming anyway)")
    if data.get("prompt_sha256") and data["prompt_sha256"] != current_psha:
        print("[session] ⚠ persona-prompt changed since this session was saved (resuming anyway)")


def resolve_session(args, lm_model: str, voice_tag: str, prompt_fingerprint: str, *,
                    character: str | None = None, persona: str = "default"):
    """Resolve --resume/--new → (SessionStore, resume_messages | None, descriptor).

    ``descriptor`` is a static-at-startup panel label (never content): a
    person's title, else the resumed file's name, else its own stem; "New"
    for a fresh session.

    Never crashes startup: a malformed/missing/empty file falls back to a
    fresh session with a warning. A resume forks the kept file into a new
    working file rather than writing it in place (``fork_session``); a start
    is never blocked by an unkept orphan (decision 008) — one just present is
    surfaced, never silently discarded.

    ``lm_model`` / ``voice_tag`` / ``prompt_fingerprint`` / ``character`` / ``persona``
    are the live identity values (from bot.py's config load) — passed in so this module
    stays config-free. Sessions are keyed by companion: everything resolves inside
    ``characters/<character>/sessions/`` under the data root.
    """
    sdir = session_store.companion_sessions_dir(character)
    session_store.ensure_dir(sdir)          # 0700 before anything can write
    session_store.clear_hold_request(sdir)  # drop any stale marker from a prior run
    psha = session_store.prompt_sha256(prompt_fingerprint)  # datetime-free → stable across sessions

    resume_data = None
    resume_path = None
    store = None

    if args.resume is not None:
        if args.resume == "":  # bare --resume
            cands = session_store.list_sessions(sdir)
            if not cands:
                print("[session] --resume: no sessions found — starting fresh")
            elif len(cands) == 1:
                resume_path = cands[0].path
            else:
                resume_path = _pick_session(cands)
        else:  # --resume <file|name>
            resume_path = session_store.resolve_resume_arg(args.resume, sdir)
            if resume_path is None:
                print(f"[session] --resume {args.resume!r}: not found — starting fresh")
        if resume_path is not None:
            try:
                store, resume_data = session_store.fork_session(
                    resume_path, model=lm_model, voice=voice_tag, prompt_sha256=psha,
                    character=character, persona=persona, sessions_dir=sdir)
            except Exception as exc:  # noqa: BLE001 — never crash startup
                print(f"[session] {resume_path} unreadable ({type(exc).__name__}) — starting fresh")
                resume_data, resume_path, store = None, None, None
    elif args.new:
        # Explicit fresh start: saved sessions are kept; expired unkept
        # orphans are swept, and any still-waiting ones are surfaced (never
        # discarded here — the launch page is where they're kept or deleted).
        removed = session_store.discard_ephemeral(sdir)
        if removed:
            print(f"[session] deleted {len(removed)} expired unkept conversation(s)")
        waiting = session_store.ephemeral_orphans(sdir)
        if waiting:
            print(f"[session] {len(waiting)} unkept conversation(s) waiting — keep or delete "
                  f"them from the launch page; they expire after 7 days")
            for m in waiting:
                print(f"    {_fmt_session(m)}")
    else:
        # Bare ./start.sh. Interactive terminal → offer a chooser (new + resumables,
        # held included so named work-topics are pickable). Non-interactive → keep the
        # hard guard so nothing is silently discarded and no prompt hangs automation.
        cands = session_store.list_sessions(sdir)
        if cands and os.isatty(0):
            sel = _startup_menu(cands)
            if sel is None:
                raise SystemExit(0)                        # cancelled — start nothing
            if sel is _NEW_SESSION:
                removed = session_store.discard_ephemeral(sdir)
                if removed:
                    print(f"[session] deleted {len(removed)} expired unkept conversation(s)")
                waiting = session_store.ephemeral_orphans(sdir)
                if waiting:
                    print(f"[session] {len(waiting)} unkept conversation(s) waiting — keep or "
                          f"delete them from the launch page; they expire after 7 days")
                    for m in waiting:
                        print(f"    {_fmt_session(m)}")
            else:
                resume_path = sel
                try:
                    store, resume_data = session_store.fork_session(
                        resume_path, model=lm_model, voice=voice_tag, prompt_sha256=psha,
                        character=character, persona=persona, sessions_dir=sdir)
                except Exception as exc:  # noqa: BLE001 — never crash startup
                    print(f"[session] {resume_path} unreadable ({type(exc).__name__}) — starting fresh")
                    resume_data, resume_path, store = None, None, None
        elif session_store.ephemeral_orphans(sdir):
            # Non-interactive with unkept orphans present: a start is never
            # blocked by one (decision 008 — launchd starts must come up).
            # They stay quarantined (shown, kept a week, then deleted) —
            # never silently discarded here.
            waiting = session_store.ephemeral_orphans(sdir)
            print(f"[session] {len(waiting)} unkept conversation(s) waiting — keep or delete "
                  f"them from the launch page; they expire after 7 days")
            for m in waiting:
                print(f"    {_fmt_session(m)}")
        # else: saved/held-only (or none), non-interactive → fall through to fresh
        # (nothing is discarded on a bare fresh start under saved-by-default)

    if resume_data is not None:
        resume_messages = resume_data.get("messages") or []
        _warn_resume_mismatch(resume_data, psha, lm_model, voice_tag, persona)
        print(f"[session] continuing {resume_path.name} as {store.path.name} · "
              f"{len(resume_messages)} messages (a kept conversation is never written in place)")
        # Panel descriptor: a person's title wins, then the source's name, then
        # the source's own stem. Just a static label — never content.
        descriptor = store.title or resume_data.get("name") or resume_path.stem
        return store, resume_messages, descriptor

    store = session_store.SessionStore(
        session_id=session_store.new_session_id(),
        model=lm_model,
        voice=voice_tag,
        prompt_sha256=psha,
        sessions_dir=sdir,
        character=character,
        persona=persona,
    )
    return store, None, "New"
