"""test_level_tap.py — the shared per-frame RMS envelope (desk-figure mouth, S2; panel waveform).

Proves:
  1. the math — int16 PCM → 0–1 level, silence 0, full-scale square 1 (clamped),
     a −20 dB tone lands near 0.4 with the ×4 gain;
  2. the contract — silence is 0.0 on its own after one chunk gap (never held),
     bot-stopped and session boundaries zero it at once, level_ts marks the last
     non-zero frame;
  3. passivity on the real pipecat machinery — every frame comes out the same object;
  4. placement — the tap counts OutputAudioRawFrame (played voice) by default and
     ignores mic frames, and vice versa when built for the mic.

Run:  .venv/bin/python -m unittest tests.test_level_tap
"""

from __future__ import annotations

import asyncio
import math
import struct
import unittest

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.tests.utils import run_test

from hearth.control.control_taps import LevelTap


def _tone(amp: float, n: int = 960) -> bytes:
    return struct.pack(f"<{n}h", *(int(amp * 32767 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(n)))


def _out(pcm: bytes) -> OutputAudioRawFrame:
    return OutputAudioRawFrame(audio=pcm, sample_rate=24000, num_channels=1)


class LevelMathTests(unittest.TestCase):

    def test_silence_is_zero(self):
        self.assertEqual(LevelTap.rms_level(b"\0\0" * 960), 0.0)
        self.assertEqual(LevelTap.rms_level(b""), 0.0)
        self.assertEqual(LevelTap.rms_level(b"\x01"), 0.0)  # odd byte, no sample

    def test_full_scale_square_clamps_to_one(self):
        self.assertEqual(LevelTap.rms_level(struct.pack("<4h", 32767, -32767, 32767, -32767)), 1.0)

    def test_minus_20db_sine_near_0_4(self):
        # rms of a 0.1 amplitude sine = 0.0707 → ×4 = 0.283; a 0.15 sine → 0.424
        self.assertAlmostEqual(LevelTap.rms_level(_tone(0.1)), 0.283, places=2)
        self.assertAlmostEqual(LevelTap.rms_level(_tone(0.15)), 0.424, places=2)


class LevelContractTests(unittest.TestCase):

    def test_follows_frames_then_decays_to_zero_on_its_own(self):
        tap = LevelTap()
        tap.observe(_out(_tone(0.15)), now=10.00)
        self.assertAlmostEqual(tap.level(now=10.02), 0.424, places=2)   # within the hold
        self.assertEqual(tap.level(now=10.00 + LevelTap.HOLD_S + 0.01), 0.0)  # one gap → closed, no frame needed
        self.assertEqual(tap.level_ts, 10.00)

    def test_level_ts_marks_the_last_nonzero_frame_only(self):
        tap = LevelTap()
        tap.observe(_out(_tone(0.15)), now=5.0)
        tap.observe(_out(b"\0\0" * 960), now=5.04)   # a silent chunk: seen, but not "last non-zero"
        self.assertEqual(tap.level(now=5.05), 0.0)
        self.assertEqual(tap.level_ts, 5.0)

    def test_bot_stopped_and_session_end_zero_it_at_once(self):
        tap = LevelTap()
        tap.observe(_out(_tone(0.15)), now=1.0)
        tap.observe(BotStoppedSpeakingFrame(), now=1.01)
        self.assertEqual(tap.level(now=1.01), 0.0)
        tap.observe(_out(_tone(0.15)), now=2.0)
        tap.observe(EndFrame(), now=2.01)
        self.assertEqual(tap.level(now=2.01), 0.0)

    def test_placement_decides_which_frames_count(self):
        out_tap = LevelTap()
        out_tap.observe(InputAudioRawFrame(audio=_tone(0.15), sample_rate=16000, num_channels=1), now=1.0)
        self.assertEqual(out_tap.level(now=1.0), 0.0)          # mic frames ignored on the output placement
        mic_tap = LevelTap(frame_type=InputAudioRawFrame)
        mic_tap.observe(_out(_tone(0.15)), now=1.0)
        self.assertEqual(mic_tap.level(now=1.0), 0.0)          # and the reverse
        mic_tap.observe(InputAudioRawFrame(audio=_tone(0.15), sample_rate=16000, num_channels=1), now=1.0)
        self.assertGreater(mic_tap.level(now=1.0), 0.4)


class LevelPassthroughTests(unittest.TestCase):

    def test_every_frame_passes_through_untouched(self):
        tap = LevelTap()
        a = _out(_tone(0.15)); b = BotStoppedSpeakingFrame()
        down, _up = asyncio.run(run_test(tap, frames_to_send=[a, b]))
        self.assertTrue(any(f is a for f in down))
        self.assertTrue(any(f is b for f in down))


if __name__ == "__main__":
    unittest.main()
