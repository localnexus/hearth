"""serve/stt_prep.py — normalize voice-note INPUT audio for the local Whisper.

mlx-audio's /audio/transcriptions accepts clean WAV only (webm/opus and mp3
both answer 500) — so the facade re-encodes whatever the
client recorded to 16 kHz mono PCM WAV before relaying. File-based ffmpeg
(not pipes: the WAV muxer needs a seekable output to finalize RIFF sizes,
and an unfinalized header is exactly the kind of input mlx-audio chokes on).
ffmpeg resolved from PATH with a Homebrew fallback — launchd agents run with
a bare PATH.

The silence gate: whisper-large-v3 hallucinates
gratitude on room tone ("Thank you." — once even a Spanish "Gracias", caught
in the field via Open WebUI Call mode, whose client VAD arms on background
variation). Clips below the energy/duration floor never reach Whisper; the
route answers an empty transcription instead.
"""

from __future__ import annotations

import array
import asyncio
import io
import math
import os
import shutil
import tempfile
import wave

from loguru import logger

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

# Speech from the phone mic (AGC on) sits around -20..-35 dBFS RMS; room tone
# -55 and below. Constants, not serve.toml knobs — this filters a Whisper bug
# class, it isn't taste. Promote if field tuning ever demands it.
SILENCE_MIN_SECONDS = 0.30
SILENCE_MIN_DBFS = -45.0


class AudioDecodeError(ValueError):
    """The uploaded bytes could not be decoded as audio."""


async def to_clean_wav(data: bytes) -> bytes:
    """Re-encode arbitrary client audio (webm/opus, mp3, wav, …) to 16 kHz mono WAV."""
    if not data:
        raise AudioDecodeError("empty upload")
    with tempfile.TemporaryDirectory(prefix="hearth-stt-") as td:
        src = os.path.join(td, "in.bin")
        dst = os.path.join(td, "out.wav")
        with open(src, "wb") as fh:
            fh.write(data)
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-v", "error", "-y", "-i", src,
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", dst,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(dst):
            detail = (err or b"no output produced").decode(errors="replace").strip()
            raise AudioDecodeError(detail[:200])
        with open(dst, "rb") as fh:
            return fh.read()


def measure(wav_bytes: bytes) -> tuple[float, float]:
    """(seconds, rms_dbfs) of a to_clean_wav() product (16-bit PCM)."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        frames = w.getnframes()
        rate = w.getframerate() or 16000
        raw = w.readframes(frames)
    samples = array.array("h")
    samples.frombytes(raw[: 2 * (len(raw) // 2)])
    if not samples:
        return 0.0, -120.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    dbfs = 20 * math.log10(rms / 32768.0) if rms > 0 else -120.0
    return frames / float(rate), dbfs


def is_silence(wav_bytes: bytes) -> bool:
    seconds, dbfs = measure(wav_bytes)
    if seconds < SILENCE_MIN_SECONDS or dbfs < SILENCE_MIN_DBFS:
        logger.info("[serve] STT silence gate: {:.2f}s at {:.1f} dBFS -> empty transcription",
                    seconds, dbfs)
        return True
    return False
