"""
stt_service.py — MLX-Whisper STT service (Apple-Silicon local transcription).

Extracted from bot.py so the pipeline core (bot.py) stays readable. This module
owns exactly one pipeline stage — the custom SegmentedSTTService backed by
MLX-Whisper — plus the latency marker it uses to timestamp the STT/TTS stage
boundaries.

The peer TTS stage lives in mlx_tts_service.py; this is its STT counterpart.

Public surface (imported by bot.py):
    MLXWhisperSTTService — the pipeline STT stage
    MLX_WHISPER_MODEL    — the model id (also the service's default `model` arg)
"""

from __future__ import annotations

import asyncio
import io
import os
import sys as _sys
import time
import wave
from typing import AsyncGenerator

from pipecat.services.stt_service import SegmentedSTTService
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
)

MLX_WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"


# ── T4 latency instrumentation (opt-in; inert unless T4_METRICS=1) ─────────────
# Timestamps the stage boundaries we own (STT internals) on a shared monotonic
# clock so one clean turn can be decomposed into per-stage budgets. Gated behind
# an env var so normal runs are unaffected. bot.py's main() reads T4_METRICS from
# the same env var independently (for pipecat's enable_metrics + TokenMeter verbose)
# — each module reads config; no import coupling for the flag.
T4_METRICS = os.environ.get("T4_METRICS", "0") == "1"
_t4_last: dict[str, object] = {"t": None, "label": None}


def _t4_mark(label: str, reset: bool = False) -> None:
    """Print a monotonic-clock marker with the delta since the previous marker.

    reset=True clears the running delta baseline (use at the start of a turn so
    the first marker of a turn doesn't show a misleading gap from the prior turn).
    """
    if not T4_METRICS:
        return
    if reset:
        _t4_last["t"] = None
        _t4_last["label"] = None
    now = time.perf_counter()
    prev = _t4_last["t"]
    if prev is None:
        delta = ""
    else:
        delta = f"   (+{(now - float(prev)) * 1000:7.0f} ms  since {_t4_last['label']})"
    print(f"[T4] {now:12.3f}  {label:<34}{delta}", file=_sys.stderr, flush=True)
    _t4_last["t"] = now
    _t4_last["label"] = label


# ── Custom STT Service ─────────────────────────────────────────────────────────


class MLXWhisperSTTService(SegmentedSTTService):
    """
    STT service backed by MLX-Whisper (Apple Silicon optimised).

    Extends SegmentedSTTService: audio is buffered per VAD-detected speech
    segment and run_stt is called ONCE with a complete-utterance WAV on
    VADUserStoppedSpeaking — NOT per 20ms frame. A VADProcessor must sit
    upstream in the pipeline to emit the VAD frames this depends on.

    Keeps the model loaded in memory across calls (load once at __init__).
    Input to run_stt: a full WAV (16 kHz mono int16, with header).
    """

    def __init__(self, model: str = MLX_WHISPER_MODEL, **kwargs):
        super().__init__(sample_rate=16000, **kwargs)
        self._model = model
        # Pre-load model weights into memory now so first-call latency is warm.
        # This happens synchronously at construction (before the pipeline starts).
        self._mlx_whisper = None
        self._load_model()

    def _load_model(self):
        """Load the MLX-Whisper model (warm-up; ~20 s cold, ~0 s if already cached)."""
        import mlx_whisper  # noqa: inline import to isolate heavy import

        self._mlx_whisper = mlx_whisper
        # Trigger a small transcription to warm the MLX graph
        # (we pass an empty-ish buffer; result is discarded)
        try:
            import numpy as np

            dummy = np.zeros(1600, dtype=np.float32)  # 0.1 s of silence
            mlx_whisper.transcribe(
                dummy,
                path_or_hf_repo=self._model,
                word_timestamps=False,
            )
        except Exception:
            pass  # warm-up failure is non-fatal

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """
        Transcribe one complete VAD-segmented utterance.

        SegmentedSTTService hands us a full WAV (16 kHz mono int16, WITH header),
        not raw PCM. Decode it, then pass the float32 array to mlx_whisper.
        """
        if not audio:
            yield None
            return

        # Turn boundary: SegmentedSTTService calls run_stt right after VAD-stop,
        # so this marks ~"user stopped speaking". reset=True rebaselines the turn.
        _t4_mark("stt_start  (~VAD stop)", reset=True)
        t0 = time.perf_counter()

        import numpy as np

        # Decode the WAV segment → int16 PCM → float32 [-1, 1].
        try:
            with wave.open(io.BytesIO(audio), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
        except (wave.Error, EOFError):
            # Fallback: treat bytes as raw int16 PCM (defensive).
            raw = audio

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if len(samples) < 1600:  # < 0.1 s of speech — too short, skip
            yield None
            return

        # mlx_whisper.transcribe is a synchronous, GPU-bound call. Run it in a
        # worker thread so it does NOT block the asyncio event loop — otherwise
        # the whole pipeline (LLM, TTS) freezes for the duration of every
        # transcription, starving concurrent stages.
        result = await asyncio.to_thread(
            self._mlx_whisper.transcribe,
            samples,
            path_or_hf_repo=self._model,
            word_timestamps=False,
        )

        elapsed = time.perf_counter() - t0
        text = (result.get("text") or "").strip()

        if not text:
            yield None
            return

        # Guard against Whisper hallucinating text from noise/silence, which would
        # otherwise fire a phantom user "turn" (and barge-in cancel the reply).
        segments = result.get("segments") or []
        if segments:
            # If every segment reads as non-speech, drop it.
            probs = [s.get("no_speech_prob", 0.0) for s in segments]
            if probs and min(probs) > 0.6:
                yield None
                return
        # Common single-token hallucinations Whisper emits on silence.
        if text.lower().strip(" .!?,") in {
            "you", "thank you", "thanks for watching", "thanks", "bye", ".",
            "okay", "so", "the", "uh", "um",
        }:
            yield None
            return

        _t4_mark(f"stt_done  ({elapsed*1000:.0f}ms) '{text[:32]}'")

        yield TranscriptionFrame(
            text=text,
            user_id="user",
            timestamp=str(time.time()),
            language=None,
        )
