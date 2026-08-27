"""serve/tts_prep.py — speech/chat parity prep (TTS parity pass).

Closes the polish gap between the desk pipeline and the facade path:

  1. **Paralinguistic repair/strip** — the SAME paralinguistics.normalize_with_report
     pass mlx_tts_service runs, so `{cue}`-drift and stage directions
     never reach Chatterbox raw (they get spoken aloud otherwise).
  2. **Live knob forwarding** — desired = baseline ⊗ overrides (config_reload's
     revert-capable overlay rule), re-read per request because overrides.toml is
     panel-written between turns. Filtered to the knobs the :8555 SpeechRequest
     accepts. config_reload itself is NOT imported — it drags pipecat into the
     sidecar; the two file contracts are small and re-read here, fail-soft
     (a bad file never breaks a turn, matching the live layer's posture).
"""

from __future__ import annotations

import json
import re
import struct
import tomllib
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from loguru import logger

from hearth.config import config_loader
from hearth.tts import paralinguistics
from hearth.tts import tag_profiles

OVERRIDES_TOML = config_loader.CONFIG_DIR / "overrides.toml"
TTS_DIR = config_loader.CONFIG_DIR / "tts"
TTS_ENGINE = "chatterbox-turbo"  # bot.py's hardcoded engine this pass

# What the mlx-audio SpeechRequest accepts AND the engine honors live — the
# facade forwards only this intersection ([inert] keys can never leak through).
_SPEECH_KNOBS = frozenset({"temperature", "top_p", "top_k", "repetition_penalty", "speed"})

_last_logged: dict = {"knobs": None, "llm_temp": object()}

# Desk-parity strip log: SAME file and JSONL shape as mlx_tts_service._log_strips,
# so one review surface covers both pipelines. Resolved relative to the repo root.
_STRIP_LOG = config_loader.CONFIG_DIR.parent / "logs" / "paralinguistic-strips.jsonl"


def _log_strips(strips: list[dict], context_id: str) -> None:
    """UNKNOWN strips are the signal (novel tag worth a mapping); known families
    stay out of the console. Best-effort — a logging fault never breaks speech."""
    for s in strips:
        if not s["known"]:
            logger.info("[serve] stripped UNKNOWN paralinguistic tag {!r} "
                        "(context_id={}) — novel tag, consider a mapping",
                        s["token"], context_id)
    try:
        _STRIP_LOG.parent.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        with _STRIP_LOG.open("a") as fh:
            for s in strips:
                fh.write(json.dumps({
                    "ts": stamp,
                    "token": s["token"],
                    "known": s["known"],
                    "context_id": context_id,
                }) + "\n")
    except Exception as exc:  # noqa: BLE001 — mirror desk's swallow-to-debug
        logger.debug("[serve] strip-log write failed: {!r}", exc)

# Sentence-sized synthesis is the engine's stable regime: single-shot long
# inputs intermittently end early (Chatterbox early-EOS — a 98-word render lost
# its final ~3 s), which the live path never sees because it feeds sentence
# chunks. The facade's non-stream (voice-note) path chunks the
# same way and stitches one WAV.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
CHUNK_CHAR_BUDGET = 220


def sentence_chunks(text: str, budget: int = CHUNK_CHAR_BUDGET) -> list[str]:
    """Pack whole sentences into chunks of at most `budget` chars. A single
    over-budget sentence stays whole — never split mid-sentence."""
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in parts:
        if cur and len(cur) + 1 + len(p) > budget:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks or [text]


def concat_wavs(blobs: list[bytes]) -> bytes:
    """Stitch same-format PCM WAVs into one; header sizes recomputed. The
    upstream engine emits one complete WAV per chunk request with identical fmt
    (same engine + knobs), so the first blob's pre-data header is reused."""
    if len(blobs) == 1:
        return blobs[0]

    def data_span(b: bytes) -> tuple[int, int]:
        if len(b) < 12 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
            raise ValueError("not a RIFF/WAVE blob")
        off = 12
        while off + 8 <= len(b):
            cid = b[off:off + 4]
            csz = struct.unpack("<I", b[off + 4:off + 8])[0]
            if cid == b"data":
                return off + 8, min(csz, len(b) - off - 8)
            off += 8 + csz + (csz & 1)
        raise ValueError("no data chunk")

    first_start, first_len = data_span(blobs[0])
    head = bytearray(blobs[0][:first_start])
    pcm = bytearray(blobs[0][first_start:first_start + first_len])
    for b in blobs[1:]:
        s, ln = data_span(b)
        pcm.extend(b[s:s + ln])
    struct.pack_into("<I", head, 4, len(head) + len(pcm) - 8)
    struct.pack_into("<I", head, first_start - 4, len(pcm))
    return bytes(head) + bytes(pcm)


# A valid, zero-sample 24 kHz mono 16-bit WAV — the "silence is the faithful
# rendering" response for word-less fragments (see build_speech_payload).
SILENT_WAV = (
    b"RIFF" + struct.pack("<I", 36) + b"WAVE"
    b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
    + b"data" + struct.pack("<I", 0)
)


def _read_toml_soft(path) -> dict:
    try:
        if not path.exists():
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as exc:  # noqa: BLE001 — fail-soft like the live layer
        logger.warning("[serve] bad live-config file {} ({}) — ignored", path, type(exc).__name__)
        return {}


def live_speech_knobs() -> dict:
    """baseline [live] ⊗ overrides [tts], filtered to _SPEECH_KNOBS. Logged on change."""
    knobs = dict(_read_toml_soft(TTS_DIR / TTS_ENGINE / "tts.toml").get("live", {}) or {})
    knobs.update(_read_toml_soft(OVERRIDES_TOML).get("tts", {}) or {})
    knobs = {k: v for k, v in knobs.items() if k in _SPEECH_KNOBS}
    if knobs != _last_logged["knobs"]:
        _last_logged["knobs"] = knobs
        logger.info("[serve] speech knobs now: {}", knobs)
    return knobs


def live_llm_temperature(default: float) -> float:
    """overrides.toml [llm].temperature (panel-written) wins over the model.toml
    value, matching desk-Hearth's turn-boundary reload. Logged on change."""
    raw = _read_toml_soft(OVERRIDES_TOML).get("llm", {}).get("temperature")
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if value != _last_logged["llm_temp"]:
        _last_logged["llm_temp"] = value
        logger.info("[serve] chat temperature now: {} ({})", value,
                    "live override" if raw is not None else "model.toml")
    return value


def with_tag_profile(payload: dict, deps) -> dict:
    """Overlay the paralinguistic tag envelope onto ONE upstream synthesis call.

    Scans this call's `input` (post-normalize canonical tags) for profiled
    style tags and merges their calibrated knob deltas. Precedence: pin >
    profile > live — the deltas land OVER the live layer but the identity
    pin's knobs are re-asserted afterward UNLESS the pin opted in
    (`[serve.identity.tts] allow_tag_profiles = true`). Untagged call ⇒ the
    payload is returned unchanged (same object; the common case stays free).
    Fail-soft throughout: profiles are an overlay, never a failure mode.
    """
    try:
        deltas = tag_profiles.deltas_for(
            str(payload.get("input", "")), tag_profiles.load_profiles(TTS_ENGINE))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[serve] tag-profile overlay failed ({}) — live knobs only",
                       type(exc).__name__)
        return payload
    if not deltas:
        return payload
    merged = {**payload, **deltas}
    if deps.pinned_tts and not deps.allow_tag_profiles:
        merged.update(deps.pinned_tts)  # pin stays absolute without opt-in
        if all(merged.get(k) == payload.get(k) for k in deltas):
            return payload  # pin swallowed every delta — nothing actually changed
    logger.info("[serve] tag envelope → {} (this call only)", deltas)
    return merged


def build_speech_payload(deps, body: dict) -> tuple[Optional[dict], str]:
    """Turn a client /v1/audio/speech body into the pinned upstream payload.

    Voice identity (model + ref_audio) is pinned server-side from the active
    bundle; the client picks only text, format, and speed. Returns
    (payload, "") or (None, error-message).
    """
    text = str(body.get("input", "")).strip()
    if not text:
        return None, "empty input"
    # Desk-parity prosody-artifact fixes, SAME rules and order as
    # mlx_tts_service.run_tts (source-verified rationale lives there):
    # whitespace runs → one space (newlines fracture prosody); 2+ dots/ellipses
    # → single "…" (orphan fragments + trailing improvisation — the single "…"
    # is the confirmed-clean baseline); then cue repair/strip; then skip
    # word-less fragments (the TTS improvises filler on them) — (None, "") =
    # answer with SILENT_WAV, not an error.
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[.…]{2,}", "…", text)
    text, strips = paralinguistics.normalize_with_report(text)
    if strips:
        logger.info("[serve] speech: {} paralinguistic repair(s)/strip(s)", len(strips))
        _log_strips(strips, f"serve:{uuid4().hex[:8]}")
    if not any(ch.isalnum() for ch in text):
        logger.info("[serve] speech: skipping word-less fragment — silent reply")
        return None, ""
    payload = {
        "model": deps.tts_model,
        "input": text,
        "response_format": str(body.get("response_format") or "wav"),
        "ref_audio": deps.ref_wav,
    }
    if "speed" in body:
        payload["speed"] = body["speed"]
    if body.get("stream"):
        # Chunked synthesis: the server emits one complete WAV envelope per
        # ~streaming_interval seconds of audio, concatenated on the wire.
        payload["stream"] = True
        interval = body.get("streaming_interval")
        if isinstance(interval, (int, float)) and interval > 0:
            payload["streaming_interval"] = float(interval)
    else:
        # Voice-note path always returns WAV regardless of the client's ask:
        # mlx-audio's mp3 encoder answers 200 with an EMPTY body,
        # so forwarding "mp3" would relay nothing. Chat clients (Open WebUI)
        # transcode for themselves.
        payload["response_format"] = "wav"
    payload.update(live_speech_knobs())
    # [serve.identity.tts] pin: applied last, so pinned keys win over both the
    # client body and the shared live layer (keys pre-validated at start()).
    payload.update(deps.pinned_tts)
    return payload, ""
