"""maintenance_lock — the per-character session-store maintenance lock.

Whoever currently owns a character's session store holds this lock: a live bot
session holds it for the life of the session; offline maintenance (compaction)
holds it for the life of the run. The lock is a kernel ``flock(2)`` on
``DATA/locks/session-maintenance/<character>.lock`` — acquisition is atomic
(no check-then-act window) and release is the kernel's job the instant the
holding process dies, however it dies. That gives the two guarantees the
design demands: while work runs the lock is always observable (no false
negatives), and a crashed holder can never leave a stale lock behind (no
false positives).

The lock FILE's JSON contents (op / session / pid / started) are informational
only — they persist after release and are reported only while the lock is
actually held. The lock is the truth; the contents are the courtesy.

Two consumption styles:

- **In-process registry** (bot, switcher): ``hold(char, op=...)`` acquires and
  parks the handle in a process-global registry (idempotent per character —
  flock treats a second fd as a rival even in the same process, so re-holding
  must short-circuit); ``drop(char)`` releases. Anything still held at process
  death is released by the kernel.
- **CLI wrapper** (the offline compactor): ``python -m
  hearth.session.maintenance_lock run <char> --op compact -- <cmd …>``
  acquires (exit 3 with a human line if held), marks the fd inheritable, and
  ``exec``s the command — the flock rides the exec on the open file
  description and releases when that process exits. No wrapper process, no
  cleanup step to crash before.

``probe()`` answers by briefly try-acquiring, so a probe racing a real
acquirer can momentarily steal the free state; callers treat probe as
advisory UX and rely on their own ``acquire``/``hold`` as the arbiter.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from hearth.config import config_loader

_CHAR_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Process-global registry: character → Handle (see ``hold``/``drop``).
_HELD: dict[str, "Handle"] = {}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def lock_dir() -> Path:
    """Resolved at call time so test patching of DATA_DIR is honored."""
    return Path(config_loader.DATA_DIR) / "locks" / "session-maintenance"


def lock_path(character: str) -> Path:
    if not _CHAR_RE.match(character or ""):
        raise ValueError(f"invalid character name for lock: {character!r}")
    return lock_dir() / f"{character}.lock"


class Handle:
    """An acquired lock. Release explicitly or let process death do it."""

    def __init__(self, character: str, fd: int):
        self.character = character
        self._fd: Optional[int] = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def __enter__(self) -> "Handle":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def acquire(character: str, *, op: str, session: Optional[str] = None) -> Optional[Handle]:
    """Non-blocking exclusive acquire. Returns a Handle, or None if held.

    On success the lock file's contents are rewritten with this holder's info.
    """
    path = lock_path(character)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    info = {"op": op, "session": session, "pid": os.getpid(), "started": _now_iso()}
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps(info).encode("utf-8"), 0)
    return Handle(character, fd)


def probe(character: str) -> Optional[dict]:
    """None = free. A dict (op/session/pid/started, best effort) = held.

    Advisory: implemented as a fleeting try-acquire — see the module note.
    """
    path = lock_path(character)
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                raw = path.read_text(encoding="utf-8")
                info = json.loads(raw) if raw.strip() else {}
            except (OSError, ValueError):
                info = {}
            return info if isinstance(info, dict) else {}
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def held_locks(op: Optional[str] = None) -> list[dict]:
    """Every currently HELD lock (optionally filtered by op), for status
    surfaces. Names and timestamps only — never content."""
    out = []
    root = lock_dir()
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.lock")):
        info = probe(path.stem)
        if info is None:
            continue
        if op is not None and info.get("op") != op:
            continue
        out.append({"character": path.stem, **info})
    return out


def describe(info: dict) -> str:
    """One human clause for refusal messages: 'compaction of X, since T'."""
    op = info.get("op") or "maintenance"
    what = "compaction" if op == "compact" else op
    sess = f" of {info['session']}" if info.get("session") else ""
    since = f", since {info['started']}" if info.get("started") else ""
    return f"{what}{sess}{since}"


# ── in-process registry ──────────────────────────────────────────────────────

def hold(character: str, *, op: str, session: Optional[str] = None) -> bool:
    """Acquire and park in the process registry. True if held (idempotent —
    a character this process already holds is a success, since a second fd
    would rival the first even in the same process)."""
    if character in _HELD:
        return True
    handle = acquire(character, op=op, session=session)
    if handle is None:
        return False
    _HELD[character] = handle
    return True


def drop(character: str) -> None:
    """Release a registry-held lock. Unknown character is a no-op."""
    handle = _HELD.pop(character, None)
    if handle is not None:
        handle.release()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="maintenance_lock")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="exit 0 free / 3 held (prints holder info)")
    p.add_argument("character")

    r = sub.add_parser("run", help="acquire, then exec CMD holding the lock "
                                   "(exit 3 if held; lock releases when CMD exits)")
    r.add_argument("character")
    r.add_argument("--op", default="compact")
    r.add_argument("--session", default=None)

    # Split on the first "--" ourselves — argparse's REMAINDER greedily eats
    # optionals that follow a positional, so it cannot be trusted with this.
    cmd: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, cmd = argv[:split], argv[split + 1:]

    args = parser.parse_args(argv)
    if args.cmd == "probe":
        info = probe(args.character)
        if info is None:
            print("free")
            return 0
        print(f"held — {describe(info)}")
        return 3

    if not cmd:
        parser.error("run requires -- <command …>")
    handle = acquire(args.character, op=args.op, session=args.session)
    if handle is None:
        info = probe(args.character) or {}
        print(f"held — {describe(info)} — try again in a few minutes")
        return 3
    os.set_inheritable(handle._fd, True)  # noqa: SLF001 — the flock must ride the exec
    os.execvp(cmd[0], cmd)
    return 127  # pragma: no cover — execvp does not return


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
