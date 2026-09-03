"""compact_trigger — close-time auto-compaction requests (detection half).

Called right after ``session_store.finalize`` on every graceful close (bot
shutdown and the live switcher's old-side finalize). When the closed session
is HELD and its estimated context weight crosses the trigger, a small JSON
request lands in ``DATA/ops/compact-queue/`` — a breadcrumb, not a lock (the
maintenance lock is the in-progress truth). The facade's compact watch picks
requests up once no bot is alive and runs the offline compactor.

Sizing: the transcript-file estimate (bytes/4) is always available; when the
caller has the TokenMeter's last per-turn prompt count (the server's own
held-in-ctx number) it passes it in and the larger of the two decides —
the meter sees system prompt + recalls that the file does not.

Honesty rules: a ``.failed`` breadcrumb for the same session means a prior
auto-attempt aborted — never re-request (a human clears it); an existing
``.running`` claim is left alone; an existing ``.request`` is refreshed.
Detection never raises past its caller: shutdown must not break on a full
disk. Unclean deaths skip finalize entirely, so they skip this too — a
session that did not close cleanly is never auto-compacted.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from hearth.config import config_loader

# ~40K tokens: between the local model's compaction sweet spot (~30-40K) and
# its single-pass ceiling (~60K, where the offline compactor refuses anyway).
TRIGGER_TOKENS = 40_000


def queue_dir() -> Path:
    """Resolved at call time so test patching of DATA_DIR is honored."""
    return Path(config_loader.DATA_DIR) / "ops" / "compact-queue"


def maybe_request(store, *, live_tokens: Optional[int] = None) -> Optional[str]:
    """Drop a compaction request if the closed session warrants one.

    Returns a short human status line for the shutdown log, or None when no
    request was made (small session, not held, no character, or .failed).
    """
    try:
        if store is None or not getattr(store, "held", False):
            return None
        path = getattr(store, "path", None)
        if path is None or not Path(path).exists():
            return None
        character = getattr(store, "character", None)
        if not character:
            return None
        est = Path(path).stat().st_size // 4
        tokens = max(est, int(live_tokens or 0))
        if tokens < TRIGGER_TOKENS:
            return None

        session = Path(path).stem
        qdir = queue_dir()
        qdir.mkdir(parents=True, exist_ok=True)
        # Names by concatenation — session names may contain dots, so
        # Path.with_suffix would mangle them.
        base = f"{character}.{session}"
        if (qdir / f"{base}.failed").exists():
            return (f"auto-compaction wanted (~{tokens} tok) but a prior attempt "
                    f"failed — clear {base}.failed to re-arm")
        if (qdir / f"{base}.running").exists():
            return None  # already being compacted
        payload = {
            "character": character,
            "session": session,
            "est_tokens": tokens,
            "source": "prompt-meter" if live_tokens else "bytes/4",
            "requested": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        tmp = qdir / f"{base}.request.tmp"
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        os.replace(tmp, qdir / f"{base}.request")
        return f"auto-compaction requested (~{tokens} tokens ≥ {TRIGGER_TOKENS})"
    except Exception as exc:  # noqa: BLE001 — never break a shutdown path
        return f"auto-compaction request failed ({type(exc).__name__})"
