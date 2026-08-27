"""tag_profiles.py — per-tag synth-knob profiles (the paralinguistic tag envelope).

A style tag names an affect DIRECTION; sampling temperature scales its DEPTH
(ear-verified). This module maps canonical tags present in an
outgoing synthesis chunk to knob DELTAS overlaid on the live synth knobs for
THAT chunk only — the envelope is the generate call containing the tag, and
baseline resumes at the next chunk by construction (no runtime mood state; the
temporal arc is the model re-emitting the tag, a template concern).

Deltas-only by design: a tag with no entry (e.g. [dramatic] — ear-verified
that baseline IS its peak) simply rides the live
knobs untouched.

Precedence at the consumers:
  desk pipeline (mlx_tts_service):  profile ⊗ live synth dict, per utterance.
  serve facade (tts_prep/app):      pin > profile > live — the identity pin's
    knob sub-table wins UNLESS it opts in via `allow_tag_profiles = true`
    (the pin exists to stop knob drift; a tag bump is desired drift only if
    the pin owner says so).

Config home: config/tts/<engine>/tts.toml [tag_profiles.<tag>] — a TOP-LEVEL
table, deliberately outside [live] so the [live]==generate()-defaults no-op
guarantee (test_config_reload BASE) is untouched. Tag names are the canonical
token WITHOUT brackets ("crying", not "[crying]").

Safety rails (load-time, fail-soft): knobs filtered to the engine's honored
live set; values must be numeric; temperature capped at TEMP_CEILING (1.4 —
the highest ear-verified-clean value; long-generation repetition risk grows
past the verified range). A rejected key/tag logs and drops — never raises.

Pure stdlib (+ paralinguistics, itself pure): importable by both
mlx_tts_service and the serve sidecar (which must not drag pipecat in — the
same posture as tts_prep re-reading the overrides contract).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from loguru import logger

from hearth.config import config_loader
from hearth.tts import paralinguistics

TTS_DIR = config_loader.ROOT_CONFIG_DIR / "tts"  # shipped baselines; a data-root copy wins (baseline_path)

# Engine-honored live knobs — duplicated small contract (config_reload owns the
# authoritative copy but imports pipecat; keep in sync with _ENGINE_LIVE_KEYS).
_ALLOWED_KNOBS: dict[str, frozenset[str]] = {
    "chatterbox-turbo": frozenset({"temperature", "top_p", "top_k", "repetition_penalty"}),
    "chatterbox": frozenset(
        {"temperature", "top_p", "top_k", "repetition_penalty", "exaggeration", "cfg_weight"}
    ),
}

TEMP_CEILING = 1.4  # highest ear-verified-clean temperature

# Canonical tag names (bracketless), derived from the one source of truth.
_CANONICAL_NAMES = frozenset(t.strip("[]") for t in paralinguistics._CANONICAL)

# Matches any canonical tag as it appears post-normalize: [name].
_TAG_RE = re.compile(r"\[([a-z ]+)\]")


def load_profiles(engine: str) -> dict[str, dict]:
    """config/tts/<engine>/tts.toml [tag_profiles] → {tag: {knob: value}}.

    Fail-soft: missing file/table ⇒ {}. Per-entry validation drops (with one
    log line each) unknown tags, non-table entries, unhonored/non-numeric
    knobs, and clamps temperature to TEMP_CEILING.
    """
    path = config_loader.baseline_path(f"tts/{engine}/tts.toml")
    allowed = _ALLOWED_KNOBS.get(engine, frozenset())
    try:
        if not path.exists():
            return {}
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:  # noqa: BLE001 — a bad file never breaks a turn
        logger.warning("tag_profiles: bad {} ({}) — no profiles", path, type(exc).__name__)
        return {}

    out: dict[str, dict] = {}
    for tag, knobs in (data.get("tag_profiles", {}) or {}).items():
        if tag not in _CANONICAL_NAMES:
            logger.warning("tag_profiles: [tag_profiles.{}] is not a canonical tag — ignoring", tag)
            continue
        if not isinstance(knobs, dict):
            logger.warning("tag_profiles: [tag_profiles.{}] must be a table — ignoring", tag)
            continue
        clean: dict = {}
        for k, v in knobs.items():
            if k not in allowed:
                logger.warning("tag_profiles: {}.{} not a live knob for {} — ignoring", tag, k, engine)
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                logger.warning("tag_profiles: {}.{} must be a number — ignoring", tag, k)
                continue
            if k == "temperature" and v > TEMP_CEILING:
                logger.warning(
                    "tag_profiles: {}.temperature {} exceeds ceiling {} — clamping", tag, v, TEMP_CEILING
                )
                v = TEMP_CEILING
            clean[k] = v
        if clean:
            out[tag] = clean
    return out


def deltas_for(text: str, profiles: dict[str, dict]) -> dict:
    """Knob deltas for the canonical tags present in one synthesis chunk.

    Text is expected POST-normalize (canonical [tag] surface forms). Multiple
    profiled tags in one chunk merge in order of appearance, last-wins per key
    (rare by the one-register-at-a-time template rule; documented, not policed).
    Untagged text ⇒ {} — the common case, and it must stay O(scan) cheap.
    """
    if not profiles or "[" not in text:
        return {}
    deltas: dict = {}
    for m in _TAG_RE.finditer(text):
        prof = profiles.get(m.group(1))
        if prof:
            deltas.update(prof)
    return deltas
