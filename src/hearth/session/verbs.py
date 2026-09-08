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
"""

from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path
from typing import Optional

#: A session id is a file stem, never a path: the same shape the store's own
#: verbs accept (``/admin/compact`` validates with this spelling too).
SESSION_ID_RE = re.compile(r"[A-Za-z0-9._-]+")

#: The archive convention (build-session-file-management §6.3), a dot-dir
#: beside the sessions themselves so the shelf answers "archived" from
#: location rather than from a field.
ARCHIVE_DIR = ".archive"


class SessionPathError(ValueError):
    """A session id that the fence refuses. ``reason`` never names a path."""

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


def resolve_session_path(character: str, session: str, *, archived: bool = False) -> Path:
    """The one gate every file verb passes through.

    Returns the real path of ``<sessions root>[/.archive]/<session>.json``, or
    raises ``SessionPathError``. Refuses: a malformed id, a symlink at the
    candidate, a candidate that resolves outside the root, and anything that is
    not a regular file.
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
    if not real.is_file():
        raise SessionPathError("no such session")
    return real


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
