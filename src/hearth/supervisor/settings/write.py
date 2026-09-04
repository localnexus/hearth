"""settings/write.py — the set verb: validate, preview, and the one surgical
write.

One scalar key, preview-then-confirm. The order of the checks is the design:
the file's kind must be writable, the key must resolve to a scalar, the value
must coerce, the CURRENT file must parse, and the edited document must be
schema-clean — a value that would introduce a NEW strict-check error is
refused, while errors the file already had are not held against the writer.

Without ``yes`` it stops there and answers old → new plus the honest effect
time. With it: a shipped file copies-on-write into the data root (the engine
tree is never edited in place), one .prev generation is kept, the surgical
edit is parse-verified against the intended document, and the replace is
atomic. Any refusal along the way leaves the file untouched.

One part of the /admin/settings surface; the package __init__ carries
the map of the whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import copy
import tomllib

from aiohttp import web

from .fields import _coerce, _discovered, _render_toml, _resolve
from .policy import _REFUSALS, _WRITABLE
from .surgery import _SurgeryRefused, _deep_get, _deep_set, _surgical_set


def _apply(label: str, dotted: str, value, confirmed: bool):
    """Worker thread for the set verb → (http_status, payload)."""
    from hearth.config import config_loader
    from hearth.config import settings_registry as sr

    disc = _discovered()
    if label not in disc:
        return 404, {"error": f"unknown file {label!r} — GET /admin/settings lists them"}
    kind, path = disc[label]
    if kind in _REFUSALS:
        return 409, {"error": _REFUSALS[kind]}
    if kind not in _WRITABLE:
        return 409, {"error": f"kind {kind!r} is not form-writable"}
    entry = sr.REGISTRY[kind]

    parts, leaf, xh, err = _resolve(entry, dotted)
    if err is not None:
        status = 404 if err.startswith("unknown key") else 400
        return status, {"error": err}
    if xh.get("secret"):
        return 409, {"error": f"'{dotted}' holds a secret — edit it at the desk, "
                              "never through a web form"}
    value, err = _coerce(value, leaf)
    if err is not None:
        return 422, {"error": f"'{dotted}': {err}"}

    try:
        old_text = path.read_text(encoding="utf-8")
        whole = tomllib.loads(old_text)
    except OSError as exc:
        return 500, {"error": f"unreadable: {type(exc).__name__}"}
    except tomllib.TOMLDecodeError:
        return 409, {"error": "the file does not parse — fix it by hand first"}

    full = ([entry.top_key] if entry.top_key else []) + parts
    old_value = _deep_get(whole, full, default=None)
    if isinstance(old_value, str) and "#" in old_value:
        return 409, {"error": f"'{dotted}' currently holds a string containing "
                              "'#' — edit the file by hand"}

    expected = copy.deepcopy(whole)
    _deep_set(expected, full, value)
    inner_old = whole.get(entry.top_key) if entry.top_key else whole
    inner_new = expected.get(entry.top_key) if entry.top_key else expected
    errs_before, _ = sr.strict_check(kind, inner_old if isinstance(inner_old, dict) else {})
    errs_after, _ = sr.strict_check(kind, inner_new if isinstance(inner_new, dict) else {})
    new_errs = [e for e in errs_after if e not in errs_before]
    if new_errs:
        return 422, {"error": "value refused by the declared schema",
                     "detail": new_errs}

    effect = {"hot_via": xh.get("hot_via"), "effect": xh.get("effect"),
              "effect_note": xh.get("effect_note"), "restart": entry.restart}
    base = {"ok": True, "file": label, "key": dotted,
            "old": old_value, "new": value, "effect": effect}
    if not confirmed:
        return 200, {**base, "written": False,
                     "confirm": 'nothing written — repeat with "yes": true'}

    # Shipped tree: copy-on-write into the data root (the persona-editor rule).
    target, copied, target_label = path, False, label
    root = config_loader._ROOT.resolve()
    data_root = config_loader.DATA_DIR.resolve()
    resolved = path.resolve()
    if resolved.is_relative_to(root) and not resolved.is_relative_to(data_root):
        rel = resolved.relative_to(root)
        target = config_loader.DATA_DIR / rel
        if target.exists():
            return 409, {"error": f"a data-root copy already shadows this shipped "
                                  f"file — edit DATA/{rel} instead"}
        target.parent.mkdir(parents=True, exist_ok=True)
        copied = True
        target_label = (f"copied to the data root (shipped file untouched): "
                        f"DATA/{rel}")

    try:
        new_text = _surgical_set(old_text, ".".join(full[:-1]), full[-1],
                                 _render_toml(value, leaf))
        if tomllib.loads(new_text) != expected:
            raise _SurgeryRefused
    except (_SurgeryRefused, tomllib.TOMLDecodeError):
        return 409, {"error": "the edit could not be made without touching "
                              "anything else — edit the file by hand; "
                              "nothing was written"}

    backup = None
    if not copied:  # one .prev generation beside an overwritten file
        backup = target.with_name(target.name + ".prev")
        backup.write_text(old_text, encoding="utf-8")
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(target)
    return 200, {**base, "written": True, "target": target_label,
                 "backup": backup.name if backup is not None else None}


async def _set(request: web.Request) -> web.Response:
    """Preview-then-confirm single-key write (see the module docstring)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body = an invalid request
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body required"}, status=400)
    label = str(body.get("file") or "")
    dotted = str(body.get("key") or "")
    if "value" not in body:
        return web.json_response({"error": "'value' required"}, status=400)
    status, payload = await asyncio.to_thread(
        _apply, label, dotted, body["value"], bool(body.get("yes")))
    return web.json_response(payload, status=status)
