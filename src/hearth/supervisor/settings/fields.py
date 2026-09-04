"""settings/fields.py — reading the registry: what a dotted key points at, and how
a value comes in and goes back out as TOML.

Everything here answers one of four questions about a knob the registry
already declared: where a dotted key lands in the models, whether its leaf is
a settable scalar at all, what an incoming JSON value must be, and how the
python value is rendered back as TOML.

Refusals are specific on purpose — "is a table, not a settable key", "holds
structured tables", "is a map — set one entry as name.<name>" — because the
form shows them to a person who is looking at that exact field.

_discovered() is the check CLI's own walk, labeled: one list of files, so the
form and `python -m hearth.config.check` can never disagree about what exists.

One part of the /admin/settings surface; the package __init__ carries
the map of the whole and re-exports every name defined here.
"""

from __future__ import annotations

import json
import re
import types
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

_MAP_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # TOML bare-key safe


def _xh(field) -> dict:
    """The x-hearth extra on a declared field ({} when absent)."""
    extra = field.json_schema_extra
    if isinstance(extra, dict):
        got = extra.get("x-hearth")
        if isinstance(got, dict):
            return got
    return {}


def _scalar_kind(ann) -> str | None:
    """Unwrap Optional/Literal → "bool" | "int" | "float" | "str", else None."""
    if get_origin(ann) is Literal:
        return "str" if all(isinstance(v, str) for v in get_args(ann)) else None
    if get_origin(ann) in (types.UnionType, Union):
        args = [a for a in get_args(ann) if a is not type(None)]
        return _scalar_kind(args[0]) if len(args) == 1 else None
    return {bool: "bool", int: "int", float: "float", str: "str"}.get(ann)


def _resolve(entry, dotted: str):
    """Walk a dotted key through the declared models.
    → (parts_below_top_key, leaf_kind, xh_extra, None) or (None, None, None, error)."""
    from hearth.config import settings_registry as sr

    parts = dotted.split(".")
    if not dotted or any(not p for p in parts):
        return None, None, None, "invalid key"
    model = entry.model
    xh: dict = {}
    i = 0
    while i < len(parts):
        name = parts[i]
        field = model.model_fields.get(name)
        if field is None:
            return None, None, None, f"unknown key '{'.'.join(parts[:i + 1])}'"
        got = _xh(field)
        if got:
            xh = got
        ann = field.annotation
        sub = sr._model_of(ann)
        if sub is not None:
            if i == len(parts) - 1:
                return None, None, None, f"'{dotted}' is a table, not a settable key"
            model = sub
            i += 1
            continue
        if sr._dict_value_model_of(ann) is not None:
            return None, None, None, (f"'{name}' holds structured tables — "
                                      "edit the file by hand")
        if get_origin(ann) is dict:
            args = get_args(ann)
            leaf = _scalar_kind(args[1]) if len(args) == 2 else None
            if leaf is None:
                return None, None, None, f"'{name}' is structured — edit the file by hand"
            if i != len(parts) - 2:
                return None, None, None, (f"'{name}' is a map — set one entry as "
                                          f"{name}.<name>")
            if not _MAP_KEY_RE.fullmatch(parts[i + 1]):
                return None, None, None, "invalid map entry name"
            return parts, leaf, xh, None
        leaf = _scalar_kind(ann)
        if leaf is None:
            return None, None, None, f"'{name}' is structured — edit the file by hand"
        if i != len(parts) - 1:
            return None, None, None, f"'{'.'.join(parts[:i + 1])}' is not a table"
        return parts, leaf, xh, None
    return None, None, None, "invalid key"


def _coerce(value, leaf: str):
    """JSON body value → python value for the leaf type, or (None, error)."""
    if leaf == "bool":
        return (value, None) if isinstance(value, bool) else (None, "expected true or false")
    if leaf == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "expected an integer"
        return value, None
    if leaf == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "expected a number"
        return float(value), None
    return (value, None) if isinstance(value, str) else (None, "expected a string")


def _render_toml(value, leaf: str) -> str:
    if leaf == "bool":
        return "true" if value else "false"
    if leaf == "int":
        return str(value)
    if leaf == "float":
        return repr(value)  # round-trips exactly through tomllib
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def _discovered() -> dict[str, tuple[str, Path]]:
    """label ("DATA/config/memory.toml") → (kind, path), the check CLI's walk."""
    from hearth.config import check

    return {check._rel(p): (kind, p) for kind, p in check.discover()}
