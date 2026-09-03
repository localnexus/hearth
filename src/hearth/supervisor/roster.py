"""supervisor/roster.py — /admin/roster: the character-roster wizard behind the door.

The onboarding half of the roster-management arc (facade-hosted per the
write-layer rule signed (c) 2026-09-02: :65000 displays the roster and links
over; every operator-layer write lives HERE, behind the bearer). The wizard
mechanizes the users-manual's six onboarding steps along the design's signed
split — invisible everything mechanical, visible only what a person actually
decides:

  MECHANIZED: directory scaffolding · clip conditioning (ffmpeg → mono
  24 kHz s16 when available; a conforming WAV is accepted as-is without it) ·
  voice.toml generation (registry-shaped: the VoiceFile schema is the form
  contract) · VOICE-SOURCE.md provenance generation from the same answers
  (one entry of truth, two files written) · the loader-verification probe
  (config_loader.load_voice + compose_persona — the exact startup path) ·
  the [memory.companions] tier entry.

  KEPT VISIBLE: the name · the persona text (## IDENTITY + ## SOUL) · the
  sample + its license/source attestation (provenance REQUIRES a human
  answer) · the memory tier. Audition/promotion stays a human step — the
  manual's rule stands: your ear decides. The wizard ends by handing off to
  /admin/launch; it grows no restart button of its own.

Preview-then-confirm, stateless: without ``yes`` the SAME multipart request
runs every validation (clip conditioned in scratch and discarded) and answers
a report; nothing persists. Confirmed, the wizard is CREATE-ONLY — an
existing character (either root) answers 409; editing a living persona is a
different, later surface. A failed verification rolls the new directory back.

memory.toml is edited by targeted line insertion under [memory.companions]
(comments preserved; parse-verified before the atomic replace) — and the
response says plainly that enrollment lands at the next process start (the
effect-time audit: nothing under [memory] is hot).

API (mounted iff [serve.supervisor] enabled):
    GET  /admin/roster         → the wizard page (static contentless shell,
                                 auth-exempt like /admin/launch; data authed)
    GET  /admin/roster/state   → roster listing: names, voices, personas,
                                 memory backend map, active selection, ffmpeg
    POST /admin/roster/onboard → multipart form; "yes" absent = dry-run report
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import re
import shutil
import subprocess
import tempfile
import tomllib
import wave
from pathlib import Path

from aiohttp import web
from loguru import logger

from . import switch as switch_mod

_PAGE = (Path(__file__).parent / "roster_page.html").read_text(encoding="utf-8")

_TIERS = ("", "floor", "hindsight")  # "" = don't touch memory.toml
_FFMPEG_TIMEOUT_S = 60.0
_MAX_CLIP_S = 120.0   # sanity bound; conditioning keeps the file, the engine reads ~15 s
_MIN_CLIP_S = 3.0

_VOICE_TOML = """\
# characters/{name}/voices/{tag}/voice.toml — voice descriptor (roster wizard, {date}).
#
# CLONING NOTE — the TTS engine conditions on only the first ~10–15 s of the clip;
# audio past that is ignored, so trim your reference to its best clean 10–15 s.
# See docs/bring-your-own-voice.md.

tag = "{tag}"
ref_wav = "sample.wav"

# ── Provenance / license (also recorded in ../../VOICE-SOURCE.md) ──
license = {license!r}
source  = {source!r}
"""

_VOICE_SOURCE_MD = """\
# Voice source — {name}/{tag}

Recorded by the roster wizard, {date}. The `license`/`source` pair below also
lives in the bundle's `voice.toml` — one answer, two enforceable places.

| Field | Value |
|---|---|
| License | {license} |
| Source | {source} |
| Processing | {processing} |
| Duration | {duration:.2f} s |

A voice cloned from a copyrighted character, a real performer, or an unclear
source is LOCAL ONLY: never shipped, shared, published, or reaching any public
artifact (docs/COMPONENT-LICENSING.md — the restriction rides the clip).
"""


# ── clip probing + conditioning ───────────────────────────────────────────────

def _probe_wav(path: Path) -> dict:
    """Structural facts of a WAV via stdlib (raises ValueError if unreadable)."""
    try:
        with wave.open(str(path), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
            return {"channels": w.getnchannels(), "rate": rate,
                    "sample_width": w.getsampwidth(),
                    "duration_s": round(frames / float(rate or 1), 2)}
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError(f"not a readable WAV ({type(exc).__name__})") from exc


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def _check_duration(facts: dict) -> None:
    d = facts["duration_s"]
    if not _MIN_CLIP_S <= d <= _MAX_CLIP_S:
        raise ValueError(
            f"clip is {d:.1f} s — a clone reference wants {_MIN_CLIP_S:.0f}–"
            f"{_MAX_CLIP_S:.0f} s (the engine reads only the first ~15 s)")


def _condition_clip(src: Path, dst: Path) -> dict:
    """src → dst as the manual's reference format (mono 24 kHz s16 WAV).

    With ffmpeg: any input format transcodes (fixed argv, no shell, bounded).
    Without: a readable WAV is copied as-is — facts reported, format advisory
    included — and any other container is refused honestly.
    """
    ff = ffmpeg_path()
    if ff:
        proc = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
             "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", str(dst)],
            capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S)
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no detail"]
            raise ValueError(f"ffmpeg could not read the clip: {tail[0][:200]}")
        facts = _probe_wav(dst)
        _check_duration(facts)
        facts["processing"] = "transcoded to mono 24 kHz s16 (ffmpeg)"
        return facts
    facts = _probe_wav(src)  # no ffmpeg: WAV in, as-is
    _check_duration(facts)
    shutil.copyfile(src, dst)
    notes = []
    if facts["channels"] != 1:
        notes.append(f"{facts['channels']} channels (mono recommended)")
    if not 22_000 <= facts["rate"] <= 26_000:
        notes.append(f"{facts['rate']} Hz (~24 kHz recommended)")
    facts["processing"] = ("kept as-is — ffmpeg not installed"
                           + ("; advisory: " + ", ".join(notes) if notes else ""))
    return facts


# ── form validation ───────────────────────────────────────────────────────────

def _validate_persona_text(text: str) -> str | None:
    """The compose_persona contract on SUBMITTED text (same regex, no file):
    '## IDENTITY' + '## SOUL', both non-empty after comment stripping."""
    from hearth.config import config_loader

    stripped = config_loader._strip_comments(text or "")
    parts = re.split(r"^##\s+([A-Za-z]+)\s*$", stripped, flags=re.MULTILINE)
    sections = {parts[i].strip().upper(): parts[i + 1].strip()
                for i in range(1, len(parts) - 1, 2)}
    for required in ("IDENTITY", "SOUL"):
        if not sections.get(required):
            return f"persona needs a non-empty '## {required}' section"
    return None


def _known_characters() -> set[str]:
    return {c["name"] for c in switch_mod.choices()["characters"]}


def _check_fields(form: dict) -> tuple[dict, list[str]]:
    """(cleaned fields, errors). Names dir-safe; provenance answered; tier known."""
    from hearth.config import config_loader

    errors: list[str] = []
    name = str(form.get("name") or "").strip()
    tag = str(form.get("voice_tag") or "default").strip()
    for label, value in (("character name", name), ("voice tag", tag)):
        if not config_loader._NAME_RE.match(value) or value.startswith("."):
            errors.append(f"invalid {label} (letters, digits, . _ - only)")
    if name and name in _known_characters():
        errors.append(f"character {name!r} already exists — the wizard is "
                      "create-only (persona edits are a later surface)")
    persona_err = _validate_persona_text(str(form.get("persona") or ""))
    if persona_err:
        errors.append(persona_err)
    license_ = str(form.get("license") or "").strip() or "personal-use-only"
    source = str(form.get("source") or "").strip()
    if not source:
        errors.append("source attestation is required — where the clip came "
                      "from cannot be mechanized")
    tier = str(form.get("memory_tier") or "").strip()
    if tier not in _TIERS:
        errors.append(f"unknown memory tier {tier!r}")
    return ({"name": name, "tag": tag, "license": license_, "source": source,
             "tier": tier, "persona": str(form.get("persona") or "")}, errors)


# ── memory.toml tier entry (targeted, comment-preserving) ─────────────────────

def _enroll_memory_tier(name: str, tier: str) -> str:
    """Insert `<name> = "<tier>"` under [memory.companions]; parse-verified
    before the atomic replace. Returns a human note — never raises past the
    caller's containment (a failed enrollment must not undo an onboarding)."""
    from hearth.config import config_loader

    path = config_loader.MEMORY_TOML  # the one path every consumer reads
    if not path.is_file():
        return ("memory.toml absent — tier not recorded (enable memory, then "
                f'add {name} = "{tier}" under [memory.companions])')
    text = path.read_text(encoding="utf-8")
    try:
        existing = dict(tomllib.loads(text).get("memory", {}).get("companions") or {})
    except tomllib.TOMLDecodeError:
        return "memory.toml did not parse — tier not recorded (fix it, then enroll by hand)"
    if name in existing:
        return f"already enrolled as {existing[name]!r} — left untouched"
    line = f'{name} = "{tier}"'
    m = re.search(r"(?m)^\[memory\.companions\][ \t]*$", text)
    if m:
        new_text = text[:m.end()] + "\n" + line + text[m.end():]
    else:
        new_text = text.rstrip("\n") + f"\n\n[memory.companions]\n{line}\n"
    parsed = dict(tomllib.loads(new_text).get("memory", {}).get("companions") or {})
    if parsed.get(name) != tier:  # never replace the file with a bad edit
        return "edit verification failed — tier not recorded (enroll by hand)"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return (f'enrolled: {name} = "{tier}" — lands at the next bot/facade start '
            "(nothing under [memory] applies live)")


# ── the transaction ───────────────────────────────────────────────────────────

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


# ── handlers ──────────────────────────────────────────────────────────────────

async def _page(_req: web.Request) -> web.Response:
    return web.Response(text=_PAGE, content_type="text/html")


async def _state(request: web.Request) -> web.Response:
    """Roster listing — names only (voices, personas, tier map, active pick)."""
    from hearth.config import config_loader

    def _build() -> dict:
        chars = switch_mod.choices()["characters"]
        mem = config_loader.load_memory_config()
        active: dict = {}
        try:
            sel = config_loader.load_active_selection()
            active = {"character": sel.get("character"), "voice": sel.get("voice")}
        except Exception:  # noqa: BLE001 — an unreadable active.toml is display-only here
            pass
        for c in chars:
            c["memory_backend"] = (
                None if mem is None else
                str(dict(mem.get("companions") or {}).get(
                    c["name"], mem.get("backend", "floor"))))
        return {"characters": chars, "active": active,
                "memory_enabled": mem is not None,
                "ffmpeg": bool(ffmpeg_path())}

    return web.json_response(await asyncio.to_thread(_build))


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


def add_routes(app: web.Application) -> None:
    """Called by routes.build_mount — same door, same middleware."""
    app.router.add_get("/admin/roster", _page)
    app.router.add_get("/admin/roster/state", _state)
    app.router.add_post("/admin/roster/onboard", _onboard_route)
