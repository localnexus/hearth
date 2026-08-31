"""Backend-neutral TTS engine parameters.

SAMPLE_RATE is the pipeline's output audio rate. It is ENGINE-owned: every
consumer (transport params, the recorder, PipelineWorker) must follow the TTS
engine actually constructed, and engines disagree (Chatterbox/Kokoro 24 kHz,
Piper 22.05 kHz). Today the only in-process engine is Chatterbox-Turbo via
MLX, so this module owns the constant and the MLX service re-exports it; when
a second backend lands (hearth[cuda]), this becomes a per-backend resolution,
not a constant.

Owned here (zero-dependency module) rather than in mlx_tts_service so that
importing the rate does NOT pull `mlx.core` — the base install (no extras)
must stay importable on any host.
"""

SAMPLE_RATE: int = 24000
"""Chatterbox-Turbo always outputs 24 kHz mono audio."""
