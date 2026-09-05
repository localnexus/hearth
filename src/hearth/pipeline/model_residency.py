"""pipeline/model_residency.py — a live session owns its model's residency.

The rule (signed 2026-09-05): if a turn would have to load a model just to
respond, the session loads it FIRST and keeps it. The wait moves to start-up,
where a wait is expected, instead of landing on the first sentence someone
says to their companion — or, on an LM Studio build that expires a
just-in-time load the second the reply ends, on EVERY sentence (~15 s each,
observed 2026-09-05 on LM Studio 0.4.19).

Scope: LM Studio only. Under llama-server the model IS the process — Hearth
starts it or the operator did — so there is nothing to load and this module
steps aside. The check uses the same residency probe the live model switch
trusts (switcher.fetch_resident_ids); the load is the same `lms load` the
compaction lane uses to bring an evicted model back, bounded and logged to
DATA/logs/model-load.log (0600) — a CLI may print what a route must not.

Nothing here ever raises into start-up: every failure is a printed note and
the pipeline proceeds; the first turn then pays the load exactly as it did
before this module existed. Release at session end is deliberately NOT done —
warm stays the default everywhere (the unload actuator is the explicit cold
stop).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from hearth.pipeline.switcher import _LLAMA_ALIASES, fetch_resident_ids

LOAD_TIMEOUT_S = 300.0  # a 40 GB model from a cold disk is a slow load, not a failure
_TERM_GRACE_S = 3.0
_ENV_BIN = "LMS_BIN"  # operator override for the CLI's path

# The same places the compaction lane looks (compact-model-lib.sh find_bin):
# a facade spawned by launchd carries the bare system PATH, so `which` alone
# misses the user-local install every LM Studio build ships to.
_FALLBACK_BINS = (
    "~/.lmstudio/bin/lms",
    "~/.local/bin/lms",
    "/opt/homebrew/bin/lms",
    "/usr/local/bin/lms",
)


def find_lms() -> Optional[str]:
    """Path of the LM Studio CLI, or None. Env override first, then PATH,
    then the usual user-local homes."""
    override = os.environ.get(_ENV_BIN)
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("lms")
    if found:
        return found
    for cand in _FALLBACK_BINS:
        p = os.path.expanduser(cand)
        if os.access(p, os.X_OK):
            return p
    return None


def is_lmstudio(provider: Optional[str]) -> bool:
    """The provider selector names anything that is not llama-server as the
    LM Studio probe (engine_probe_llamaserver.fetch_engine_info_for) — the
    same reading here, so the two never disagree about who owns the model."""
    return (provider or "").strip().lower() not in _LLAMA_ALIASES


async def ensure_resident(
    provider: Optional[str], base_url: str, token: str, model_id: str, *,
    log_dir: Optional[Path] = None,
    probe: Callable = fetch_resident_ids,
    lms_path: Optional[Callable[[], Optional[str]]] = find_lms,
    timeout_s: float = LOAD_TIMEOUT_S,
    say: Callable[[str], None] = lambda s: print(s, flush=True),
) -> dict:
    """Make `model_id` resident on an LM Studio server before the first turn.

    Returns a small record for the caller/test: {action, ok, seconds} where
    action ∈ skipped (not LM Studio) · unreachable (probe failed) · resident
    (already there) · loaded (we loaded it) · no-cli (lms not found) ·
    failed (load ran and did not take) · timeout. Never raises.
    """
    if not is_lmstudio(provider):
        return {"action": "skipped", "ok": True, "seconds": 0.0}
    t0 = time.monotonic()
    ids = await probe(provider, base_url, token)
    if ids is None:
        say("[model] the model server did not answer the residency check — "
            "the first turn will load the model if it must")
        return {"action": "unreachable", "ok": False, "seconds": 0.0}
    if model_id in ids:
        return {"action": "resident", "ok": True, "seconds": 0.0}
    lms = lms_path() if lms_path else None
    if not lms:
        say(f"[model] {model_id} is not loaded and the lms tool was not found "
            f"(set {_ENV_BIN}) — the first turn will pay the load")
        return {"action": "no-cli", "ok": False, "seconds": 0.0}
    say(f"[model] loading {model_id} — the session waits here so no turn has to")
    rc, timed_out = await _run_load(lms, model_id, log_dir, timeout_s)
    ids = await probe(provider, base_url, token)
    secs = round(time.monotonic() - t0, 1)
    if ids is not None and model_id in ids:
        say(f"[model] {model_id} resident after {secs} s")
        return {"action": "loaded", "ok": True, "seconds": secs}
    if timed_out:
        say(f"[model] load of {model_id} still running after {int(timeout_s)} s — "
            "continuing; see logs/model-load.log")
        return {"action": "timeout", "ok": False, "seconds": secs}
    say(f"[model] load of {model_id} did not take (exit {rc}) — "
        "the first turn will retry it; see logs/model-load.log")
    return {"action": "failed", "ok": False, "seconds": secs}


async def _run_load(lms: str, model_id: str, log_dir: Optional[Path],
                    timeout_s: float) -> tuple[Optional[int], bool]:
    """`lms load <id> --identifier <id>`, output to a 0600 log, bounded.
    The identifier is pinned to the model key because LM Studio routes
    requests on the identifier, and model.toml's `id` is what Hearth sends."""
    fd = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "model-load.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(path, 0o600)
        os.write(fd, f"\n── {time.strftime('%Y-%m-%dT%H:%M:%S%z')} — load {model_id}\n".encode())
    out = fd if fd is not None else asyncio.subprocess.DEVNULL
    try:
        proc = await asyncio.create_subprocess_exec(
            lms, "load", model_id, "--identifier", model_id, "--yes",
            stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=out,
        )
    except OSError:
        if fd is not None:
            os.close(fd)
        return None, False
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout_s)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True
        proc.terminate()
        try:
            rc = await asyncio.wait_for(proc.wait(), _TERM_GRACE_S)
        except asyncio.TimeoutError:
            proc.kill()
            rc = await proc.wait()
    finally:
        if fd is not None:
            os.close(fd)
    return rc, timed_out
