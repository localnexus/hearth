"""audio/devices.py — the devices paired with this Hearth, as rows.

Pairing used to keep no record: a phone traded a six-digit code for the access
key, one line went into the log, and the device name was whatever somebody
typed into a text field at each end. A list nobody keeps is a list nobody can
choose from. So a pair now ENROLS: one row per device, in
``HEARTH_DATA/config/devices.toml``.

    [[device]]
    id = "pixel-3f4a"          the word a route carries (audio/route.py's shape)
    label = "Pixel"            what a person calls it, in their own words
    paired_at = "…"            when it first traded a code for the key
    last_seen = "…"            when a conversation was last started for it, or
                               when it re-paired

Three properties this file is written for:

  * **Only two acts write it** — the claim on the pairing page, and "forget" on
    the launch page (plus one ``last_seen`` stamp per conversation started).
    Nothing on a hot path writes it, and the overrides layer never touches it.
  * **Every write is atomic** — emitted to ``devices.toml.tmp`` beside the file
    and ``os.replace``d onto it, so a reader never sees half a registry.
  * **The one lossy write archives first.** ``enrol`` and ``touch`` rewrite the
    file additively — nothing is lost, so nothing is archived. ``forget``
    removes a row, and the house rule for a load-bearing file outside version
    control is that it is copied beside itself before it can lose anything:
    ``devices.toml.prev-<date>``, seconds appended when the day's name is
    taken, never overwritten.

There is no TOML writer in the tree and this does not add one: the emitter
writes the four string keys and the one table array by hand, which is the whole
of the shape. Reading is ``tomllib``, and a malformed file NEVER raises — it is
an empty list and one warning line, because a device that cannot be listed is a
nuisance and a conversation that will not start is not.

The conversation's own process does not read this. The socket's hello compares
the id the conversation was started FOR against the id on the wire
(audio/remote_transport.py); the start door is what checks an id against this
registry. One check each, in the place that can make it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

from loguru import logger

from hearth.config import config_loader as cl

from .route import DEVICE_RE

#: The file's own first lines, so whoever opens it knows who writes it.
HEADER = """\
# config/devices.toml — the devices paired with this Hearth. Written by the
# pair page's claim and by "forget" on the launch page; never by hand-edits
# of the overrides layer. A row is a device the launch page can route to.
#
# last_seen means the last time a conversation was started for this device, or
# the last time it paired again — not a heartbeat and not a connection.
"""

#: The label's ceiling. It is somebody's words for their own phone, shown in a
#: radio button; past this it is not a name any more.
LABEL_MAX = 40

#: The slug half of a minted id.
SLUG_MAX = 24

#: Anything that would break a line, a quote, or a terminal — out of a label
#: before it is ever written.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SLUG_RUN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Device:
    """One enrolled device. Four strings, and nothing derived."""
    id: str
    label: str
    paired_at: str
    last_seen: str

    def as_dict(self) -> dict:
        """The shape /admin/devices and /admin/state answer in."""
        return {"id": self.id, "label": self.label,
                "paired_at": self.paired_at, "last_seen": self.last_seen}


def devices_toml() -> Path:
    """Where the registry lives: ``HEARTH_DATA/config/devices.toml``."""
    return cl.CONFIG_DIR / "devices.toml"


def _path(path: Path | str | None) -> Path:
    return Path(path) if path is not None else devices_toml()


def stamp(now: datetime | None = None) -> str:
    """A moment, as the file writes it: ``2026-09-18T04:10:11-07:00``. Local
    time WITH its offset, to the second — a person reading the file sees the
    hour they paired the thing, and two rows still order from anywhere."""
    when = now or datetime.now().astimezone()
    if when.tzinfo is None:
        when = when.astimezone()
    return when.isoformat(timespec="seconds")


def clean_label(label: str | None) -> str:
    """Somebody's words for their own device, made safe to write and to read.

    Control characters out, whitespace collapsed, trimmed to LABEL_MAX, and
    "device" when there is nothing left. It is never validated beyond that:
    a label is not an identifier and never becomes one.
    """
    text = _CONTROL.sub("", str(label or ""))
    text = " ".join(text.split())[:LABEL_MAX].strip()
    return text or "device"


def slug(label: str) -> str:
    """The readable half of a minted id: lower case, runs of letters and digits
    joined by dashes, at most SLUG_MAX characters, ``device`` when empty."""
    runs = _SLUG_RUN.findall(str(label or "").lower())
    word = "-".join(runs)[:SLUG_MAX].strip("-")
    return word or "device"


def mint_id(label: str, taken: set[str] | Iterable[str],
            draw: Callable[[], str] | None = None) -> str:
    """A fresh device id: ``<slug of the label>-<4 hex>``, not in ``taken``.

    The hex is what makes two phones both called "Pixel" two different rows;
    the slug is what keeps the id readable in a log line and a route word.
    ``draw`` is the only impure part, taken as an argument so a test can pin it
    — and a draw that keeps colliding widens (a counter) rather than spins.
    """
    taken = set(taken)
    head = slug(label)
    draw = draw or (lambda: secrets.token_hex(2))
    suffix = ""
    for _ in range(64):
        suffix = str(draw())
        candidate = f"{head}-{suffix}"
        if candidate not in taken:
            return candidate
    nth = 2
    while f"{head}-{suffix}-{nth}" in taken:
        nth += 1
    return f"{head}-{suffix}-{nth}"


# ── the file: reading ────────────────────────────────────────────────────────

def _row(entry: object) -> Device | None:
    """One ``[[device]]`` table → a Device, or None when it is not one."""
    if not isinstance(entry, dict):
        return None
    ident = str(entry.get("id") or "")
    if not DEVICE_RE.fullmatch(ident):
        return None
    paired = str(entry.get("paired_at") or "")
    return Device(id=ident,
                  label=clean_label(entry.get("label")),
                  paired_at=paired,
                  last_seen=str(entry.get("last_seen") or paired))


def load(path: Path | str | None = None) -> list[Device]:
    """Every enrolled device, in file order. Never raises: a missing file is
    ``[]`` (an install that has never paired is the ordinary case), and a file
    that cannot be read or parsed is ``[]`` and one warning line."""
    target = _path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("[devices] could not read {} ({}) — no devices listed",
                       target.name, type(exc).__name__)
        return []
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        logger.warning("[devices] {} is malformed ({}) — no devices listed; "
                       "pair a device again to rebuild it",
                       target.name, type(exc).__name__)
        return []
    entries = doc.get("device")
    if not isinstance(entries, list):
        if entries is not None:
            logger.warning("[devices] {} has no [[device]] rows — no devices listed",
                           target.name)
        return []
    rows, dropped = [], 0
    for entry in entries:
        device = _row(entry)
        if device is None:
            dropped += 1
            continue
        rows.append(device)
    if dropped:
        logger.warning("[devices] {} rows in {} have no usable id — skipped",
                       dropped, target.name)
    return rows


def get(device_id: str, path: Path | str | None = None) -> Device | None:
    """The row with that id, or None."""
    for device in load(path):
        if device.id == device_id:
            return device
    return None


# ── the file: the mtime cache ────────────────────────────────────────────────
#
# /admin/state is polled about fifteen times a minute and now carries the device
# list. A file that changes twice a month should not be parsed that often, so
# the poll pays a stat() and parses only when the stat says something moved.

_CACHE: dict[Path, tuple[tuple[int, int] | None, list[Device]]] = {}


def _fingerprint(target: Path) -> tuple[int, int] | None:
    try:
        info = target.stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def load_cached(path: Path | str | None = None) -> list[Device]:
    """``load()``, but a stat() when nothing has changed since the last call."""
    target = _path(path)
    mark = _fingerprint(target)
    cached = _CACHE.get(target)
    if cached is not None and cached[0] == mark:
        return cached[1]
    rows = load(target)
    _CACHE[target] = (mark, rows)
    return rows


def listed(path: Path | str | None = None) -> list[dict]:
    """Every enrolled device as JSON, through the stat cache — what
    /admin/devices answers and what /admin/state carries on every poll."""
    return [device.as_dict() for device in load_cached(path)]


def forget_cache() -> None:
    """Drop what is remembered about every file — for a test, and for a write."""
    _CACHE.clear()


# ── the file: writing ────────────────────────────────────────────────────────

def _toml_string(value: str) -> str:
    """A TOML basic string: quote and backslash escaped, control characters
    stripped again (an emitter that trusts its callers writes a file nothing
    can read)."""
    text = _CONTROL.sub("", str(value))
    return json.dumps(text, ensure_ascii=False)


def emit(devices: Sequence[Device]) -> str:
    """The whole file, as text: the header, then one ``[[device]]`` per row."""
    out = [HEADER]
    for device in devices:
        out.append("\n[[device]]\n"
                   f"id = {_toml_string(device.id)}\n"
                   f"label = {_toml_string(device.label)}\n"
                   f"paired_at = {_toml_string(device.paired_at)}\n"
                   f"last_seen = {_toml_string(device.last_seen)}\n")
    return "".join(out)


def save(devices: Sequence[Device], path: Path | str | None = None) -> Path:
    """Write the registry, atomically: ``.tmp`` beside it, then ``os.replace``.

    Raises OSError like any write; every caller is on a control path (a pair, a
    start, a forget) and decides for itself what a failure means — pairing,
    notably, answers the key anyway."""
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(emit(devices), encoding="utf-8")
    os.replace(tmp, target)
    _CACHE.pop(target, None)
    return target


# The four lines `weights/enroll.py:archive_name` already keeps, COPIED rather
# than imported: hearth.weights drags a GGUF reader and a subprocess scanner
# behind it for a name and a shutil.copy2. Same rule, and a test pins it.

def archive_name(existing: Path, when: datetime | None = None) -> Path:
    """`<name>.prev-<date>`, seconds appended if that name is taken."""
    when = when or datetime.now()
    candidate = existing.with_name(f"{existing.name}.prev-{when:%Y-%m-%d}")
    if candidate.exists():
        candidate = existing.with_name(f"{existing.name}.prev-{when:%Y-%m-%d-%H%M%S}")
    return candidate


def archive_copy(existing: Path) -> Path:
    """Copy `existing` beside itself under `archive_name`; → the copy."""
    dest = archive_name(Path(existing))
    shutil.copy2(existing, dest)
    return dest


# ── the three verbs ──────────────────────────────────────────────────────────

def enrol(label: str | None, device_id: str | None = None,
          now: datetime | None = None, path: Path | str | None = None,
          draw: Callable[[], str] | None = None) -> Device:
    """Pair-time enrolment: refresh the row this device already had, or add one.

    ``device_id`` is what the device says it already is, honoured whenever it
    has the shape of an id: a KNOWN one refreshes that row (new label, new
    ``last_seen``, the original ``paired_at`` kept); an unknown one is adopted
    as written. A claimant naming its own id is not a hole — it arrives with a
    correct pairing code and leaves with the access key, which is strictly more
    than the right to choose a name — and it is what makes the migration one
    screen: a device that paired before this registry keeps the id its route
    word already uses. Anything that is not an id (absent, empty, a shell word,
    80 characters) is ignored and a fresh id is minted from the label.
    """
    rows = load(path)
    when = stamp(now)
    name = clean_label(label)
    wanted = str(device_id or "")
    known = {device.id for device in rows}
    if wanted and DEVICE_RE.fullmatch(wanted):
        if wanted in known:
            fresh = None
            out = []
            for device in rows:
                if device.id == wanted:
                    fresh = Device(device.id, name, device.paired_at, when)
                    out.append(fresh)
                else:
                    out.append(device)
            save(out, path)
            return fresh  # type: ignore[return-value]
        minted = wanted
    else:
        minted = mint_id(name, known, draw)
    device = Device(id=minted, label=name, paired_at=when, last_seen=when)
    save([*rows, device], path)
    return device


def touch(device_id: str, now: datetime | None = None,
          path: Path | str | None = None) -> bool:
    """Stamp ``last_seen`` — a conversation was just started for this device.

    A no-op on an id that is not enrolled, saying so rather than raising: the
    door has already refused an unknown id, and a registry that vanished
    between the two is not worth a failed start."""
    rows = load(path)
    hit = False
    out = []
    for device in rows:
        if device.id == device_id:
            hit = True
            out.append(Device(device.id, device.label, device.paired_at, stamp(now)))
        else:
            out.append(device)
    if hit:
        save(out, path)
    return hit


def forget(device_id: str, path: Path | str | None = None) -> Path | None:
    """Remove a device's row; the file is copied beside itself FIRST.

    → the archive it left (truthy) when a row went, None when there was none.
    The copy is made only when there is something to lose, and never
    overwrites one. The caller answers with the path rather than predicting it,
    so what it names is the file that exists."""
    target = _path(path)
    rows = load(target)
    if not any(device.id == device_id for device in rows):
        return None
    archived = archive_copy(target) if target.exists() else None
    save([device for device in rows if device.id != device_id], target)
    return archived
