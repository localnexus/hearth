"""roster/onboard.py — onboarding a NEW character: the transaction, its dry
run, and the route.

The transaction's proof is that it ends by running the STARTUP loaders
themselves — load_voice and compose_persona, not a copy of their rules — and
rolls the whole new directory back if either refuses. That is the only reason
the wizard can promise a character it created will load at the next start.

The dry run is the same request without ``yes``: every validation runs and the
clip is conditioned in a scratch directory that is then discarded, so a person
can see what would happen before anything exists on disk.

The tier entry is the last step and the one thing here that is not hot: it is
recorded through memory/enroll.py, and the response says plainly that
enrollment lands at the next process start.

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

from hearth.memory.enroll import enroll_memory_tier as _enroll_memory_tier

from .bundle import _VOICE_SOURCE_MD, _VOICE_TOML, _condition_clip
from .forms import _check_fields


def _onboard(fields: dict, clip_src: Path) -> dict:
    """Worker thread: condition → scaffold → write → VERIFY (the exact startup
    loaders) → tier entry. A failure after scaffolding rolls the new dir back."""
    from hearth.config import config_loader

    name, tag = fields["name"], fields["tag"]
    date = _dt.date.today().isoformat()
    char_dir = config_loader._DATA / "characters" / name
    if char_dir.exists():  # racing a second submit — create-only stands
        raise FileExistsError(name)
    vdir = char_dir / "voices" / tag
    vdir.mkdir(parents=True)
    try:
        facts = _condition_clip(clip_src, vdir / "sample.wav")
        (vdir / "voice.toml").write_text(
            _VOICE_TOML.format(name=name, tag=tag, date=date,
                               license=fields["license"], source=fields["source"]),
            encoding="utf-8")
        (char_dir / "persona.md").write_text(fields["persona"], encoding="utf-8")
        (char_dir / "VOICE-SOURCE.md").write_text(
            _VOICE_SOURCE_MD.format(name=name, tag=tag, date=date,
                                    license=fields["license"], source=fields["source"],
                                    processing=facts["processing"],
                                    duration=facts["duration_s"]),
            encoding="utf-8")
        # The loader-verification probe = the startup path itself, not a copy.
        config_loader.load_voice(name, tag)
        config_loader.compose_persona(name)
    except BaseException:
        shutil.rmtree(char_dir, ignore_errors=True)  # ours, created this call
        raise
    note = _enroll_memory_tier(name, fields["tier"]) if fields["tier"] else \
        "memory tier untouched (per-companion default applies)"
    return {"clip": facts, "memory": note,
            "files": [f"characters/{name}/persona.md",
                      f"characters/{name}/VOICE-SOURCE.md",
                      f"characters/{name}/voices/{tag}/voice.toml",
                      f"characters/{name}/voices/{tag}/sample.wav"]}


def _dry_run(fields: dict, clip_src: Path) -> dict:
    """Everything validated, clip conditioned in scratch, nothing persisted."""
    with tempfile.TemporaryDirectory() as scratch:
        facts = _condition_clip(clip_src, Path(scratch) / "sample.wav")
    return {"clip": facts,
            "would_write": [f"characters/{fields['name']}/…"],
            "memory": (f'would enroll {fields["name"]} = "{fields["tier"]}"'
                       if fields["tier"] else "memory tier untouched")}


async def _onboard_route(request: web.Request) -> web.Response:
    form = await request.post()  # multipart; the file spools to a temp file
    fields, errors = _check_fields(dict(form))
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
                report = await asyncio.to_thread(_dry_run, fields, src)
                return web.json_response({
                    "ok": True, "created": False, **report,
                    "confirm": 'everything checks out — repeat with "yes": true '
                               "to create the character"})
            result = await asyncio.to_thread(_onboard, fields, src)
        except FileExistsError:
            return web.json_response(
                {"ok": False, "errors": [f"character {fields['name']!r} already "
                                         "exists — the wizard is create-only"]},
                status=409)
        except ValueError as exc:  # clip problems, named honestly
            return web.json_response({"ok": False, "errors": [str(exc)]}, status=422)
        except Exception as exc:  # noqa: BLE001 — rolled back in _onboard
            logger.warning("[roster] onboarding failed ({})", type(exc).__name__)
            return web.json_response(
                {"ok": False,
                 "errors": [f"onboarding failed ({type(exc).__name__}) — "
                            "nothing was kept"]}, status=500)
    logger.info("[roster] onboarded character {} (voice {})",
                fields["name"], fields["tag"])
    return web.json_response({
        "ok": True, "created": True, "character": fields["name"],
        "voice": fields["tag"], "loader": "verified (startup loaders ran clean)",
        **result,
        "next": "select + restart via /admin/launch — composition happens once "
                "at startup. Audition by ear before promoting: your ear decides."})
