"""supervisor/settings.py — /admin/settings: the generated settings surface.

Schema-driven settings, STEP 2 (the proposal's "chrome" half): the settings
registry (step 1) declared every file-configurable knob — path, type, range,
default, owner, live path or effect time. This module serves that declaration
over JSON and hosts the generated form page that renders it, so a knob cannot
exist without appearing (the registry's coverage property, now user-facing).

Toolchain decision (step 2's "decide once"): NO front-end toolchain. The form
generator is plain JS in one static shell (settings_page.html, the fifth
auth-exempt page) walking the served JSON Schema — the proposal's own
static-front-end reasoning, satisfied by the facade's proven page idiom
instead of a second build stack.

Reading: every discovered file's verdict comes from the same strict check as
``python -m hearth.config.check``; values are the parsed TOML with
secret-marked fields REDACTED server-side (x-hearth ``secret``: the hindsight
API key and the env maps — their values never leave the file, even behind the
door). Verdict errors/warnings name KEYS only, never values (check.py's
contract).

Writing (preview-then-confirm, per the write-layer rule (c) — all mutation on
the facade, behind the bearer):

- ONE scalar key per write, via comment-preserving line surgery
  (generalizing the roster wizard's memory.toml insertion): replace the value
  on the key's own line (trailing comment kept) or insert the line under its
  section header. The edited text must parse AND equal the intended document
  exactly (everything else byte-equal semantically) or the write is REFUSED
  with "edit by hand" — surgery never guesses.
- Refusals are honest pointers: ``active`` → /admin/switch (a switch is an
  orchestration, not a file poke) · ``overrides``/``profile`` → the :65000
  panel (never fight the panel's own writer) · secret-marked fields → the
  desk · structured values (lists, sub-table maps) → the file.
- A file resolving to the SHIPPED tree copies-on-write into the data root
  (the persona-editor pattern): the engine tree is never edited in place.
- One ``.prev`` backup generation beside an overwritten file; atomic
  tmp → replace.
- Every response states the honest effect time from the field's x-hearth
  stamp (live path · "lands at bot+facade restart" + note) with the file's
  registry ``restart`` as the fallback — nothing pretends to apply live.

API (mounted by routes.build_mount iff [serve.supervisor] enabled; authed):
    GET  /admin/settings          → per-kind registry facts + discovered files
                                    with strict-check verdicts (keys only)
    GET  /admin/settings/schema   → the registry's JSON Schema bundle
    GET  /admin/settings/file?file=<label> → one file's values (redacted)
    POST /admin/settings/set {file, key, value, yes?}
         yes absent/false → validated preview (old → new + effect time),
         nothing written; yes true → surgical write
    GET  /admin/settings/ui       → the generated form page (static chrome,
         auth-exempt beside the launch/roster/memory shells)
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import tomllib
import types
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

from aiohttp import web

from hearth.ui import admin_shell
from hearth.ui import brand
from hearth.ui import pages

_PAGE = pages.Page(Path(__file__).parent / "settings_page.html",
                   pages.chain(admin_shell.splice, brand.splice))

_REDACTED = "•••"

# Kinds a form may write; everything else answers with an honest pointer.
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

_MAP_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # TOML bare-key safe


# ── registry helpers ──────────────────────────────────────────────────────────

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


# ── redaction (x-hearth secret: values never leave the file) ──────────────────

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


# ── comment-preserving line surgery ───────────────────────────────────────────

class _SurgeryRefused(Exception):
    """The edit cannot be made without guessing — the caller reports
    'edit by hand' and the file stays byte-identical."""


def _surgical_set(text: str, section: str, key: str, rendered: str) -> str:
    """Set `key = rendered` under [section] ("" = the root table), touching
    nothing else. The caller MUST parse-verify the result against the intended
    document before writing — this function aims, the verification decides."""
    line = f"{key} = {rendered}"
    if section:
        m = re.search(rf"(?m)^\[[ \t]*{re.escape(section)}[ \t]*\][ \t]*$", text)
        if m is None:  # no such section header yet: append a fresh one
            base = text if not text or text.endswith("\n") else text + "\n"
            sep = "\n" if base.strip() else ""
            return base + sep + f"[{section}]\n{line}\n"
        start = m.end()
        nxt = re.compile(r"(?m)^\[").search(text, start)
        end = nxt.start() if nxt is not None else len(text)
    else:
        start = 0
        nxt = re.compile(r"(?m)^\[").search(text)
        end = nxt.start() if nxt is not None else len(text)
    span = text[start:end]
    km = re.search(rf"(?m)^(?P<ind>[ \t]*){re.escape(key)}[ \t]*=[ \t]*(?P<rest>[^\n]*)$",
                   span)
    if km is None:  # key not present: insert at the end of the section's span
        if section:  # right below the header keeps related keys together
            return text[:start] + "\n" + line + text[start:]
        seg = text[:end]
        if seg and not seg.endswith("\n"):
            seg += "\n"
        return seg + line + "\n" + text[end:]
    rest = km.group("rest")
    # Trailing comment survives. Callers refuse string values containing '#'
    # upstream, so a '#' in `rest` here can only start a comment.
    idx = rest.find("#")
    comment = rest[idx:].rstrip() if idx >= 0 else ""
    new_line = km.group("ind") + line + (("  " + comment) if comment else "")
    new_span = span[:km.start()] + new_line + span[km.end():]
    return text[:start] + new_span + text[end:]


def _deep_set(doc: dict, parts: list[str], value) -> None:
    cur = doc
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _deep_get(doc: dict, parts: list[str], default=None):
    cur = doc
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


# ── discovery (the check CLI's own walk, labeled) ─────────────────────────────

def _discovered() -> dict[str, tuple[str, Path]]:
    """label ("DATA/config/memory.toml") → (kind, path), the check CLI's walk."""
    from hearth.config import check

    return {check._rel(p): (kind, p) for kind, p in check.discover()}


# ── handlers ──────────────────────────────────────────────────────────────────

async def _overview(request: web.Request) -> web.Response:
    """Registry facts per kind + every discovered file's strict verdict
    (errors/warnings name keys only — check.py's contract)."""
    def _scan() -> list[dict]:
        from hearth.config import check
        from hearth.config import settings_registry as sr

        kinds = {k: {"kind": k, "title": e.title, "path": e.path, "role": e.role,
                     "owner": e.owner, "layer": e.layer, "restart": e.restart,
                     "note": e.note, "top_key": e.top_key,
                     "writable": k in _WRITABLE,
                     "pointer": _REFUSALS.get(k), "files": []}
                 for k, e in sr.REGISTRY.items()}
        for kind, path in check.discover():
            verdict, errors, warnings = check.check_file(kind, path)
            kinds[kind]["files"].append({
                "file": check._rel(path), "verdict": verdict,
                "errors": errors, "warnings": warnings,
            })
        return list(kinds.values())

    return web.json_response({"kinds": await asyncio.to_thread(_scan)})


async def _schema(request: web.Request) -> web.Response:
    """The step-2 form contract, verbatim from the registry."""
    def _emit() -> dict:
        from hearth.config import settings_registry as sr

        return sr.json_schema()

    return web.json_response({"schema": await asyncio.to_thread(_emit)})


async def _file(request: web.Request) -> web.Response:
    """One discovered file's parsed values (secret fields redacted)."""
    label = request.query.get("file") or ""

    def _load():
        from hearth.config import check
        from hearth.config import settings_registry as sr

        disc = _discovered()
        if label not in disc:
            return None
        kind, path = disc[label]
        entry = sr.REGISTRY[kind]
        verdict, errors, warnings = check.check_file(kind, path)
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        if entry.top_key is not None:
            inner = data.get(entry.top_key)
            data = inner if isinstance(inner, dict) else {}
        return {"kind": kind, "file": label, "verdict": verdict,
                "errors": errors, "warnings": warnings,
                "writable": kind in _WRITABLE, "pointer": _REFUSALS.get(kind),
                "values": _redacted_values(entry.model, data)}

    got = await asyncio.to_thread(_load)
    if got is None:
        return web.json_response(
            {"error": f"unknown file {label!r} — GET /admin/settings lists them"},
            status=404)
    return web.json_response(got)


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


async def _page(_req: web.Request) -> web.Response:
    """The generated form page — static chrome (see settings_page.html's
    security contract; the serve middleware exempts exactly this path)."""
    return web.Response(text=_PAGE(), content_type="text/html")


def add_routes(app: web.Application) -> None:
    """Called by routes.build_mount — same door, same middleware."""
    app.router.add_get("/admin/settings", _overview)
    app.router.add_get("/admin/settings/schema", _schema)
    app.router.add_get("/admin/settings/file", _file)
    app.router.add_get("/admin/settings/ui", _page)
    app.router.add_post("/admin/settings/set", _set)
