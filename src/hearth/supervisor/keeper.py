"""supervisor/keeper.py — is something standing behind this process to bring it back?

The daemon's own restart (`POST /admin/daemon/restart`) is a deliberate exit: it
only *is* a restart when a keeper relaunches the process — launchd's KeepAlive
on the supervised install, or whatever an operator has wired in. Under a plain
terminal run the same exit is simply the end of Hearth, which is why the
launch page draws its Restart button from this answer and the route refuses
without one.

Two sources, in order:
  1. HEARTH_KEEPER in the environment — an operator's word, for supervisors we
     cannot see ("systemd", "runit", …). "none"/"" says: nothing is behind me.
  2. The parent pid. A process launchd started has ppid 1 (macOS; on Linux
     ppid 1 is init/systemd). Any daemonised process also has ppid 1, so this
     is a strong hint, not a proof — the env override exists for the cases
     where the hint is wrong.
"""

from __future__ import annotations

import os
import sys

ENV = "HEARTH_KEEPER"


def detect(environ: dict | None = None, ppid: int | None = None, platform: str | None = None) -> str | None:
    """The keeper's name, or None when nothing would relaunch this process."""
    env = os.environ if environ is None else environ
    if ENV in env:
        word = str(env[ENV]).strip().lower()
        return None if word in ("", "none", "0", "no", "false") else word
    parent = os.getppid() if ppid is None else ppid
    if parent != 1:
        return None
    plat = sys.platform if platform is None else platform
    return "launchd" if plat == "darwin" else "init"
