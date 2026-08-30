"""records.py — canonical memory-record I/O (the substrate every backend derives from).

One JSON file per ended session under DATA/characters/<c>/memory/records/,
written atomically at 0600 in a 0700 tree (session_store's contract, restated
here rather than imported — its writer is module-private and the coupling
isn't worth 25 lines). These records persist even though the session file
itself is ephemeral-by-default and deletes on graceful stop: enabling memory
IS the choice to keep a trace (documented in docs/memory.md). Deleting a
record is the explicit act, and it removes that session from every backend on
the next rebuild (decider 7: backends are derived indexes).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from .backend import SessionRecord

SCHEMA = 1
DIR_MODE = 0o700
FILE_MODE = 0o600


def records_dir(companion: str) -> Path:
    """DATA/characters/<companion>/memory/records/ (not created until first write)."""
    from hearth.config import config_loader  # lazy: keeps import cost off the CLI path

    return config_loader.companion_state_dir(companion, "memory") / "records"


def _ensure_dir(path: Path) -> None:
    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    # parents=True won't re-chmod an existing parent; assert the leaf at least.
    try:
        os.chmod(path, DIR_MODE)
    except OSError:
        pass


def _atomic_write_json(path: Path, obj: dict) -> None:
    """tmp at 0600 → flush+fsync → os.replace (same contract as session_store)."""
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, FILE_MODE)
    except OSError:
        pass


def write_record(record: SessionRecord, directory: Path | None = None) -> Path:
    """Persist one canonical record; returns its path. Idempotent per session_id
    (a re-run of the same session end overwrites the same file atomically)."""
    directory = Path(directory) if directory is not None else records_dir(record.companion)
    _ensure_dir(directory)
    payload = {"schema": SCHEMA, "kind": "memory-record", **asdict(record)}
    path = directory / f"{record.session_id}.json"
    _atomic_write_json(path, payload)
    return path


def load_record(path: Path) -> SessionRecord:
    """Load + validate one record file. Raises ValueError on malformed shape."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("kind") != "memory-record":
        raise ValueError(f"malformed memory record: {path}")
    return SessionRecord(
        companion=str(data.get("companion", "")),
        session_id=str(data.get("session_id", path.stem)),
        started=str(data.get("started", "")),
        ended=str(data.get("ended", "")),
        name=str(data.get("name", "") or ""),
        persona=str(data.get("persona", "default")),
        messages=data.get("messages") if isinstance(data.get("messages"), list) else [],
    )


def iter_records(companion: str, directory: Path | None = None,
                 newest_first: bool = False) -> Iterator[SessionRecord]:
    """Yield records ordered by ``ended`` (fallback: filename). Malformed files
    are skipped, never raised — one corrupt record must not cost the rest."""
    directory = Path(directory) if directory is not None else records_dir(companion)
    if not directory.is_dir():
        return
    loaded: list[SessionRecord] = []
    for path in sorted(directory.glob("*.json")):
        try:
            loaded.append(load_record(path))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    loaded.sort(key=lambda r: (r.ended or r.session_id), reverse=newest_first)
    yield from loaded
