"""close_row — the durable, machine-readable record of one graceful close.

CLOSE-UX Phase 0, second half (spec-session-close-ux.md §3, §4, §5). Phase 0's
first half — the 27-row fail-state enumeration — is §4 of that spec. This module
is its output: the states §4 marks **WEAK** ("caught, but only as a log line")
become outcomes a machine can read, and land in an append-only ledger at
the close-rows ledger under the data root (one ``<YYYY-MM>.jsonl`` file per month).

Why a ledger and not a status field: §3.2. A state we detect but cannot yet
phrase plainly still earns an audit record — the record is what lets the phrasing
be learned later, and what lets a stranger's machine be diagnosed from a file
they send. Nothing here is user-facing; §3 forbids user-facing words for a state
until it is both detected AND recorded, and the recording is what this builds.

**The absence of a row is itself a signal, and the most important one.** A row is
written at the END of the graceful block in ``close_path.run_close``. §4's B2/B3
— SIGINT outlived, escalated to SIGTERM — cannot write a row by construction:
``bot.py`` builds its runner as a bare ``WorkerRunner()`` (bot.py:657), and in
pipecat 1.4.0 ``WorkerRunner.__init__`` defaults ``handle_sigterm=False``
(workers/runner.py:112), so ``_setup_sigterm()`` is never installed
(workers/runner.py:344) and SIGTERM keeps its default disposition: the process
dies without unwinding, and the ``finally`` that calls this module never runs.
**Re-verify that default on any pipecat upgrade** — if a later version installs a
SIGTERM handler, a force-close would start writing rows and the inference below
would silently invert.

So: a close the supervisor logged that left no row was force-closed. That is
§5's **at risk** band, and it is detectable only from the outside. This module
therefore never emits ``at-risk`` itself; the supervisor's escalation log and a
missing row are its two halves.

**This must never be the thing that breaks a close.** Every entry point is
contained. A row that cannot be built or written degrades to a log line and the
close proceeds — the ledger is the courtesy, the conversation is the truth.

**Nothing private.** Session *names* and ids are metadata the operator chose and
already appear in filenames; message text, persona text, and transcript lines
never enter a row. ``lines`` carries the same status strings the shutdown log
already prints, nothing more.

Two of §4's rows have no band in §5 and are recorded as ``degraded`` with a note,
flagged for the operator rather than silently banded (see ``UNBANDED``):
  * **D7** — hold marker write failed: the conversation IS on disk, but not under
    the name the user asked for. Not "auxiliary", not "unwritten".
  * **memory record write failed** — the transcript survives (session_store owns
    it); what is lost is recall of this session. Worse than a deferred index,
    short of an unwritten conversation.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from loguru import logger

SCHEMA = 1

#: §5's bands. "at-risk" is deliberately absent — see the module docstring.
SAFE, DEGRADED, UNSAFE = "safe", "degraded", "unsafe"

#: §4 rows that §5 does not place. Recorded, banded conservatively, and named
#: here so the gap is visible in the artifact instead of living in my head.
UNBANDED = {
    "hold-failed": "D7 — conversation saved, but not under the name the user gave",
    "record-write-failed": "memory record unwritten — transcript survives, recall of this session does not",
}


def ledger_dir() -> Path:
    """The close-rows ledger directory — a sibling of the compaction queue, same anchor."""
    from hearth.config import config_loader

    return Path(config_loader.DATA_DIR) / "ops" / "close-rows"


def band(outcomes: dict) -> tuple:
    """§5's band for a set of outcomes, plus the reasons that drove it.

    UNSAFE — the conversation itself may not be written (§4 D5), or the input
    transport was not confirmed closed when the ladder ran (§4 C1) — the one
    alarming case now that the mic is asserted rather than inferred.
    DEGRADED — conversation on disk, an auxiliary step failed or deferred.
    SAFE — every step ran.
    """
    reasons: list = []
    session = (outcomes.get("session") or {})
    memory = (outcomes.get("memory") or {})
    compaction = (outcomes.get("compaction") or {})
    capture = (outcomes.get("capture") or {})
    drain = (outcomes.get("drain") or {})
    mic = (outcomes.get("mic") or {})

    if session.get("result") == "failed":
        reasons.append("session-finalize-failed")  # D5
        return UNSAFE, reasons

    if mic.get("closed") is False:
        reasons.append("mic-not-confirmed-closed")  # §4 C1
        return UNSAFE, reasons

    for key, flag in (
        ("hold", session.get("hold_ok") is False),          # D7  (unbanded in §5)
        ("record-write-failed", memory.get("result") in
            ("record-write-failed", "record-build-failed")),  # (unbanded in §5)
        ("index-deferred", memory.get("index") == "deferred"),   # D1
        ("index-skipped", memory.get("index") == "skipped"),     # D2
        ("consolidate-failed", memory.get("consolidate") == "failed"),
        ("intent-failed", memory.get("intent") == "failed"),     # D9
        ("compaction-not-queued", compaction.get("result") in
            ("failed", "prior-failed")),                          # D6
        ("capture-failed", capture.get("result") == "failed"),   # D8
        ("drain-timeout", drain.get("result") == "timeout"),     # D4
        ("memory-tail-raised", memory.get("result") == "raised"),
    ):
        if flag:
            reasons.append("hold-failed" if key == "hold" else key)
    return (DEGRADED if reasons else SAFE), reasons


def build(store, outcomes: dict, lines: list) -> dict:
    """Assemble one row. Pure; never raises for a malformed store."""

    def _attr(name, default=None):
        try:
            return getattr(store, name, default)
        except Exception:  # noqa: BLE001 — a broken store must not cost the row
            return default

    b, reasons = band(outcomes)
    path = _attr("path")
    row = {
        "schema": SCHEMA,
        "closed": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "character": str(_attr("character") or "") or None,
        "session": (Path(str(path)).stem if path else None),
        "session_id": str(_attr("session_id") or "") or None,
        "name": str(_attr("name") or "") or None,
        "held": bool(_attr("held", False)),
        "memory_mode": str(_attr("memory_mode", "full") or "full"),
        "band": b,
        "reasons": reasons,
        "outcomes": outcomes,
        "lines": list(lines or []),
    }
    unbanded = [UNBANDED[r] for r in reasons if r in UNBANDED]
    if unbanded:
        row["note"] = unbanded
    # §4's mic rows (C1/C2) are asserted, not inferred, once the caller's
    # facts carry a mic fact — either value IS detected. Absent a fact, say so
    # in the artifact rather than let a later reader mistake silence for a
    # clean mic.
    mic_closed = (outcomes.get("mic") or {}).get("closed")
    row["undetected"] = [] if mic_closed in (True, False) else (
        ["mic-closed (C1/C2 — no signal until the bot asserts it)"])
    return row


def write(row: dict, *, directory: Optional[Path] = None) -> Optional[Path]:
    """Append one row to the month's ledger. Contained: returns None on failure.

    Append-only, one JSON object per line, so a close can never corrupt an
    earlier one and a reader can ``tail`` it. The write is a single small
    ``write()`` on an O_APPEND handle — atomic in practice for a line this size.
    """
    try:
        d = Path(directory) if directory is not None else ledger_dir()
        d.mkdir(parents=True, exist_ok=True)
        target = d / f"{time.strftime('%Y-%m', time.localtime())}.jsonl"
        blob = json.dumps(row, ensure_ascii=False) + "\n"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, blob.encode("utf-8"))
        finally:
            os.close(fd)
        return target
    except Exception as exc:  # noqa: BLE001 — the ledger is the courtesy
        logger.warning("[session] close row not written ({}) — close unaffected",
                       type(exc).__name__)
        return None


def record(store, outcomes: dict, lines: list, *,
           directory: Optional[Path] = None) -> Optional[dict]:
    """build + write, fully contained. The default ``record=`` for run_close."""
    try:
        row = build(store, outcomes, lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[session] close row not built ({}) — close unaffected",
                       type(exc).__name__)
        return None
    write(row, directory=directory)
    return row
