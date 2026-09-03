#!/usr/bin/env python3
"""test_mlx_tts.py — standalone unit test for MLXAudioTTSService.

Runs WITHOUT mic, LLM, or pipeline.  Exercises run_tts() directly inside an
asyncio event loop, checks the frames, writes a WAV for ear-testing, and
prints timing metrics.  Runs the synthesis TWICE in-process so both warm runs
are visible (MLX Metal kernels are JIT-compiled on the very first synthesise call
on this machine; subsequent calls use the disk-cached kernels).

Usage:
    uv run python test_mlx_tts.py

Expected pass criteria (checked via assertions):
  (a) > 1 TTSAudioRawFrame streamed per call
  (b) each frame: sample_rate == 24000, num_channels == 1
  (c) total audio duration > 0.5 s

Output WAV written to: step1_unit_out.wav  (24 kHz / mono / int16)
"""

import asyncio
import struct
import time
import wave
from pathlib import Path

import unittest

from pipecat.frames.frames import TTSAudioRawFrame

# Import after environment is active (transformers==5.5.0 must be on path).
# mlx is an optional, platform-specific extra: a venv without it must SKIP this
# module, not error out of discovery. An unimportable test file that reports as
# a failure is noise in the baseline, and noise is where real regressions hide.
try:
    from hearth.tts.mlx_tts_service import MLXAudioTTSService, SAMPLE_RATE
except ImportError as exc:
    raise unittest.SkipTest(f"mlx runtime not installed ({exc})") from exc

TEST_TEXT = (
    "Hi, this is a step one unit test of the in process streaming T T S."
)
WAV_OUT = Path(__file__).parent / "step1_unit_out.wav"
CONTEXT_ID = "test-ctx"


def pcm_duration_s(pcm_frames: list[bytes], sample_rate: int = SAMPLE_RATE) -> float:
    """Sum of frame byte lengths → seconds of int16 mono audio."""
    total_bytes = sum(len(f) for f in pcm_frames)
    # 2 bytes per int16 sample, 1 channel
    return total_bytes / (sample_rate * 2)


async def run_once(svc: MLXAudioTTSService, run_label: str) -> tuple[list[TTSAudioRawFrame], float, float]:
    """Drive run_tts once and return (frames, ttfa_s, total_wall_s)."""
    frames: list[TTSAudioRawFrame] = []
    ttfa: float | None = None
    t_start = time.perf_counter()

    async for frame in svc.run_tts(TEST_TEXT, CONTEXT_ID):
        if frame is None:
            continue
        now = time.perf_counter() - t_start
        if ttfa is None:
            ttfa = now
        assert isinstance(frame, TTSAudioRawFrame), f"Unexpected frame type: {type(frame)}"
        frames.append(frame)

    wall = time.perf_counter() - t_start
    ttfa = ttfa or wall  # guard for zero-frame edge case

    dur = pcm_duration_s([f.audio for f in frames])
    rtf = wall / dur if dur > 0 else 0.0

    print(
        f"[{run_label}] frames={len(frames)}  TTFA={ttfa:.2f}s  "
        f"wall={wall:.2f}s  audio_dur={dur:.2f}s  RTF={rtf:.2f}"
    )
    return frames, ttfa, wall


def write_wav(frames: list[TTSAudioRawFrame], path: Path) -> None:
    """Concatenate frames and write a 24 kHz / mono / int16 WAV."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for f in frames:
            w.writeframes(f.audio)
    print(f"[wav] written → {path}")


async def main() -> None:
    # ── Load service (measured separately from synthesis) ──────────────────
    print(f"[test] test text: {TEST_TEXT!r}")
    t_load = time.perf_counter()
    svc = MLXAudioTTSService()
    load_time = time.perf_counter() - t_load
    print(f"[test] service loaded in {load_time:.2f}s")

    # ── Run 1 ───────────────────────────────────────────────────────────────
    frames1, ttfa1, wall1 = await run_once(svc, "run1")

    # ── Run 2 (second in-process synth, kernels already compiled/cached) ───
    frames2, ttfa2, wall2 = await run_once(svc, "run2")

    # ── Assertions ──────────────────────────────────────────────────────────
    print("\n[assertions] checking run1 …")

    # (a) More than 1 frame streamed
    assert len(frames1) > 1, (
        f"FAIL (a): expected >1 frame, got {len(frames1)}"
    )
    print(f"  (a) PASS: {len(frames1)} frames > 1")

    # (b) Each frame has correct sample_rate and num_channels
    for i, f in enumerate(frames1):
        assert f.sample_rate == 24000, (
            f"FAIL (b): frame[{i}].sample_rate={f.sample_rate} != 24000"
        )
        assert f.num_channels == 1, (
            f"FAIL (b): frame[{i}].num_channels={f.num_channels} != 1"
        )
    print(f"  (b) PASS: all {len(frames1)} frames have sample_rate=24000, num_channels=1")

    # (c) Total audio > 0.5 s
    dur1 = pcm_duration_s([f.audio for f in frames1])
    assert dur1 > 0.5, f"FAIL (c): total audio {dur1:.2f}s is not > 0.5s"
    print(f"  (c) PASS: total audio {dur1:.2f}s > 0.5s")

    # ── Write WAV from run2 (kernel-cached warm run) ─────────────────────────
    write_wav(frames2, WAV_OUT)

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n===== STEP 1 UNIT TEST SUMMARY =====")
    print(f"  model load time      : {load_time:.2f}s")
    print(f"  run1 TTFA            : {ttfa1:.2f}s")
    print(f"  run1 total wall      : {wall1:.2f}s")
    print(f"  run1 audio duration  : {dur1:.2f}s")
    print(f"  run1 RTF             : {wall1/dur1:.2f}")
    dur2 = pcm_duration_s([f.audio for f in frames2])
    print(f"  run2 TTFA            : {ttfa2:.2f}s")
    print(f"  run2 total wall      : {wall2:.2f}s")
    print(f"  run2 audio duration  : {dur2:.2f}s")
    print(f"  run2 RTF             : {wall2/dur2:.2f}")
    print(f"  output WAV           : {WAV_OUT}")
    print("  assertions           : ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
