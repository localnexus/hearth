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

from . import close_row


def run_close(store, seam, messages, *, live_tokens: Optional[int] = None,
              finalize: Callable, request: Callable,
              emit: Callable[[str], None] = print,
              record: Optional[Callable] = close_row.record,
              facts: Optional[dict] = None,
              phase: Optional[Callable] = None) -> list:
    """Run the three steps in order; every step is contained. Returns the
    status lines in the order they were emitted (tests assert on this).

    ``record`` receives ``(store, outcomes, lines)`` once the three steps are
    done and writes the durable close row (session/close_row.py) — injected the
    same way ``finalize`` and ``request`` are, so a test can assert on the row
    without touching the ledger. Pass ``record=None`` to disable it.

    ``facts`` carries what the CALLER observed and this function cannot: the
    capture finalize (§4 D8) and the live-switcher drain (§4 D4) both happen in
    bot.py's ``finally`` before this runs. They are merged into the row as-is.

    Two properties the row depends on, both load-bearing:

    * **The row is written LAST, and only on the graceful path.** §4's B2/B3 —
      SIGINT outlived, escalated to SIGTERM, which the runner does not handle —
      never reach this line. A missing row IS the force-close signal; see
      close_row's docstring. Recording earlier would forge evidence of a close
      that did not finish.
    * **The row is never worth a close.** ``record`` is called inside its own
      guard: a ledger that cannot be written loses the audit trail and nothing
      else. The conversation is the truth; the row is the courtesy.
    """
    lines: list = []
    outcomes: dict = dict(facts or {})

    def say(line: str) -> None:
        lines.append(line)
        emit(line)

    if store is not None:
        session_out: dict = {}
        outcomes["session"] = session_out
        try:
            status = finalize(store, messages, outcome=session_out)
            say(f"[session] {status}")
        except Exception as exc:  # noqa: BLE001 — §4 D5, §5's only provable UNSAFE band
            session_out["result"] = "failed"
            session_out["error"] = type(exc).__name__
            errno = getattr(exc, "errno", None)
            if errno is not None:                       # spec §4: disk full rolls up here
                session_out["errno"] = errno
            logger.warning("[session] finalize failed: {}", type(exc).__name__)
        else:
            _phase(phase, "session-finalized", outcome=session_out)
            compaction_out: dict = {}
            outcomes["compaction"] = compaction_out
            try:
                note = request(store, live_tokens=live_tokens or None,
                               outcome=compaction_out)
            except Exception as exc:  # noqa: BLE001
                compaction_out["result"] = "failed"
                compaction_out["error"] = type(exc).__name__
                logger.warning("[session] compaction request failed: {}", type(exc).__name__)
                note = None
            if note:
                say(f"[session] {note}")
            _phase(phase, "compaction-queued", outcome=compaction_out)
    if seam is not None:
        memory_out: dict = {}
        outcomes["memory"] = memory_out
        _phase(phase, "memory-tail", deadline_s=getattr(seam, "close_budget_s", None))
        try:
            mem_status = seam.on_session_end(messages, store, outcome=memory_out)
            if mem_status:
                say(f"[memory] {mem_status}")
        except BaseException as exc:  # noqa: BLE001 — re-raised below; the row is written first
            memory_out["result"] = "raised"
            memory_out["error"] = type(exc).__name__
            raise
        finally:
            seam.close()
            _phase(phase, "sidecar-stopped")
            _record(record, store, outcomes, lines)
        _phase(phase, "done")
        return lines
    _record(record, store, outcomes, lines)
    _phase(phase, "done")
    return lines


def _record(record: Optional[Callable], store, outcomes: dict, lines: list) -> None:
    """Write the close row, contained. A ledger failure is never a close failure."""
    if record is None:
        return
    try:
        record(store, outcomes, lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[session] close row failed ({}) — close unaffected",
                       type(exc).__name__)


def _phase(phase: Optional[Callable], stage: str, **kw) -> None:
    """Advance the close-phase breadcrumb, contained. A breadcrumb is never
    worth a close."""
    if phase is None:
        return
    try:
        phase(stage, **kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[session] close phase callback failed ({}) — close unaffected",
                       type(exc).__name__)
