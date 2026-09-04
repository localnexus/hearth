"""backend_hindsight — adapter for Hindsight (vectorize-io), sidecar-first.

The survey's shortlist #1 (run-verified 2026-08-29/30: typed temporal facts on a
local 30B, recall 0.27–0.36 s with zero LLM calls, strict bank isolation,
zero-egress with the env posture in sidecar.py, dependency tree vetted clean).

Was one 29 KB module until 2026-09-03. It held two jobs that meet at exactly
one line — the adapter asking ``exited_rc()`` whether the server is still there
— so process supervision and the memory contract now sit apart, with the pure
pieces each of them used lifted out beside them:

    sweep.py     the orphan sweep: SIGTERM sidecars whose parent is gone. The
                 only code here that touches processes this host did not spawn,
                 so its one safety rule stands alone
    sidecar.py   the server PROCESS: why it is a separate venv at all, spawning
                 it, the logfile and drain thread that make it visible, and the
                 two ways one ends. Knows nothing about memory
    payload.py   what gets handed over for extraction: a speaker-labelled
                 transcript, tail-capped, and the record's end date
    adapter.py   the seam contract (recall · store · forget · clear), the one
                 worker thread every SDK call rides, and the policy for a
                 server that died mid-session — respawn once, then hand it up

The seam still sees one object: ``backend_hindsight.HindsightBackend(cfg)``,
unchanged. Note when patching in tests that a name re-exported here is a
BINDING, not the definition: patch ``backend_hindsight.sidecar`` or
``.sweep``, where the code that reads those names lives. Patching the name here
would rebind this module's copy and change nothing.
"""

from __future__ import annotations

from .payload import _MAX_RETAIN_CHARS_DEFAULT, _ended_at, _render_transcript
from .sweep import _REAP_TIMEOUT_S, _reap_orphaned_sidecars
from .sidecar import (
    _DEFAULT_LOG_REL, _LOG_DIR_MODE, _LOG_FILE_MODE, _LOG_ROTATE_BYTES,
    _SIDECAR_START_TIMEOUT_S, Sidecar)
from .adapter import _FACT_COUNT_LIMIT, _RECENT_BOOST_DEFAULT, HindsightBackend

__all__ = ["HindsightBackend", "Sidecar"]
