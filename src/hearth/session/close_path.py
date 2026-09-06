"""close_path — the bot's graceful-stop order, as one testable function.

Order (2026-09-06, spec-close-path-finalize-first S1):
  1. session_store.finalize      — the keep-decision (hold promotion, recall-only
                                   delete, unsaved snapshot). Cheap, file-only.
  2. compact_trigger.maybe_request — drops the auto-compaction request. Cheap.
  3. memory seam on_session_end + close — the SLOW tail (record write, backend
                                   index, consolidate, intent capture).

Why this order: the supervisor's SIGINT grace is finite (child.STOP_GRACE_S).
With the memory tail first, a long close was SIGTERMed before finalize and the
compaction request ever ran — run-observed on every supervisor stop up to
2026-09-06 (`outlived SIGINT — escalating` ×9; the auto lane never fired).
The seam reads the in-memory messages and the store's session id only — never
the transcript file — so finalize running first cannot starve it. The one
case the old order guarded (finalize true-deleting a recall-only transcript)
is exactly the case where the seam retains nothing.

A request created while the bot is still alive is safe: the facade watch
skips ticks while the bot child lives, and the desk compactor's live-session
guard dies before claiming anything.
"""
from __future__ import annotations

from typing import Callable, Optional

from loguru import logger


def run_close(store, seam, messages, *, live_tokens: Optional[int] = None,
              finalize: Callable, request: Callable,
              emit: Callable[[str], None] = print) -> list:
    """Run the three steps in order; every step is contained. Returns the
    status lines in the order they were emitted (tests assert on this)."""
    lines: list = []

    def say(line: str) -> None:
        lines.append(line)
        emit(line)

    if store is not None:
        try:
            status = finalize(store, messages)
            say(f"[session] {status}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session] finalize failed: {}", type(exc).__name__)
        else:
            try:
                note = request(store, live_tokens=live_tokens or None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[session] compaction request failed: {}", type(exc).__name__)
                note = None
            if note:
                say(f"[session] {note}")
    if seam is not None:
        try:
            mem_status = seam.on_session_end(messages, store)
            if mem_status:
                say(f"[memory] {mem_status}")
        finally:
            seam.close()
    return lines
