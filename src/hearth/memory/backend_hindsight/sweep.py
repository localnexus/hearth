"""backend_hindsight/sweep.py — SIGTERM sidecars nobody owns any more.

The only file here that acts on processes this host did NOT spawn, which is why
it stands alone: the safety rule below is short, absolute, and easier to hold
to without the spawn logic around it.
"""

from __future__ import annotations

import os
import signal
import subprocess

from loguru import logger

_REAP_TIMEOUT_S = 5.0      # the orphan sweep's pgrep — bounded, never blocking


def _reap_orphaned_sidecars(runner: str) -> int:
    """SIGTERM sidecars whose parent is gone, before spawning ours.

    The sidecar runs in its OWN process group (``start_new_session`` below) so
    the operator's Ctrl+C can never reach it — which means only ``stop()`` ends
    it, and a host that dies ungracefully never gets there. Survivors are not
    harmless: each holds ~1 GB resident plus an idle pg0 connection, and they
    accumulate silently. Ten of them (9.4 GB) were found 2026-09-03 after a day
    of bot startup crashes — nothing had ever swept them.

    ``pgrep -P 1`` is the ENTIRE safety rule, and the reason this is safe to run
    while other hosts are live: a running host's sidecar is parented to THAT
    host, so a bot and the facade may each hold their own and neither is
    touched. Only a dead parent reparents a child to launchd (pid 1).

    Matching is on the runner path, so another checkout's sidecars stay that
    install's business. Best-effort throughout — a failed sweep must never block
    the session it precedes. Returns how many were signalled.
    """
    try:
        completed = subprocess.run(
            ["pgrep", "-P", "1", "-f", runner],
            capture_output=True, text=True, timeout=_REAP_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return 0  # no pgrep, or it hung — not worth failing a session start over
    me = os.getpid()
    reaped = 0
    for token in completed.stdout.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid == me:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue  # already exited, or not ours to signal
        reaped += 1
    if reaped:
        # Deliberately no wait: an orphan holds no port we need (5432 belongs to
        # the shared pg0 postmaster, which reaps their connections as they go),
        # so session start pays nothing here. A straggler is the next sweep's.
        logger.warning("[memory] reaped {} orphaned hindsight sidecar(s) — "
                       "parent gone (ungraceful host exit)", reaped)
    return reaped
