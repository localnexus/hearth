"""roster/voices.py — add-a-voice: the clip pipeline pointed at an EXISTING
character.

Create-only per TAG: an existing bundle is refused, never overwritten — a
voice someone auditioned and kept does not get replaced by a form post. The
bundle is written under DATA, which works for shipped characters too because
voice lookup is per-voice; their persona is not copied.

Rollback is narrower than onboarding's on purpose: a failure removes the new
voice directory alone. The character is never touched, because this verb did
not create it.

Provenance rides the DATA side beside the clip, appended to that character's
VOICE-SOURCE.md there (or starting it) — a shipped character keeps its shipped
record intact.

One part of the /admin/roster arc; the package __init__ carries the map of the
whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import shutil
import tempfile
from pathlib import Path

from aiohttp import web
from loguru import logger

from .bundle import (
    _VOICE_SOURCE_ADD, _VOICE_SOURCE_MD, _VOICE_TOML, _condition_clip)
from .forms import _check_voice_fields


def _add_voice(fields: dict, clip_src: Path) -> dict:
    """Worker thread: condition → write the bundle under DATA → VERIFY with
    load_voice (the startup path) → append provenance. A failure rolls back
    only the new voice dir — the character is never touched."""
    from hearth.config import config_loader

    name, tag = fields["name"], fields["tag"]
    date = _dt.date.today().isoformat()
    vdir = config_loader._DATA / "characters" / name / "voices" / tag
    if (vdir / "voice.toml").is_file():  # racing a second submit
        raise FileExistsError(tag)
    vdir.mkdir(parents=True, exist_ok=True)
    try:
        facts = _condition_clip(clip_src, vdir / "sample.wav")
        (vdir / "voice.toml").write_text(
            _VOICE_TOML.format(name=name, tag=tag, date=date,
                               license=fields["license"], source=fields["source"]),
            encoding="utf-8")
        config_loader.load_voice(name, tag)  # the startup loader, not a copy
    except BaseException:
        shutil.rmtree(vdir, ignore_errors=True)  # ours alone, created this call
        raise
    # Provenance rides the DATA side beside the clip: append a section to the
    # character's VOICE-SOURCE.md there, or start the file if this is the
    # first DATA-side voice (a shipped character keeps its shipped record).
    vs = config_loader._DATA / "characters" / name / "VOICE-SOURCE.md"
    entry_args = dict(name=name, tag=tag, date=date, license=fields["license"],
                      source=fields["source"], processing=facts["processing"],
                      duration=facts["duration_s"])
    if vs.is_file():
        with open(vs, "a", encoding="utf-8") as f:
            f.write(_VOICE_SOURCE_ADD.format(**entry_args))
    else:
        vs.write_text(_VOICE_SOURCE_MD.format(**entry_args), encoding="utf-8")
    return {"clip": facts,
            "files": [f"characters/{name}/voices/{tag}/voice.toml",
                      f"characters/{name}/voices/{tag}/sample.wav",
                      f"characters/{name}/VOICE-SOURCE.md"]}


async def _voice_route(request: web.Request) -> web.Response:
    form = await request.post()  # multipart; the file spools to a temp file
    fields, errors = _check_voice_fields(dict(form))
    sample = form.get("sample")
    if not isinstance(sample, web.FileField):
        errors.append("a voice sample file is required")
    if errors:
        return web.json_response({"ok": False, "errors": errors}, status=400)

    confirmed = str(form.get("yes") or "").lower() in ("true", "1", "yes")
    with tempfile.TemporaryDirectory() as scratch:
        src = Path(scratch) / "upload"
        with open(src, "wb") as f:
            shutil.copyfileobj(sample.file, f)
        try:
            if not confirmed:
                with tempfile.TemporaryDirectory() as probe:
                    facts = await asyncio.to_thread(
                        _condition_clip, src, Path(probe) / "sample.wav")
                return web.json_response({
                    "ok": True, "created": False, "clip": facts,
                    "would_write": [f"characters/{fields['name']}/voices/"
                                    f"{fields['tag']}/…"],
                    "confirm": 'everything checks out — repeat with "yes": true '
                               "to add the voice"})
            result = await asyncio.to_thread(_add_voice, fields, src)
        except FileExistsError:
            return web.json_response(
                {"ok": False, "errors": [f"voice {fields['tag']!r} already "
                                         "exists — add-a-voice is create-only"]},
                status=409)
        except ValueError as exc:  # clip problems, named honestly
            return web.json_response({"ok": False, "errors": [str(exc)]}, status=422)
        except Exception as exc:  # noqa: BLE001 — rolled back in _add_voice
            logger.warning("[roster] add-voice failed ({})", type(exc).__name__)
            return web.json_response(
                {"ok": False,
                 "errors": [f"add-voice failed ({type(exc).__name__}) — "
                            "nothing was kept"]}, status=500)
    logger.info("[roster] voice {} added to {}", fields["tag"], fields["name"])
    return web.json_response({
        "ok": True, "created": True, "character": fields["name"],
        "voice": fields["tag"], "loader": "verified (startup loader ran clean)",
        **result,
        "next": "the tag is already in the switch pickers (they read the disk) "
                "— audition by ear before promoting: your ear decides."})
