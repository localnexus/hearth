#!/usr/bin/env python3
"""compact_session.py — offline compaction of held session JSON logs.

Operator-facing CLI (does NOT run inside the live bot). Implements the procedure
in SCHEMA.md: dated pre-compaction bak → rewrite live messages → optional
restore-from-bak.

Provenance: recovered from an earlier chat session
first live compact ran as inline heredocs; this file is the
durable form so the logic no longer depends on chat history.

Usage (from anywhere; paths resolve via --sessions-dir):

    python3 tools/compaction/compact_session.py --sessions-dir <dir> backup <name>
    python3 tools/compaction/compact_session.py --sessions-dir <dir> compact <name> --from <body.md|json>
    python3 tools/compaction/compact_session.py --sessions-dir <dir> restore-from-bak <name> [--day YYYY.MM.DD]
    python3 tools/compaction/compact_session.py --sessions-dir <dir> stats <name>

Privacy: same class as sessions/*.json — plaintext, local-only, never sync bak
under portable/. Do not compact a session the bot currently has open mid-turn.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    from hearth.session import session_store as ss
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from hearth.session import session_store as ss

BAK_ROOT_NAME = "pre-compaction-bak"
DIR_MODE = ss.DIR_MODE
FILE_MODE = ss.FILE_MODE

# Sidecar fields copied unchanged through compact (persona fingerprint included).
# persona / character / memory_mode joined the store schema post-v2 (the persona-fingerprint rule (see SCHEMA)
# root + the per-session memory mode); the store writes character/name/memory_mode
# only when set, so copying conditionally-absent keys stays a no-op on old files.
SIDECAR_KEYS = (
    "schema",
    "model",
    "voice",
    "persona",
    "prompt_sha256",
    "started",
    "held",
    "character",
    "name",
    "memory_mode",
)

DEFAULT_META_USER = (
    "[session compact applied — full transcript backed up under {bak_rel}; "
    "continue as established partners with the continuity note that follows "
    "in your prior reply. Pick up in the present moment.]"
)


# ── path helpers ─────────────────────────────────────────────────────────────

def _today() -> str:
    return time.strftime("%Y.%m.%d", time.localtime())


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _session_path(name: str, sessions_dir: Path) -> Path:
    """Resolve <name> or <name>.json under sessions/."""
    name = name.removesuffix(".json")
    path = Path(sessions_dir) / f"{name}.json"
    return path


def _bak_root(sessions_dir: Path) -> Path:
    return Path(sessions_dir) / BAK_ROOT_NAME


def _ensure_mode(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_session(path: Path) -> dict:
    data = ss.load(path)
    msgs = data.get("messages") or []
    if not msgs:
        raise ValueError(f"empty messages list: {path}")
    return data


# ── backup ───────────────────────────────────────────────────────────────────

def backup_session(
    name: str,
    *,
    sessions_dir: Path,
    day: Optional[str] = None,
) -> Path:
    """Copy sessions/<name>.json → pre-compaction-bak/<day>/<name>.json.

    Never overwrites a differing same-day bak; writes
    <name>.pre-compact-<n>.json instead. Returns the path written (or the
    existing identical bak).
    """
    src = _session_path(name, sessions_dir)
    if not src.is_file():
        raise FileNotFoundError(f"session not found: {src}")
    _load_session(src)  # validate

    bakday = day or _today()
    bak_root = _bak_root(sessions_dir)
    day_dir = bak_root / bakday
    bak_root.mkdir(mode=DIR_MODE, exist_ok=True)
    _ensure_mode(bak_root, DIR_MODE)
    day_dir.mkdir(mode=DIR_MODE, exist_ok=True)
    _ensure_mode(day_dir, DIR_MODE)

    dest = day_dir / src.name
    if dest.exists():
        if dest.read_bytes() == src.read_bytes():
            return dest
        n = 2
        while True:
            alt = day_dir / f"{src.stem}.pre-compact-{n}.json"
            if not alt.exists():
                dest = alt
                break
            n += 1

    shutil.copy2(src, dest)
    _ensure_mode(dest, FILE_MODE)
    if dest.read_bytes() != src.read_bytes():
        raise RuntimeError(f"bak verify failed: {dest}")
    return dest


# ── compact body source ──────────────────────────────────────────────────────

def _read_compact_from(path: Path) -> tuple[str, Optional[list]]:
    """Return (assistant_body, optional_full_messages).

    - .json with list → full messages replacement (no auto preamble/tail)
    - .json with {messages: [...]} → same
    - .json with {body|content|compact_body: str} → body text
    - otherwise → entire file as assistant body (markdown / text)
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        obj = json.loads(text)
        if isinstance(obj, list):
            return "", obj
        if isinstance(obj, dict):
            if isinstance(obj.get("messages"), list):
                return "", obj["messages"]
            for key in ("body", "content", "compact_body", "text"):
                if isinstance(obj.get(key), str) and obj[key].strip():
                    return obj[key], None
            raise ValueError(
                f"{path}: JSON must be a messages list, "
                "{messages:[...]}, or {body|content|compact_body: str}"
            )
        raise ValueError(f"{path}: unsupported JSON root type {type(obj).__name__}")
    return text, None


def _trim_leading_assistants(tail: list) -> list:
    out = list(tail)
    while out and out[0].get("role") == "assistant":
        out = out[1:]
    return out


def compact_session(
    name: str,
    *,
    from_path: Path,
    sessions_dir: Path,
    tail: int = 12,
    trim_leading_assistant: bool = True,
    meta_user: bool = True,
    meta_user_text: Optional[str] = None,
    notes: str = "",
    method: str = "manual-curated",
    skip_backup: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backup (unless skipped), rewrite live session with compact payload."""
    src = _session_path(name, sessions_dir)
    if not src.is_file():
        raise FileNotFoundError(f"session not found: {src}")
    data = _load_session(src)
    if not data.get("held", False):
        print(
            "warning: session is not held (held=false); compacting ephemeral "
            "transcripts is unusual",
            file=sys.stderr,
        )

    bak_path: Optional[Path] = None
    bak_rel: Optional[str] = None
    if not skip_backup:
        if dry_run:
            # Plan-only: report where bak would land; do not write.
            planned = _bak_root(sessions_dir) / _today() / src.name
            bak_path = planned
            bak_rel = str(planned.relative_to(sessions_dir))
        else:
            bak_path = backup_session(name, sessions_dir=sessions_dir)
            bak_rel = str(bak_path.relative_to(sessions_dir))
            # Live must still match bak we just took (no concurrent writer).
            if bak_path.read_bytes() != src.read_bytes():
                raise RuntimeError(
                    "live file changed between bak and compact; aborting"
                )
    else:
        # Prefer today's bak for metadata; do not require it.
        day_dir = _bak_root(sessions_dir) / _today()
        candidate = day_dir / src.name
        if candidate.is_file():
            bak_path = candidate
            bak_rel = str(candidate.relative_to(sessions_dir))

    body, full_messages = _read_compact_from(Path(from_path))
    pre_count = len(data["messages"])

    if full_messages is not None:
        messages = full_messages
    else:
        messages: list[dict] = []
        if meta_user:
            bak_label = bak_rel or f"{BAK_ROOT_NAME}/{_today()}/"
            text = meta_user_text or DEFAULT_META_USER.format(bak_rel=bak_label)
            messages.append({"role": "user", "content": text})
        messages.append({"role": "assistant", "content": body})
        if tail > 0:
            raw_tail = list(data["messages"][-tail:])
            if trim_leading_assistant:
                trimmed = _trim_leading_assistants(raw_tail)
                # If trim ate almost everything, fall back to untrimmed tail.
                if len(trimmed) >= max(2, min(6, tail // 2)):
                    raw_tail = trimmed
            messages.extend(raw_tail)

    now = _now_iso()
    out: dict[str, Any] = {}
    for key in SIDECAR_KEYS:
        if key in data:
            out[key] = data[key]
    out["updated"] = now
    out["compaction"] = {
        "schema": 1,
        "compacted_at": now,
        "source_backup": bak_rel,
        "pre_message_count": pre_count,
        "post_message_count": len(messages),
        "method": method,
        "notes": notes or None,
    }
    # Drop null notes for cleaner JSON
    if out["compaction"]["notes"] is None:
        del out["compaction"]["notes"]
    out["messages"] = messages

    result = {
        "path": str(src),
        "bak": str(bak_path) if bak_path else None,
        "pre_message_count": pre_count,
        "post_message_count": len(messages),
        "prompt_sha256": out.get("prompt_sha256"),
        "dry_run": dry_run,
    }

    if dry_run:
        result["preview_roles"] = [m.get("role") for m in messages]
        result["preview_first_chars"] = (messages[0].get("content") or "")[:120]
        return result

    ss._atomic_write_json(src, out)  # noqa: SLF001 — same helper live store uses

    # Verify
    v = ss.load(src)
    if v.get("prompt_sha256") != data.get("prompt_sha256"):
        raise RuntimeError("prompt_sha256 changed after compact — abort state")
    if len(v["messages"]) != len(messages):
        raise RuntimeError("message count mismatch after write")
    if bak_path and not bak_path.is_file():
        raise RuntimeError("bak disappeared after compact")

    result["live_bytes"] = src.stat().st_size
    if bak_path:
        result["bak_bytes"] = bak_path.stat().st_size
    return result


# ── restore ──────────────────────────────────────────────────────────────────

def restore_from_bak(
    name: str,
    *,
    sessions_dir: Path,
    day: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Replace live sessions/<name>.json from a pre-compaction bak.

    If --day omitted: newest day dir that contains a matching bak (prefers
    exact <name>.json over .pre-compact-N).
    """
    name = name.removesuffix(".json")
    bak_root = _bak_root(sessions_dir)
    if not bak_root.is_dir():
        raise FileNotFoundError(f"no bak root: {bak_root}")

    if day:
        day_dirs = [bak_root / day]
        if not day_dirs[0].is_dir():
            raise FileNotFoundError(f"no bak day dir: {day_dirs[0]}")
    else:
        day_dirs = sorted(
            (p for p in bak_root.iterdir() if p.is_dir()),
            reverse=True,
        )

    bak: Optional[Path] = None
    for d in day_dirs:
        exact = d / f"{name}.json"
        if exact.is_file():
            bak = exact
            break
        # fall back to highest pre-compact-N
        alts = sorted(d.glob(f"{name}.pre-compact-*.json"))
        if alts:
            bak = alts[-1]
            break

    if bak is None:
        raise FileNotFoundError(
            f"no bak for {name!r} under {bak_root}"
            + (f" day={day}" if day else "")
        )

    live = _session_path(name, sessions_dir)
    data = _load_session(bak)
    result = {
        "bak": str(bak),
        "path": str(live),
        "message_count": len(data["messages"]),
        "bak_bytes": bak.stat().st_size,
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    # Atomic replace with bak bytes (preserve bak as source of truth).
    tmp = live.with_name(live.name + ".tmp")
    shutil.copy2(bak, tmp)
    _ensure_mode(tmp, FILE_MODE)
    os.replace(tmp, live)
    _ensure_mode(live, FILE_MODE)
    result["live_bytes"] = live.stat().st_size
    ss.load(live)  # validate
    return result


# ── stats ────────────────────────────────────────────────────────────────────

def session_stats(name: str, *, sessions_dir: Path) -> dict[str, Any]:
    src = _session_path(name, sessions_dir)
    data = _load_session(src)
    msgs = data["messages"]
    baks = []
    bak_root = _bak_root(sessions_dir)
    if bak_root.is_dir():
        for p in sorted(bak_root.rglob(f"{src.stem}*.json")):
            baks.append(
                {
                    "path": str(p.relative_to(sessions_dir)),
                    "bytes": p.stat().st_size,
                    "sha256_16": _sha256_file(p)[:16],
                    "msgs": len(json.loads(p.read_text(encoding="utf-8")).get("messages") or []),
                }
            )
    return {
        "path": str(src),
        "bytes": src.stat().st_size,
        "sha256_16": _sha256_file(src)[:16],
        "msg_count": len(msgs),
        "user_turns": sum(1 for m in msgs if m.get("role") == "user"),
        "held": data.get("held"),
        "name": data.get("name"),
        "model": data.get("model"),
        "voice": data.get("voice"),
        "prompt_sha256": data.get("prompt_sha256"),
        "started": data.get("started"),
        "updated": data.get("updated"),
        "compaction": data.get("compaction"),
        "first_role": msgs[0].get("role") if msgs else None,
        "first_content_120": (msgs[0].get("content") or "")[:120] if msgs else None,
        "baks": baks,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cmd_backup(args: argparse.Namespace) -> int:
    dest = backup_session(args.name, sessions_dir=Path(args.sessions_dir), day=args.day)
    print(f"bak → {dest}")
    print(f"bytes {dest.stat().st_size} sha256_16 {_sha256_file(dest)[:16]}")
    return 0


def _cmd_compact(args: argparse.Namespace) -> int:
    result = compact_session(
        args.name,
        from_path=Path(args.from_path),
        sessions_dir=Path(args.sessions_dir),
        tail=args.tail,
        trim_leading_assistant=not args.no_trim_leading_assistant,
        meta_user=not args.no_meta_user,
        meta_user_text=args.meta_user_text,
        notes=args.notes or "",
        method=args.method,
        skip_backup=args.skip_backup,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    result = restore_from_bak(
        args.name,
        sessions_dir=Path(args.sessions_dir),
        day=args.day,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    print(json.dumps(session_stats(args.name, sessions_dir=Path(args.sessions_dir)), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="compact_session.py",
        description="Offline compaction of sessions/*.json chat logs.",
    )
    p.add_argument(
        "--sessions-dir",
        required=True,
        help="sessions directory, e.g. <data root>/characters/<character>/sessions",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", help="Copy live session to pre-compaction-bak/YYYY.MM.DD/")
    b.add_argument("name", help="session id / file stem (e.g. my-session-01)")
    b.add_argument("--day", help="bak day YYYY.MM.DD (default: local today)")
    b.set_defaults(func=_cmd_backup)

    c = sub.add_parser(
        "compact",
        help="Bak + rewrite live messages from a curated compact body",
    )
    c.add_argument("name", help="session id / file stem")
    c.add_argument(
        "--from",
        dest="from_path",
        required=True,
        help="compact body (.md/.txt) or JSON messages / {body:…}",
    )
    c.add_argument(
        "--tail",
        type=int,
        default=12,
        help="raw messages retained after preamble (default 12; 0 = none)",
    )
    c.add_argument(
        "--no-trim-leading-assistant",
        action="store_true",
        help="keep assistant-leading edges on the raw tail",
    )
    c.add_argument(
        "--no-meta-user",
        action="store_true",
        help="omit the [session compact applied …] user line",
    )
    c.add_argument("--meta-user-text", help="override meta user line text")
    c.add_argument("--notes", default="", help="stored in compaction.notes")
    c.add_argument(
        "--method",
        default="manual-curated",
        help="compaction.method (default: manual-curated)",
    )
    c.add_argument(
        "--skip-backup",
        action="store_true",
        help="do not bak (you already ran backup); still records source_backup if today bak exists",
    )
    c.add_argument("--dry-run", action="store_true", help="print plan; do not write")
    c.set_defaults(func=_cmd_compact)

    r = sub.add_parser("restore-from-bak", help="Replace live file from a bak copy")
    r.add_argument("name", help="session id / file stem")
    r.add_argument("--day", help="YYYY.MM.DD (default: newest day with a bak)")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=_cmd_restore)

    s = sub.add_parser("stats", help="Print live + bak metadata (no content dump)")
    s.add_argument("name", help="session id / file stem")
    s.set_defaults(func=_cmd_stats)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
