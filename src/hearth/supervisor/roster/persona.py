"""roster/persona.py — the persona editor: reading and rewriting an existing
persona.md.

Every write lands in the DATA overlay, always: editing a character whose
persona resolves to the shipped tree copies-on-write into DATA and the lookup
rule shadows it from then on, so the shipped file is never touched. An
overwrite keeps one backup generation (<file>.prev, reported in the response),
and the write is verified with compose_persona — the startup path — with the
previous text restored if composition breaks.

The GET is authed for a reason: persona text is the one piece of a character
that is genuinely hers, and it crosses this wire only behind the bearer, only
to the operator's own browser.

One part of the /admin/roster arc; the package __init__ carries the map of the
whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from aiohttp import web
from loguru import logger

from .forms import _known_characters, _validate_persona_text

_MAX_PERSONA_CHARS = 256_000  # sanity bound on the editor, not a design limit


def _persona_names(character: str, variant: str) -> tuple[Path, Path | None, str | None]:
    """(DATA target, resolved existing file or None, error). The target is
    ALWAYS the DATA-side path — editing a shipped persona copies-on-write into
    the overlay and the lookup rule shadows it from then on."""
    from hearth.config import config_loader

    if character not in _known_characters():
        return Path(), None, f"unknown character {character!r}"
    try:
        resolved = config_loader.persona_path(character, variant or None)
    except config_loader.ConfigError as exc:
        return Path(), None, str(exc)
    fname = ("persona.md" if variant in ("", "default")
             else f"persona.{variant}.md")
    target = config_loader._DATA / "characters" / character / fname
    return target, (resolved if resolved.is_file() else None), None


def _persona_write(character: str, variant: str, text: str) -> dict:
    """Worker thread: backup (one .prev generation) → atomic write → VERIFY
    with compose_persona (the startup path) → roll back if composition breaks."""
    from hearth.config import config_loader

    target, resolved, _err = _persona_names(character, variant)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    fresh = not target.is_file()
    if not fresh:
        backup = target.with_name(target.name + ".prev")
        shutil.copyfile(target, backup)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    try:
        config_loader.compose_persona(character, variant or None)
    except BaseException:
        if fresh:
            target.unlink(missing_ok=True)
        elif backup is not None:
            shutil.copyfile(backup, target)  # the .prev restores the last good text
        raise
    action = ("created variant" if fresh and resolved is None else
              "created DATA overlay (shipped persona untouched, now shadowed)"
              if fresh else "updated")
    return {"action": action,
            "target": f"characters/{character}/{target.name}",
            "backup": (f"characters/{character}/{backup.name}" if backup else None)}


async def _persona_get(request: web.Request) -> web.Response:
    """The persona text, for the editor. Authed — persona content crosses this
    wire only behind the bearer door, and only to the operator's browser."""
    character = str(request.query.get("character") or "")
    variant = str(request.query.get("persona") or "")
    target, resolved, err = _persona_names(character, variant)
    if err:
        status = 404 if "unknown character" in err else 400
        return web.json_response({"error": err}, status=status)
    if resolved is None:
        return web.json_response(
            {"error": f"no persona {variant or 'default'!r} for {character!r}",
             "hint": "POST text to create it"}, status=404)

    def _read() -> str:
        return resolved.read_text(encoding="utf-8")

    return web.json_response({
        "character": character, "persona": variant or "default",
        "text": await asyncio.to_thread(_read),
        "root": ("data" if resolved == target else "shipped"),
        "editable_note": (None if resolved == target else
                          "resolves to the shipped tree — saving writes a DATA "
                          "overlay; the shipped file is never touched"),
    })


async def _persona_post(request: web.Request) -> web.Response:
    """Preview-then-confirm persona write: without ``yes`` nothing persists."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body = an invalid request
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body required"}, status=400)
    character = str(body.get("character") or "")
    variant = str(body.get("persona") or "")
    if variant == "default":
        variant = ""
    text = str(body.get("text") or "")
    target, resolved, err = _persona_names(character, variant)
    if err:
        status = 404 if "unknown character" in err else 400
        return web.json_response({"ok": False, "errors": [err]}, status=status)
    errors = []
    if len(text) > _MAX_PERSONA_CHARS:
        errors.append(f"persona text over the {_MAX_PERSONA_CHARS} character bound")
    persona_err = _validate_persona_text(text)
    if persona_err:
        errors.append(persona_err)
    if errors:
        return web.json_response({"ok": False, "errors": errors}, status=400)

    if not bool(body.get("yes")):
        action = ("update" if target.is_file() else
                  "create DATA overlay (shipped persona stays untouched)"
                  if resolved is not None else "create new variant")
        return web.json_response({
            "ok": True, "written": False, "action": action,
            "target": f"characters/{character}/{target.name}",
            "chars": len(text),
            "confirm": 'validates clean — repeat with "yes": true to write'})
    try:
        result = await asyncio.to_thread(_persona_write, character, variant, text)
    except Exception as exc:  # noqa: BLE001 — rolled back in the worker
        logger.warning("[roster] persona write failed ({})", type(exc).__name__)
        return web.json_response(
            {"ok": False,
             "errors": [f"composition verification failed ({type(exc).__name__}) "
                        "— the previous text was restored"]}, status=422)
    logger.info("[roster] persona written: {} ({})", character, variant or "default")
    return web.json_response({
        "ok": True, "written": True, **result,
        "effect": "composed at bot start / live-switch prepare — switch or "
                  "restart to hear it (nothing re-composes mid-sitting)"})
