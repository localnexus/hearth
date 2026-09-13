"""close_phase — the breadcrumb the close ladder rewrites as it walks.

Kept as a FILE, not memory: a facade restart mid-close must still be able to
find where the ladder got to. Every write is atomic (``tmp`` + ``os.replace``)
and entirely contained — a lost breadcrumb is never worth a close.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from loguru import logger

STAGES = (
    "pipeline-down",
    "capture-finalized",
    "panel-down",
    "drain",
    "session-finalized",
    "compaction-queued",
    "memory-tail",
    "sidecar-stopped",
    "done",
)

_UNSET = object()


def phase_path() -> Path:
    from hearth.config import config_loader

    return Path(config_loader.DATA_DIR) / "ops" / "close-phase" / "current.json"


def input_closed(transport_input) -> Optional[bool]:
    """True/False from the installed transport's own stream handle, else None
    (unknown — NEVER claimed closed). This reads the installed transport's
    private stream handle on purpose; a test pins the attribute name against
    the installed dependency so an upgrade that renames it degrades to
    "unknown", never to a false "closed"."""
    if not hasattr(transport_input, "_in_stream"):
        return None
    return getattr(transport_input, "_in_stream") is None


class ClosePhase:
    """One close's breadcrumb file; overwritten in place as the ladder walks."""

    def __init__(self, *, pid: int, character: Optional[str], session_id: Optional[str],
                 path: Optional[Path] = None) -> None:
        self.pid = pid
        self.character = character
        self.session_id = session_id
        self.path = Path(path) if path is not None else phase_path()
        self.stages: list = []
        self.mic_closed = None

    def advance(self, stage: str, *, outcome=None, deadline_s=None,
                mic_closed=_UNSET) -> None:
        """Append one stage entry and rewrite the whole document. Contained:
        any exception is logged once at warning and swallowed."""
        try:
            entry = {
                "stage": stage,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "outcome": outcome,
            }
            if stage not in STAGES:
                entry["unknown"] = True
            self.stages.append(entry)
            if mic_closed is not _UNSET:
                self.mic_closed = mic_closed
            doc = {
                "schema": 1,
                "pid": self.pid,
                "character": self.character,
                "session_id": self.session_id,
                "stage": stage,
                "at": entry["at"],
                "heartbeat": time.time(),
                "deadline_s": deadline_s,
                "mic_closed": self.mic_closed,
                "stages": self.stages,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001 — a breadcrumb is never worth a close
            logger.warning("[session] close phase not written ({})", type(exc).__name__)


def read(*, pid: Optional[int], path: Optional[Path] = None) -> Optional[dict]:
    """The current document, or None: no pid to match, file absent/unreadable/
    not JSON, or the file's pid differs. A stale file from an earlier close
    can therefore never be mistaken for the current one."""
    if pid is None:
        return None
    p = Path(path) if path is not None else phase_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(doc, dict) or doc.get("pid") != pid:
        return None
    return doc
