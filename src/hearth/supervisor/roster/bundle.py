"""roster/bundle.py — conditioning an uploaded clip, and the two files written
beside it.

Both write verbs come through here — onboarding a new character and adding a
voice to an existing one — so a bundle is the same shape whichever door wrote
it. ffmpeg is optional, not required: with it any container transcodes to the
manual's reference format (fixed argv, no shell, bounded); without it a
readable WAV is copied as-is with its format reported as an advisory, and
anything else is refused honestly.

The two templates take the SAME license/source answers: voice.toml carries
them where the loader reads, VOICE-SOURCE.md where a person does. One answer,
two enforceable places — the restriction rides the clip.

One part of the /admin/roster arc; the package __init__ carries the map of the
whole and re-exports every name defined here.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

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

_VOICE_SOURCE_ADD = """\

## {tag} — added {date}

| Field | Value |
|---|---|
| License | {license} |
| Source | {source} |
| Processing | {processing} |
| Duration | {duration:.2f} s |
"""


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
