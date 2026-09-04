"""settings/policy.py — what a form may write, and what it may never show.

Two rules that both halves of the surface consult, which is why they sit above
both. What a form may WRITE: seven kinds, and every refusal points somewhere
real rather than just saying no — the selection has its own orchestrated
surface, the panel-owned layers belong to the panel.

What a form may never SHOW: x-hearth `secret` fields are redacted server-side,
including every value of a secret-marked map. The values never leave the file,
even behind the bearer, and the redaction happens here rather than in the page
so no future caller can forget it.

One part of the /admin/settings surface; the package __init__ carries
the map of the whole and re-exports every name defined here.
"""

from __future__ import annotations

from .fields import _xh

_REDACTED = "•••"


_WRITABLE = frozenset({"model", "voice", "serve", "memory", "openclaw",
                       "tts-baseline", "vad"})
_REFUSALS = {
    "active": "the selection pointer has its own orchestrated surface — "
              "switch via /admin/switch (the COMPANION button applies it live)",
    "overrides": "panel-managed live layer — tune these knobs on the :65000 "
                 "control panel, which owns this file",
    "profile": "panel-managed preset snapshot — save presets from the :65000 "
               "control panel, which owns this layer",
}


def _redact(value):
    if isinstance(value, dict):
        return {k: _REDACTED for k in value}
    if isinstance(value, str):
        return _REDACTED if value else ""
    return _REDACTED


def _redacted_values(model_cls, data: dict) -> dict:
    from hearth.config import settings_registry as sr

    out: dict = {}
    for key, value in data.items():
        field = model_cls.model_fields.get(key)
        if field is None:
            out[key] = value  # unknown key: the verdict flags it; shown as data
            continue
        if _xh(field).get("secret"):
            out[key] = _redact(value)
            continue
        sub = sr._model_of(field.annotation)
        if sub is not None and isinstance(value, dict):
            out[key] = _redacted_values(sub, value)
            continue
        vm = sr._dict_value_model_of(field.annotation)
        if vm is not None and isinstance(value, dict):
            out[key] = {n: _redacted_values(vm, item) if isinstance(item, dict) else item
                        for n, item in value.items()}
            continue
        out[key] = value
    return out
