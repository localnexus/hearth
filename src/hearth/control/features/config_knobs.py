"""features/config_knobs.py — web write-path for live hot knobs (the panel-extension seam).

DROP-IN, ACTIVE — bot.py imports features.config_profiles, which imports this module
(one activation line lights up both); every panel knob write flows through here. The
drop-in property still holds: remove that single import and
`control_routes.contributors()` is empty again — the panel reverts byte-identical to
core-only. `control.py` needs NO edits either way (it is the stable route seam).

WHAT IT DOES
    GET/POST /config read & merge-write `config/overrides.toml` — the LIVE override layer
    that `config_reload.py` already polls at every turn boundary. The routes ONLY touch
    the file; the apply-path (config_reload.py → LLMUpdateSettingsFrame / set_synth_params
    / set_ref_wav) is entirely decoupled. Write a knob → it lands on the NEXT turn. Clear a
    knob → it reverts to baseline next turn (config_reload computes `baseline ⊗ overrides`
    every poll, so a deleted key falls back to the persisted baseline). This is why the
    contributor needs nothing from the live pipeline — `ctx` is unused today.

HONORED SURFACE (mirrors config_reload.py)
    [llm]   temperature (0..2) · reasoning_effort (none|low|medium|high) · persona (str, capped)
    [tts]   temperature · top_p · top_k · repetition_penalty   (chatterbox-turbo honored set)
    [vad]   confidence · start_secs · stop_secs · min_volume   (the CALIBRATION tier —
            per-room/mic listening feel; baseline config/vad.toml [live];
            never carried by character/voice profiles)
    [voice] ref_wav (repo-relative or absolute path; existence checked at write time)
    Rejected (never written): [llm].system_instruction (non-live by design — use persona),
    inert/unknown TTS keys (exaggeration/cfg_weight/min_p on Turbo), unknown sections/keys.
    So the file can't drift into keys the reloader would only warn-and-drop.

SESSION-SCOPED KEYS
    [voice].ref_wav lives only until shutdown: bot.py calls scrub_session_scoped() at
    startup (before the reloader's first poll), so a panel sample switch can never
    outlive its session — between sessions, active.toml owns who-sounds-how. All other
    overrides persist across restarts by design.

API
    GET  /config → {ok, overrides:{...current file...}, schema:{...honored keys+ranges...}}
    POST /config → body may carry any of:
        {"llm":{...}, "tts":{...}, "voice":{...}, "clear":["llm.temperature", "tts.top_p"]}
      A key set to null is treated as a clear. Unrelated existing keys are preserved.
      → {ok:true, overrides:<effective overrides after write>}  (or {ok:false, error} + 4xx)

FILE HANDLING
    Read via stdlib tomllib; written via a tiny local TOML serializer (no writer dep in the
    venv). A malformed overrides.toml makes POST refuse (409) rather than clobber a hand-edit.
    Writes are atomic (temp→rename) and serialized by an asyncio.Lock. When no overrides
    remain, a header-only no-op file is written (byte-identical-behavior restored).
    NOTE: the first panel write REPLACES the hand-documented template with a machine-managed
    file (the documented template lives in git history / config/README.md); blanking the file
    or clearing all keys restores the pure no-op.

Auth: none today — mirrors the existing control.py routes (/say etc.), solo-LAN by
default. A WEB_TOKEN gate drops in at the marked seam below when non-solo exposure
is wanted.
"""

from __future__ import annotations

import asyncio
import os
import tomllib
from pathlib import Path

from aiohttp import web
from loguru import logger

from hearth.config import settings_registry

from hearth.config import config_loader
from hearth.config import config_reload
from hearth.control.control_routes import PanelContext, register

# The live layer config_reload.py polls (same path, by construction).
_OVERRIDES: Path = config_loader.CONFIG_DIR / "overrides.toml"

# Serialize concurrent writers (aiohttp is single-loop, but a POST does read→merge→write).
_WRITE_LOCK = asyncio.Lock()

# After-write hooks: called (fail-soft, under the lock) with the new overrides dict after
# every successful POST /config knob write. config_profiles registers the identity-scope
# mirror here, so the live companion's directory always carries its current knobs.
_AFTER_WRITE: list = []

# ── honored surface — derived from the settings registry (derive-knobs 2026-09-01) ──
_LLM_FACTS = settings_registry.llm_knob_facts()
_REASONING = _LLM_FACTS["reasoning_effort"]
_PERSONA_MAX = _LLM_FACTS["persona_max_len"]
_LLM_TEMP_LO, _LLM_TEMP_HI = _LLM_FACTS["temperature"]
# chatterbox-turbo honored synth knobs; widens to +{exaggeration,cfg_weight} under a proper-
# Chatterbox engine swap (EXPENSIVE tier — not wired). Ranges are sane guardrails, adjustable.
_TTS_RANGES = settings_registry.live_knob_ranges("tts")
# [vad] — the CALIBRATION tier. Ranges are sane guardrails; the panel's
# warn thresholds live in ui/panel_knobs.js KNOB_HELP; ear sessions own the final words.
_VAD_RANGES = settings_registry.live_knob_ranges("vad")
_WRITABLE_SECTIONS = ("llm", "tts", "voice", "vad")

# Static schema surfaced on GET so the UI can render controls without hardcoding.
_SCHEMA = {
    "llm": {
        "temperature": {"type": "float", "min": _LLM_TEMP_LO, "max": _LLM_TEMP_HI},
        "reasoning_effort": {"type": "enum", "values": sorted(_REASONING)},
        "persona": {"type": "str", "max_len": _PERSONA_MAX},
    },
    "tts": {
        k: {"type": "int" if integer else "float", "min": lo, "max": hi}
        for k, (lo, hi, integer) in _TTS_RANGES.items()
    },
    "vad": {
        k: {"type": "int" if integer else "float", "min": lo, "max": hi}
        for k, (lo, hi, integer) in _VAD_RANGES.items()
    },
    "voice": {"ref_wav": {"type": "path"}},
}


# ── validation ──────────────────────────────────────────────────────────────────

class KnobError(ValueError):
    """A rejected knob write (bad section/key/value). Surfaced as HTTP 400."""


def _num(value, lo: float, hi: float, *, integer: bool):
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise KnobError(f"expected a number, got bool {value!r}")
    if integer:
        if not isinstance(value, int):
            raise KnobError(f"expected an integer, got {type(value).__name__}")
        n = value
    else:
        if not isinstance(value, (int, float)):
            raise KnobError(f"expected a number, got {type(value).__name__}")
        n = float(value)
        if n != n:  # NaN
            raise KnobError("NaN is not a valid value")
    if not (lo <= n <= hi):
        raise KnobError(f"out of range [{lo}, {hi}]: {n}")
    return n


def _validate(section: str, key: str, value) -> object:
    """Return the validated/coerced value, or raise KnobError. Mirrors the reloader's
    honored surface so we never persist a key the loop would just warn-and-drop."""
    if section == "vad":
        rng = _VAD_RANGES.get(key)
        if rng is None:
            raise KnobError(f"unknown [vad] key: {key!r}")
        lo, hi, integer = rng
        return _num(value, lo, hi, integer=integer)
    if section == "llm":
        if key == "temperature":
            return _num(value, _LLM_TEMP_LO, _LLM_TEMP_HI, integer=False)
        if key == "reasoning_effort":
            if value not in _REASONING:
                raise KnobError(f"reasoning_effort must be one of {sorted(_REASONING)}")
            return value
        if key == "persona":
            s = str(value)
            if len(s) > _PERSONA_MAX:
                raise KnobError(f"persona exceeds {_PERSONA_MAX} chars")
            return s
        if key == "system_instruction":
            raise KnobError("system_instruction is not a live key — use persona (hard-rules stay pinned)")
        raise KnobError(f"unknown [llm] key: {key!r}")
    if section == "tts":
        rng = _TTS_RANGES.get(key)
        if rng is None:
            raise KnobError(f"[tts].{key} is inert/unknown for chatterbox-turbo")
        lo, hi, integer = rng
        return _num(value, lo, hi, integer=integer)
    if section == "voice":
        if key == "ref_wav":
            return _resolve_ref_or_raise(str(value))
        raise KnobError(f"unknown [voice] key: {key!r}")
    raise KnobError(f"unknown section: [{section}]")


def _resolve_ref_or_raise(ref: str) -> str:
    """Relative → absolute against the data root (then the engine tree, for the shipped
    example clip), like config_reload._resolve_ref, but reject a missing file at WRITE
    time for immediate UI feedback (the reloader would otherwise silently keep the current
    voice next turn). Stores the ORIGINAL ref string so the file stays portable; only
    existence is validated here."""
    p = config_loader.resolve_data_path(ref)
    if not p.resolve().exists():
        raise KnobError(f"ref_wav not found: {p}")
    return ref


# ── merge (pure: current ⊕ body → new overrides dict) ─────────────────────────────

def _merge(current: dict, body: dict) -> dict:
    """Apply POST body to a copy of the current overrides dict. Pure + testable.

    - `clear`: list of "section.key" dotted paths → remove those keys.
    - each writable section in body: set validated keys; a null value clears the key.
    - unrelated existing keys are preserved; empty sections are pruned.
    Raises KnobError on any invalid section/key/value (nothing is written on error —
    callers merge into a copy, then persist only on success).
    """
    result: dict = {sec: dict(vals) for sec, vals in current.items() if isinstance(vals, dict)}

    for dotted in body.get("clear", []) or []:
        section, _, key = str(dotted).partition(".")
        if section in result:
            result[section].pop(key, None)

    for section in _WRITABLE_SECTIONS:
        if section not in body:
            continue
        incoming = body[section]
        if not isinstance(incoming, dict):
            raise KnobError(f"[{section}] must be a table/object")
        for key, value in incoming.items():
            if value is None:
                result.get(section, {}).pop(key, None)
                continue
            result.setdefault(section, {})[key] = _validate(section, key, value)

    # Reject any unknown sections present in the body (defensive — an explicit top-level
    # unknown section is caught here; unknown keys inside writable sections hit _validate above).
    for section in body:
        if section not in (*_WRITABLE_SECTIONS, "clear"):
            raise KnobError(f"unknown section: [{section}]")

    return {sec: vals for sec, vals in result.items() if vals}  # prune empties


# ── serialize (tiny TOML writer for the known flat structure — no writer dep) ─────

_HEADER = (
    "# config/overrides.toml — LIVE override layer (PANEL-MANAGED).\n"
    "# Written by features/config_knobs.py via POST /config; polled each turn by config_reload.py.\n"
    "# No sections ⇒ byte-identical no-op. Revert a knob: POST it null / add to \"clear\", or blank\n"
    "# this file. Hand-editing is fine (same format); the panel preserves unrelated keys.\n"
)


def _fmt_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    s = (
        str(v)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{s}"'


def _dump(data: dict) -> str:
    """Serialize the overrides dict back to TOML. Deterministic section order; scalar
    values only (matches the honored surface — no nested tables/arrays are ever written)."""
    lines = [_HEADER]
    for section in _WRITABLE_SECTIONS:
        vals = data.get(section)
        if not vals:
            continue
        lines.append(f"[{section}]")
        for key, value in vals.items():
            lines.append(f"{key} = {_fmt_scalar(value)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# ── session-scoped keys ────────────────────────────────────────────

# Overrides that live only until shutdown. Between sessions active.toml owns
# who-sounds-how, so a panel sample switch must not silently override it at the
# next boot. bot.py calls the scrub at startup, BEFORE the reloader's first poll.
_SESSION_SCOPED = ("voice.ref_wav",)


def scrub_session_scoped() -> list[str]:
    """Remove session-scoped keys from overrides.toml; return the dotted keys removed.

    Fail-soft on principle (startup must never die here): an absent file is a no-op;
    a malformed file is left for the operator (same posture as POST); a file carrying
    an unknown section is left untouched too — _dump only knows the writable sections,
    so rewriting would silently drop a hand-edit. Sync and lockless by design: runs
    once, before the web server exists."""
    try:
        current = _read()
    except Exception as exc:  # noqa: BLE001 — malformed: leave it, like POST does
        logger.warning("config_knobs: overrides.toml unparseable at startup ({}) — scrub skipped",
                       type(exc).__name__)
        return []
    unknown = [s for s in current if s not in _WRITABLE_SECTIONS]
    if unknown:
        logger.warning("config_knobs: overrides.toml has unknown section(s) {} — scrub skipped "
                       "to preserve a hand-edit", unknown)
        return []
    removed = []
    for dotted in _SESSION_SCOPED:
        section, _, key = dotted.partition(".")
        vals = current.get(section)
        if isinstance(vals, dict) and key in vals:
            vals.pop(key)
            removed.append(dotted)
    if not removed:
        return []
    try:
        _atomic_write(_dump({sec: vals for sec, vals in current.items() if vals}))
    except Exception as exc:  # noqa: BLE001
        logger.warning("config_knobs: scrub write failed ({}) — override may persist",
                       type(exc).__name__)
        return []
    logger.info("config_knobs: session-scoped override(s) cleared at startup: {} — "
                "active.toml owns between-session voice", removed)
    return removed


# ── file IO ───────────────────────────────────────────────────────────────────────

def _read() -> dict:
    """Parse overrides.toml → dict. Absent ⇒ {}. Malformed ⇒ raise (POST refuses to
    clobber a file the user may be mid-edit on)."""
    try:
        with open(_OVERRIDES, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def _atomic_write(text: str) -> None:
    tmp = _OVERRIDES.with_name(_OVERRIDES.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, _OVERRIDES)


# ── the seam contributor ────────────────────────────────────────────────────────

@register
def config_knob_routes(ctx: PanelContext) -> web.RouteTableDef:  # noqa: ARG001 — ctx unused by design
    """Register GET/POST /config. `ctx` is accepted per the contributor contract but
    unused: the write-path is decoupled from the live pipeline through overrides.toml."""
    routes = web.RouteTableDef()

    @routes.get("/config")
    async def get_config(_req: web.Request) -> web.Response:
        try:
            overrides = _read()
        except Exception as exc:  # malformed file — report, don't crash the panel
            return web.json_response(
                {"ok": False, "error": f"overrides.toml unparseable: {exc}", "schema": _SCHEMA},
                status=200,
            )
        # defaults: the calibration-tier baseline rides /config (taxonomy split — the
        # texture-tier baselines ride /config/profiles); the page merges the two.
        return web.json_response({
            "ok": True, "overrides": overrides, "schema": _SCHEMA,
            "defaults": {"vad": config_reload.load_vad_baseline()},
        })

    @routes.post("/config")
    async def post_config(req: web.Request) -> web.Response:
        # ── M1 auth seam ── (solo-LAN today; drop a WEB_TOKEN check here for non-solo use)
        try:
            body = await req.json()
        except Exception:
            return web.json_response({"ok": False, "error": "body must be JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"ok": False, "error": "body must be a JSON object"}, status=400)

        async with _WRITE_LOCK:
            try:
                current = _read()
            except Exception as exc:
                return web.json_response(
                    {"ok": False, "error": f"overrides.toml unparseable — fix or blank it first: {exc}"},
                    status=409,
                )
            try:
                new = _merge(current, body)
            except KnobError as exc:
                return web.json_response({"ok": False, "error": str(exc)}, status=400)
            try:
                _atomic_write(_dump(new))
            except Exception as exc:
                logger.warning("config_knobs: write failed ({})", type(exc).__name__)
                return web.json_response({"ok": False, "error": f"write failed: {exc}"}, status=500)
            for hook in _AFTER_WRITE:
                try:
                    hook(new)
                except Exception as exc:  # noqa: BLE001 — a mirror must never fail the write
                    logger.warning("config_knobs: after-write hook {} failed ({})",
                                   getattr(hook, "__name__", hook), type(exc).__name__)

        logger.info("config_knobs: overrides updated → {}", {s: list(v) for s, v in new.items()})
        return web.json_response({"ok": True, "overrides": new})

    return routes
