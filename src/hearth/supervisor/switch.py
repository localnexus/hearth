"""supervisor/switch.py — switch-companion as ONE action (ADR 007 stroke 2).

POST /admin/switch = a registry-validated `config/active.toml` write + a
supervised bot restart: today's ritual (edit the selection → stop → relaunch)
performed by the daemon, so the user presses one button instead of following
a runbook. NO bot internals change — the bot still reads active.toml once at
startup (stroke 3 moves pieces live; the button's UX never will change).

File discipline (ADR 007 §2): active.toml stays the durable selection record
and the cold-boot truth. The write is atomic (tmp → rename), keeps unknown
scalar keys a hand-edit may have added, and leaves the previous file beside it
as `active.toml.prev` (rollback = one rename). Hand-edit + restart keeps
working unchanged; comments do not survive a daemon write (the four keys are
the whole contract — the shipped .example documents them).

The facade is deliberately untouched: with a [serve.identity] pin its voice
never followed active.toml anyway; unpinned LLM-leg params follow at the next
facade restart. Reported honestly in the route response, never assumed.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Optional

from hearth.config import config_loader
from hearth.config import settings_registry as sr

SELECTION_KEYS = ("character", "model", "voice", "persona")


def active_path() -> Path:
    """The selection pointer's path (seam: tests point this at a tmp file)."""
    return config_loader.ACTIVE_TOML


# ── read / merge ─────────────────────────────────────────────────────────────

def read_selection(path: Optional[Path] = None):
    """→ (raw dict | None, error | None). Absent file is not an error (None, None)."""
    p = Path(path) if path else active_path()
    if not p.is_file():
        return None, None
    try:
        with open(p, "rb") as f:
            return tomllib.load(f), None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f"unreadable {p.name}: {type(exc).__name__}"


def merge_selection(current: Optional[dict], body: dict) -> dict:
    """Requested keys over the current selection; unknown body keys ignored.
    Empty-string values mean 'not provided'. persona defaults to "default"."""
    merged = {k: v for k, v in dict(current or {}).items() if k in SELECTION_KEYS}
    for key in SELECTION_KEYS:
        val = str(body.get(key) or "").strip()
        if val:
            merged[key] = val
    merged.setdefault("persona", "default")
    return merged


# ── validation (registry shape + on-disk existence) ──────────────────────────

def validate_selection(sel: dict) -> list:
    """Errors that must block the write: registry strict errors (missing keys,
    name-pattern violations) + existence of every piece the bot will load."""
    errors, _warnings = sr.strict_check("active", sel)
    if errors:
        return list(errors)
    checks = []
    try:
        p = config_loader.persona_path(sel["character"], sel.get("persona"))
        if not p.is_file():
            checks.append(f"persona file not found: {p}")
    except config_loader.ConfigError as exc:
        checks.append(str(exc))
    try:
        config_loader.load_model(sel["model"])
    except config_loader.ConfigError as exc:
        checks.append(str(exc))
    try:
        config_loader.load_voice(sel["character"], sel["voice"])
    except config_loader.ConfigError as exc:
        checks.append(str(exc))
    return checks


# ── write (atomic, .prev backup, scalar passthrough) ─────────────────────────

def _toml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def write_selection(sel: dict, path: Optional[Path] = None) -> dict:
    """Write the selection atomically. → {"previous": dict|None, "extras": [...]}.

    Unknown SCALAR keys already in the file are carried over (a hand-edit is
    never silently dropped); a non-scalar extra (a table/array this writer
    cannot re-serialize) REFUSES the write via ValueError — hand-edit instead.
    """
    p = Path(path) if path else active_path()
    previous, prev_err = read_selection(p)
    if prev_err:
        raise ValueError(f"{prev_err} — refusing to overwrite; fix or remove it first")
    extras = {}
    for key, value in (previous or {}).items():
        if key in SELECTION_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            extras[key] = value
        else:
            raise ValueError(
                f"active.toml holds a non-scalar key {key!r} this writer cannot "
                "preserve — hand-edit the file instead")
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# config/active.toml — the selection pointer.",
        f"# Written by the supervisor's /admin/switch on {stamp}"
        + (" (previous file: active.toml.prev)." if previous is not None else "."),
        "# Hand-edits still work exactly as before: edit + restart. Comments do not",
        "# survive a daemon switch; see config/active.toml.example for the field docs.",
        "",
    ]
    for key in SELECTION_KEYS:
        lines.append(f"{key} = {_toml_scalar(sel[key])}")
    for key in sorted(extras):
        lines.append(f"{key} = {_toml_scalar(extras[key])}")
    text = "\n".join(lines) + "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    if previous is not None:
        (p.parent / (p.name + ".prev")).write_bytes(p.read_bytes())
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".active-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)
    except OSError:
        with open(tmp, "a", encoding="utf-8"):  # pragma: no cover — surface path
            pass
        os.unlink(tmp)
        raise
    return {"previous": ({k: previous.get(k) for k in SELECTION_KEYS if k in previous}
                         if previous else None),
            "extras": sorted(extras)}


# ── choices (what the picker can offer) ──────────────────────────────────────

def choices() -> dict:
    """Selectable names across BOTH roots (data first, shipped fallback — the
    config_loader lookup rule, applied to enumeration). Names only, never
    contents: a character is a dir keyed on persona.md, a model on model.toml,
    a voice on voice.toml; persona variants are the persona.<name>.md siblings.
    """
    roots = []
    # _DATA/_ROOT are read at CALL time (unittest patches _DATA; DATA_DIR is import-bound)
    for root in (config_loader._DATA, config_loader._ROOT):
        if root not in roots:
            roots.append(root)
    characters = {}
    for root in roots:
        for marker in (root / "characters").glob("*/persona.md"):
            name = marker.parent.name
            if name.startswith("."):
                continue
            entry = characters.setdefault(name, {"name": name, "voices": [], "personas": set()})
            entry["personas"].add("default")
            for variant in marker.parent.glob("persona.*.md"):
                middle = variant.name[len("persona."):-len(".md")]
                if middle and not middle.startswith("."):
                    entry["personas"].add(middle)
    for entry in characters.values():
        entry["voices"] = config_loader.list_voices(entry["name"])
        entry["personas"] = sorted(entry["personas"])
    models = set()
    for root in roots:
        for marker in (root / "config" / "models").glob("*/model.toml"):
            if not marker.parent.name.startswith("."):
                models.add(marker.parent.name)
    return {"characters": [characters[k] for k in sorted(characters)],
            "models": sorted(models)}
