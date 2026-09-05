"""settings_registry/manual.py — the generated settings reference (markdown).

Sliced out of the single settings_registry.py it used to share; see the
package __init__ for the layout and the order the parts import in.
"""

from __future__ import annotations

import types
from typing import Literal, Union, get_args, get_origin

from pydantic import BaseModel

from .registry import ENV_VARS, REGISTRY
from .validate import _dict_value_model_of, _model_of

# ── generated settings reference (markdown) ──────────────────────────────────

def _type_name(annotation) -> str:
    if get_origin(annotation) is Literal:
        return "enum(" + " | ".join(str(a) for a in get_args(annotation)) + ")"
    if get_origin(annotation) in (types.UnionType, Union):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _type_name(args[0]) if len(args) == 1 else " | ".join(_type_name(a) for a in args)
    if _model_of(annotation) is not None:
        return "table"
    if get_origin(annotation) is dict:
        args = get_args(annotation)
        val = _dict_value_model_of(annotation)
        if val is not None:
            return "tables"
        return f"map(str → {getattr(args[1], '__name__', 'any')})" if len(args) == 2 else "map"
    return getattr(annotation, "__name__", str(annotation))


def _constraints(field) -> str:
    lo = hi = None
    for m in field.metadata:
        for attr, slot in (("ge", 0), ("gt", 0), ("le", 1), ("lt", 1)):
            v = getattr(m, attr, None)
            if v is not None:
                lo, hi = (v, hi) if slot == 0 else (lo, v)
        if getattr(m, "max_length", None) is not None:
            return f"≤ {m.max_length} chars"
    if lo is not None or hi is not None:
        return f"{lo if lo is not None else ''}–{hi if hi is not None else ''}"
    return ""


def _default_str(field) -> str:
    if field.is_required():
        return "**required**"
    if field.default_factory is not None:
        return "—"
    d = field.default
    if d is None:
        return "—"
    return f"`{str(d).lower()}`" if isinstance(d, bool) else f"`{d}`"


def _field_rows(model_cls: type[BaseModel], prefix: str = "") -> list[str]:
    rows: list[str] = []
    subtables: list[tuple[str, type[BaseModel]]] = []
    for name, field in model_cls.model_fields.items():
        ann = field.annotation
        extra = (field.json_schema_extra or {}).get("x-hearth", {}) if isinstance(field.json_schema_extra, dict) else {}
        if extra.get("hot_via"):
            live = f"`{extra['hot_via']}`"
        elif extra.get("effect"):  # effect-time stamp: no live path, edit lands at…
            live = f"— (lands at the next restart of {_restart_words(extra['effect'])})"
        else:
            live = "—"
        sub = _model_of(ann)
        if sub is not None:
            subtables.append((f"{prefix}{name}", sub))
        rows.append(f"| `{prefix}{name}` | {_type_name(ann)} | {_default_str(field)} "
                    f"| {_constraints(field)} | {live} | {field.description or ''} |")
    for sub_name, sub_model in subtables:
        rows.extend(_field_rows(sub_model, prefix=f"{sub_name}."))
    return rows


# The machine vocabulary (FileEntry.restart / x-hearth.effect, pinned by tests
# and read by the settings form) rendered in plain words for the reader.
_RESTART_WORDS = {"none": "none", "bot": "the companion", "facade": "Hearth",
                  "bot+facade": "the companion and Hearth"}
_ROLE_WORDS = {"gate": "on/off switch"}


def _restart_words(value: str) -> str:
    return _RESTART_WORDS.get(value, value)


def _role_words(value: str) -> str:
    return _ROLE_WORDS.get(value, value)


_HEADER_ROW = ("| key | type | default | range | live path | what it sets |\n"
               "|---|---|---|---|---|---|")


# The generated reference ships as TWO pages (POL: split along natural seams —
# everyday knobs vs the gate files). Both regenerate from one command and a
# test keeps each byte-synced with this module.
MANUAL_PAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "settings-reference.md": (
        "Settings reference — selection, models, voices, live knobs",
        ("active", "model", "voice", "overrides", "tts-baseline", "vad", "profile"),
    ),
    "settings-reference-gates.md": (
        "Settings reference — the on/off files",
        ("serve", "memory", "openclaw"),
    ),
}


def _render_page(name: str) -> str:
    title, kinds = MANUAL_PAGES[name]
    other = next(n for n in MANUAL_PAGES if n != name)
    out: list[str] = [
        f"# {title}",
        "",
        "> **GENERATED — do not hand-edit.** Source of truth: the settings registry",
        "> (`hearth/config/settings_registry/`). Regenerate both pages:",
        "> `python -m hearth.config.check --emit-manual <this directory>`; a test fails on drift.",
        "",
        f"Companion page: [{other}]({other}). **Live path** = how a setting hot-applies at the next turn",
        "boundary: a `config/overrides.toml` dotted key (the panel writes that layer), or the launch page's",
        "*switch intent* for the selection fields (the COMPANION button / `/admin/switch`).",
        "**Restart** (in each",
        "section header) = what must relaunch for a persisted edit to land: *the companion* = the voice",
        "pipeline (`start.sh`, or the launch page) · *Hearth* = the running program (`hearth.serve`) ·",
        "*none* = applies live. Strict validation",
        "of your install: `python -m hearth.config.check`.",
        "",
    ]
    for kind in kinds:
        entry = REGISTRY[kind]
        out += [
            f"## `{entry.path}` — {entry.title}",
            "",
            f"*{entry.layer} scope · {entry.owner}-owned · {_role_words(entry.role)} · "
            f"restart: {_restart_words(entry.restart)}*",
            "",
        ]
        if entry.note:
            out += [entry.note, ""]
        if entry.top_key:
            out += [f"All keys below live under the `[{entry.top_key}]` table.", ""]
        out += [_HEADER_ROW, *_field_rows(entry.model), ""]
    if name == "settings-reference-gates.md":
        out += [
            "## Prose layers (deliberately not schema'd)",
            "",
            "| file | what it is |",
            "|---|---|",
            "| `characters/<c>/persona.md` | the CHARACTER layer: `## IDENTITY` + `## SOUL` fill the `{{persona}}` slot |",
            "| `config/models/<m>/system-prompt-template.md` | the MODEL layer: envelope, spoken/no-markdown hard rules, `{{persona}}` slot |",
            "",
            "## Environment variables the engine reads",
            "",
            "| var | default | consumer | meaning |",
            "|---|---|---|---|",
            *(f"| `{n}` | `{d}` | {c} | {m} |" for n, d, c, m in ENV_VARS),
            "",
        ]
    return "\n".join(out)


def generate_manual_pages() -> dict[str, str]:
    """filename → rendered markdown, for every generated reference page."""
    return {name: _render_page(name) for name in MANUAL_PAGES}


