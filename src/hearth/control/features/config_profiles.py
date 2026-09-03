"""features/config_profiles.py — per-scope presets for the live hot knobs (the panel-extension seam).

DROP-IN, ACTIVE — bot.py carries the single activation line
(`import features.config_profiles`, its feature-import block). Importing THIS module
also imports features/config_knobs (it reuses its validated write-path helpers), so
that one line lights up BOTH the /config knobs and these /config/profiles presets.
Remove it and both go inert again — the drop-in seam still holds.

`control.py` needs NO edits (it is the stable route seam).

WHY TWO TIERS
    Settings segment by what they travel with:

      PER-CHARACTER — *how they think*  →  [llm]  temperature, reasoning_effort
          characters/<character>/profile.toml                (under the data root)
      PER-VOICE     — *how it sounds*   →  [tts]  temperature, top_p, top_k, repetition_penalty
          characters/<character>/voices/<voice>/profile.toml (under the data root)

    Both live in the companion's OWN directory (config_loader.companion_data_dir), so a
    companion travels — persona, voices, presets, sessions — as one directory. Beside
    each profile, `overrides.toml` is the LIVE MIRROR: after every panel knob write the
    identity-scope sections ([llm] for the character, [tts] for the voice) are copied
    there (config_knobs._AFTER_WRITE hook), so the directory is always complete without
    an explicit save. The profile stays the deliberate snapshot (save/load/reset
    semantics unchanged); the mirror is write-through only — config/overrides.toml
    remains the single live layer the reloader polls.

    A character has ONE active persona but MANY voice bundles; both `character` and
    `voice` are stable dir-name IDs already in config/active.toml, so keying presets off
    them needs no new identity plumbing. (Persona is deliberately NOT a live knob here —
    it stays a file-swap workflow: persona.md ↔ persona-a.md. The panel only displays the
    active persona filename.) The voice profile stores ONLY the [tts] prosody; the clip
    itself (ref_wav) is the KEY, re-derived from the bundle on load — never stored twice.

WHAT A PROFILE HOLDS
    A snapshot of your OVERRIDES (your deltas from the shipped baseline) for that tier —
    NOT absolute values. This matches config_reload's whole model (desired = baseline ⊗
    overrides): an empty profile == baseline. Saving with nothing dialed away writes a
    baseline preset (a valid "reset-to-default" preset), which is fine.

    A character profile may additionally carry ONE selection key, `voice` — the bundle
    the switch pickers reach for when you move to her (config_loader.preferred_voice).
    Nothing here writes it: it is an operator hand-edit, and save/load/reset leave it
    exactly where it is (the save carries it through rather than erasing it).

API  (all bodies JSON; scopes: "character" | "voice" | "all")
    GET  /config/profiles
        → {ok, active:{character,voice,model,engine}, defaults:{llm,tts},
           overrides:{...}, voices:[names], saved:{character:bool, voices:[names]}}
      `defaults` are the persisted baselines (model.toml [llm] + tts.toml [live]) so the
      panel can render true EFFECTIVE values = override ?? default per knob.
    POST /config/profiles/save   {scope, character, voice?}  — snapshot live tier → profile
    POST /config/profiles/load   {scope, character, voice?}  — profile → live overrides
        (voice-load also selects the clip: sets [voice].ref_wav from the bundle)
    POST /config/profiles/reset  {scope}                     — clear that tier live → baseline
        (character→drop [llm]; voice→drop [tts]; all→blank overrides entirely)
      Reset reverts the LIVE overrides only; it never deletes a saved profile file.

FILE HANDLING mirrors config_knobs: read via tomllib, write via its tiny serializer,
    atomic temp→rename, serialized by the shared config_knobs._WRITE_LOCK. Profiles are
    personal operational state under the data root — never committed (the public
    .gitignore excludes them even when the data root is the checkout).

Auth: none today — solo-LAN (mirrors control.py /say and config_knobs). M1 WEB_TOKEN
drops in at the marked seam when non-solo exposure is wanted.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from aiohttp import web
from loguru import logger

from hearth.config import config_loader
from hearth.config import config_reload
from hearth.control.control_routes import PanelContext, register
from hearth.control.features import config_knobs as ck  # reuse the validated overrides write-path

# Active TTS engine — mirrors bot.py's hardcoded TTS_ENGINE (the reloader's baseline
# subtree + honored-key set are keyed by this). Update in lockstep if bot.py's changes.
_ENGINE = "chatterbox-turbo"

_PROFILE_NAME = "profile.toml"    # the deliberate snapshot
_MIRROR_NAME = "overrides.toml"   # the live identity-scope mirror (write-through)
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")  # dir-name safe; blocks path traversal

# Which override sections each scope owns (the segmentation, in code).
_SCOPE_SECTIONS = {"character": "llm", "voice": "tts"}


class ProfileError(ValueError):
    """A rejected profile op (bad scope/name/missing bundle). Surfaced as HTTP 400."""


# ── names / discovery ─────────────────────────────────────────────────────────────

def _safe(name: str, what: str) -> str:
    if not name or not _NAME_RE.match(name):
        raise ProfileError(f"invalid {what} name: {name!r}")
    return name


def _list_voices(character: str) -> list[str]:
    """Voice bundle names for a character (dirs holding a voice.toml). Empty if none."""
    return config_loader.list_voices(character)


def _active() -> dict:
    sel = config_loader.load_active_selection()
    return {"character": sel["character"], "voice": sel["voice"],
            "model": sel["model"], "engine": _ENGINE}


def _defaults() -> dict:
    """Persisted baselines the panel needs to render effective values. Fail-soft: a bad
    config yields an empty tier rather than a broken panel."""
    llm, tts = {}, {}
    try:
        sel = config_loader.load_active_selection()
        model = config_loader.load_model(sel["model"])
        llm = {"temperature": float(model["temperature"]),
               "reasoning_effort": str(model["reasoning_effort"])}
    except Exception as exc:  # noqa: BLE001
        logger.warning("config_profiles: llm baseline unavailable ({})", type(exc).__name__)
    try:
        tts = config_reload.load_tts_baseline(_ENGINE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("config_profiles: tts baseline unavailable ({})", type(exc).__name__)
    return {"llm": llm, "tts": tts}


# ── profile files ──────────────────────────────────────────────────────────────────

def _char_path(character: str, name: str = _PROFILE_NAME) -> Path:
    return config_loader.companion_data_dir(_safe(character, "character")) / name


def _voice_path(character: str, voice: str, name: str = _PROFILE_NAME) -> Path:
    return (config_loader.companion_data_dir(_safe(character, "character"))
            / "voices" / _safe(voice, "voice") / name)


def _read_profile(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── pure compose helpers (testable; no IO of the live file) ────────────────────────

def _snapshot(scope: str, current: dict, existing: dict | None = None) -> dict:
    """The tier slice of the current overrides to persist as a profile.

    A character profile may also carry `voice` — the remembered voice bundle the
    switch pickers reach for (config_loader.preferred_voice). That is a SELECTION,
    not a knob, and nothing here ever sets it; `existing` carries it through so a
    knob save does not silently erase what a hand-edit put there."""
    section = _SCOPE_SECTIONS[scope]
    kept = (existing or {}).get("voice")
    snap: dict = {}
    if scope == "character" and isinstance(kept, str) and kept:
        snap["voice"] = kept  # first: a bare key after a [table] header joins that table
    snap[section] = dict(current.get(section, {}))
    return snap


def _dump_profile(data: dict) -> str:
    """Serialize a profile: the selection scalar, then the knob tiers.

    ck._dump knows only the knob sections, so it would drop `voice` on the floor;
    it is written here instead, AHEAD of the tables (TOML reads a bare key after a
    `[llm]` header as `llm.voice`)."""
    tiers = ck._dump({k: v for k, v in data.items() if k != "voice"})
    voice = data.get("voice")
    if not voice:
        return tiers
    head, bracket, tables = tiers.partition("\n[")
    line = f"voice = {ck._fmt_scalar(voice)}\n"
    return head.rstrip("\n") + "\n" + line + (("\n" + bracket.lstrip("\n") + tables) if bracket else "")


def _compose_load(scope: str, current: dict, profile: dict,
                  *, ref_wav: str | None = None) -> dict:
    """New overrides after loading `profile` for `scope`. REPLACE semantics for the
    tier (drop the live section, then lay the profile's on top) so a load makes the tier
    exactly the preset. For voice, also set [voice].ref_wav to select the clip. Validates
    via config_knobs._merge — a hand-corrupted profile is rejected, not applied."""
    section = _SCOPE_SECTIONS[scope]
    base = {s: dict(v) for s, v in current.items() if s != section}
    body: dict = {}
    if profile.get(section):
        body[section] = dict(profile[section])
    if scope == "voice" and ref_wav is not None:
        body.setdefault("voice", {})["ref_wav"] = ref_wav
    return ck._merge(base, body)


def _compose_reset(scope: str, current: dict) -> dict:
    if scope == "all":
        return {}
    section = _SCOPE_SECTIONS[scope]
    return {s: dict(v) for s, v in current.items() if s != section}


def _validate_target(scope: str, character: str, voice: str | None) -> None:
    if scope not in ("character", "voice", "all"):
        raise ProfileError(f"unknown scope: {scope!r}")
    if scope == "all":
        return
    _safe(character, "character")
    if not (config_loader.character_dir(character) / "persona.md").is_file():
        raise ProfileError(f"no such character: {character!r}")
    if scope == "voice":
        if not voice:
            raise ProfileError("voice scope needs a voice name")
        _safe(voice, "voice")
        if voice not in _list_voices(character):
            raise ProfileError(f"no such voice bundle: {character}/{voice}")


# ── the live identity mirror (config_knobs after-write hook) ───────────────────────

def mirror_identity(overrides: dict, active: dict | None = None) -> list:
    """Copy the identity-scope sections of the live overrides into the live companion's
    directory: [llm] → characters/<c>/overrides.toml, [tts] → .../voices/<v>/overrides.toml.
    Write-through only (never read back by the reloader). Returns the paths written.
    Fail-soft by contract: the caller (config_knobs) swallows exceptions."""
    if active is None:
        sel = config_loader.load_active_selection()
        active = {"character": sel["character"], "voice": sel["voice"]}
    written = []
    for scope, path in (("character", _char_path(active["character"], _MIRROR_NAME)),
                        ("voice", _voice_path(active["character"], active["voice"], _MIRROR_NAME))):
        data = _snapshot(scope, overrides)
        _atomic_write(path, ck._dump({k: v for k, v in data.items() if v}))
        written.append(path)
    return written


ck._AFTER_WRITE.append(mirror_identity)


# ── the seam contributor ────────────────────────────────────────────────────────

@register
def config_profile_routes(ctx: PanelContext) -> web.RouteTableDef:  # noqa: ARG001 — ctx unused by design
    """GET/POST /config/profiles. `ctx` unused — decoupled from the live pipeline via the
    overrides file, exactly like config_knobs."""
    routes = web.RouteTableDef()

    @routes.get("/config/profiles")
    async def get_profiles(_req: web.Request) -> web.Response:
        try:
            active = _active()
        except Exception as exc:  # no active.toml / malformed — report, don't crash panel
            return web.json_response({"ok": False, "error": f"active selection unavailable: {exc}"}, status=200)
        try:
            overrides = ck._read()
        except Exception as exc:
            overrides = {}
            logger.warning("config_profiles: overrides unreadable ({})", type(exc).__name__)
        voices = _list_voices(active["character"])
        saved = {
            "character": _char_path(active["character"]).exists(),
            "voices": [v for v in voices if _voice_path(active["character"], v).exists()],
        }
        return web.json_response({
            "ok": True, "active": active, "defaults": _defaults(),
            "overrides": overrides, "voices": voices, "saved": saved,
        })

    async def _body(req: web.Request) -> dict:
        try:
            b = await req.json()
        except Exception:
            raise ProfileError("body must be JSON")
        if not isinstance(b, dict):
            raise ProfileError("body must be a JSON object")
        return b

    @routes.post("/config/profiles/save")
    async def save_profile(req: web.Request) -> web.Response:
        # ── M1 auth seam ── (solo-LAN today)
        try:
            b = await _body(req)
            scope = b.get("scope")
            character = b.get("character") or ""
            voice = b.get("voice")
            if scope == "all":
                raise ProfileError("save needs scope 'character' or 'voice'")
            _validate_target(scope, character, voice)
        except ProfileError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        async with ck._WRITE_LOCK:
            try:
                current = ck._read()
            except Exception as exc:
                return web.json_response({"ok": False, "error": f"overrides unparseable: {exc}"}, status=409)
            path = _char_path(character) if scope == "character" else _voice_path(character, voice)
            data = _snapshot(scope, current, existing=_read_profile(path))
            try:
                _atomic_write(path, _dump_profile(data))
            except Exception as exc:  # noqa: BLE001
                logger.warning("config_profiles: save failed ({})", type(exc).__name__)
                return web.json_response({"ok": False, "error": f"save failed: {exc}"}, status=500)
        logger.info("config_profiles: saved {} preset → {}", scope, path.name)
        return web.json_response({"ok": True, "saved": data})

    @routes.post("/config/profiles/load")
    async def load_profile(req: web.Request) -> web.Response:
        try:
            b = await _body(req)
            scope = b.get("scope")
            character = b.get("character") or ""
            voice = b.get("voice")
            if scope == "all":
                raise ProfileError("load needs scope 'character' or 'voice'")
            _validate_target(scope, character, voice)
        except ProfileError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        ref_wav = None
        if scope == "voice":
            try:  # re-derive the clip from the bundle (the key), resolved absolute
                ref_wav = config_loader.load_voice(character, voice)["ref_wav"]
            except Exception as exc:  # noqa: BLE001
                return web.json_response({"ok": False, "error": f"voice bundle unreadable: {exc}"}, status=400)

        async with ck._WRITE_LOCK:
            try:
                current = ck._read()
            except Exception as exc:
                return web.json_response({"ok": False, "error": f"overrides unparseable: {exc}"}, status=409)
            path = _char_path(character) if scope == "character" else _voice_path(character, voice)
            profile = _read_profile(path)
            try:
                new = _compose_load(scope, current, profile, ref_wav=ref_wav)
            except ck.KnobError as exc:  # corrupted profile file
                return web.json_response({"ok": False, "error": f"profile invalid: {exc}"}, status=400)
            try:
                ck._atomic_write(ck._dump(new))
            except Exception as exc:  # noqa: BLE001
                return web.json_response({"ok": False, "error": f"write failed: {exc}"}, status=500)
        logger.info("config_profiles: loaded {} preset → overrides {}", scope, {s: list(v) for s, v in new.items()})
        return web.json_response({"ok": True, "overrides": new})

    @routes.post("/config/profiles/reset")
    async def reset_profile(req: web.Request) -> web.Response:
        try:
            b = await _body(req)
            scope = b.get("scope")
            if scope not in ("character", "voice", "all"):
                raise ProfileError(f"unknown scope: {scope!r}")
        except ProfileError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        async with ck._WRITE_LOCK:
            try:
                current = ck._read()
            except Exception as exc:
                return web.json_response({"ok": False, "error": f"overrides unparseable: {exc}"}, status=409)
            new = _compose_reset(scope, current)
            try:
                ck._atomic_write(ck._dump(new))
            except Exception as exc:  # noqa: BLE001
                return web.json_response({"ok": False, "error": f"write failed: {exc}"}, status=500)
        logger.info("config_profiles: reset {} → overrides {}", scope, {s: list(v) for s, v in new.items()})
        return web.json_response({"ok": True, "overrides": new})

    return routes
