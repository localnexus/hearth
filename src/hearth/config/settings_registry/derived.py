"""settings_registry/derived.py — the honored surfaces the live modules build from
the declared fields.

Sliced out of the single settings_registry.py it used to share; see the
package __init__ for the layout and the order the parts import in.
"""

from __future__ import annotations

import types
from typing import Union, get_args, get_origin

from .schema_files import _OvLLM, _OvTTS, _OvVAD, _VadLive

# ── derived-surface helpers (derive-knobs stroke) ──────────────────────────────
# The live modules build their honored surfaces from these at import time; the
# declared field constraints above are the only place ranges/defaults are stated.

def _field_bounds(model, name):
    lo = hi = None
    for m in model.model_fields[name].metadata:
        lo = getattr(m, "ge", None) if getattr(m, "ge", None) is not None else lo
        hi = getattr(m, "le", None) if getattr(m, "le", None) is not None else hi
    return lo, hi


def _field_is_int(model, name) -> bool:
    ann = model.model_fields[name].annotation
    return ann is int or int in get_args(ann)


def vad_fallback() -> dict:
    """In-code [vad]-tier fallback = _VadLive defaults (bot.py's pre-tier literals)."""
    return {k: f.default for k, f in _VadLive.model_fields.items()}


def live_knob_ranges(section: str) -> dict:
    """{knob: (lo, hi, is_int)} for the 'tts' / 'vad' override tier, read off the
    declared field constraints — config_knobs' validation ranges derive here."""
    model = {"tts": _OvTTS, "vad": _OvVAD}[section]
    return {k: (*_field_bounds(model, k), _field_is_int(model, k))
            for k in model.model_fields}


def llm_knob_facts() -> dict:
    """The [llm] override tier's declared facts (panel schema + validation)."""
    ann = _OvLLM.model_fields["reasoning_effort"].annotation
    if get_origin(ann) in (types.UnionType, Union):
        for a in get_args(ann):
            if get_origin(a) is not None:
                ann = a
                break
    persona_max = next(m.max_length for m in _OvLLM.model_fields["persona"].metadata
                       if getattr(m, "max_length", None) is not None)
    return {
        "temperature": _field_bounds(_OvLLM, "temperature"),
        "reasoning_effort": frozenset(get_args(ann)),
        "persona_max_len": persona_max,
    }
