"""test_turn_timer.py — the in-ear latency chain, marked per spoken turn.

A bench found time-to-first-audio living in the chain around the model
(end-pointing, STT, first TTS chunk), not in the model. TurnTimer marks that
chain per turn from real pipecat frames (test_token_meter.py's pattern: drive
on_push_frame directly, no fake pipeline), with time.monotonic patched to a
controllable clock so the marks are exact.

  (a) one full turn         — all four marks + speaking_s land at the right offsets
  (b) a re-pushed frame     — same frame.id counts once (dedupe, like TokenMeter)
  (c) a typed turn          — no UserStoppedSpeakingFrame -> untimed, not timed
  (d) two turns             — median/p95/max computed over both
  (e) snapshot() is numeric — no str anywhere, json.dumps succeeds
  (f) the 500-turn cap      — oldest dropped, total count kept

Run: ./.venv/bin/python tests/test_turn_timer.py
"""

import asyncio
import json
import statistics
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
)

from hearth.session.turn_timer import TurnTimer  # noqa: E402


class FakeClock:
    """A controllable stand-in for time.monotonic()."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _push(timer: TurnTimer, frame) -> None:
    asyncio.run(timer.on_push_frame(SimpleNamespace(frame=frame)))


def _full_turn(timer: TurnTimer, clock: FakeClock, *, stt=0.5, token=1.2, tts=1.5,
               audio=2.0, stop=5.0) -> None:
    """One complete spoken turn, marks landing at the given clock offsets."""
    base = clock.now
    _push(timer, UserStoppedSpeakingFrame())
    clock.now = base + stt
    _push(timer, TranscriptionFrame(text="hi", user_id="u1", timestamp="t1"))
    clock.now = base + token - 0.1
    _push(timer, LLMFullResponseStartFrame())
    clock.now = base + token
    _push(timer, LLMTextFrame(text="hello"))
    clock.now = base + tts
    _push(timer, TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1))
    clock.now = base + audio
    _push(timer, BotStartedSpeakingFrame())
    clock.now = base + stop
    _push(timer, BotStoppedSpeakingFrame())


class TestOneTurn(unittest.TestCase):
    def test_all_four_marks_and_speaking_s(self):
        clock = FakeClock()
        with patch("hearth.session.turn_timer.time.monotonic", clock):
            timer = TurnTimer()
            _full_turn(timer, clock, stt=0.5, token=1.2, tts=1.5, audio=2.0, stop=5.0)

        snap = timer.snapshot()
        self.assertEqual(snap["turns"], 1)
        self.assertEqual(snap["untimed"], 0)
        turn = snap["per_turn"][0]
        self.assertEqual(turn, [0.5, 1.2, 2.0, 3.0])  # stt, first_token, first_audio, speaking


class TestDedupe(unittest.TestCase):
    def test_a_repushed_frame_counts_once(self):
        clock = FakeClock()
        with patch("hearth.session.turn_timer.time.monotonic", clock):
            timer = TurnTimer()
            opened = UserStoppedSpeakingFrame()
            _push(timer, opened)
            clock.now = 0.5
            transcript = TranscriptionFrame(text="hi", user_id="u1", timestamp="t1")
            _push(timer, transcript)
            clock.now = 0.9
            _push(timer, transcript)  # same frame.id — must not move the mark
            clock.now = 2.0
            _push(timer, BotStartedSpeakingFrame())
            clock.now = 3.0
            _push(timer, BotStoppedSpeakingFrame())
            clock.now = 3.1
            _push(timer, opened)  # re-pushed open marker — must not open a second turn
            clock.now = 3.5
            # A fresh close after the re-pushed opener: with dedupe nothing is
            # open, so nothing closes and nothing is counted. Without dedupe the
            # re-pushed opener starts a phantom turn and this close lands it as
            # a second turn — which is exactly what the assertion below catches.
            _push(timer, BotStoppedSpeakingFrame())

        snap = timer.snapshot()
        self.assertEqual(snap["turns"], 1)
        self.assertEqual(snap["untimed"], 0)
        self.assertEqual(snap["per_turn"][0][0], 0.5)


class TestTypedTurn(unittest.TestCase):
    def test_a_typed_response_is_untimed_not_timed(self):
        clock = FakeClock()
        with patch("hearth.session.turn_timer.time.monotonic", clock):
            timer = TurnTimer()
            _push(timer, LLMFullResponseStartFrame())
            _push(timer, LLMTextFrame(text="hello"))
            _push(timer, TTSAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1))
            _push(timer, BotStartedSpeakingFrame())
            _push(timer, BotStoppedSpeakingFrame())

        snap = timer.snapshot()
        self.assertEqual(snap["untimed"], 1)
        self.assertEqual(snap["turns"], 0)


class TestTwoTurns(unittest.TestCase):
    def test_medians_p95_max(self):
        clock = FakeClock()
        with patch("hearth.session.turn_timer.time.monotonic", clock):
            timer = TurnTimer()
            _full_turn(timer, clock, audio=2.0)
            clock.now = 10.0
            _full_turn(timer, clock, audio=4.0)

        snap = timer.snapshot()
        self.assertEqual(snap["turns"], 2)
        values = [2.0, 4.0]
        expected = {
            "median": round(statistics.median(values), 3),
            "p95": round(statistics.quantiles(values, n=20)[-1], 3),
            "max": round(max(values), 3),
        }
        self.assertEqual(snap["first_audio_s"], expected)


class TestSnapshotIsNumeric(unittest.TestCase):
    def test_no_strings_anywhere_and_json_serializable(self):
        clock = FakeClock()
        with patch("hearth.session.turn_timer.time.monotonic", clock):
            timer = TurnTimer()
            _full_turn(timer, clock)
            clock.now = 10.0
            _push(timer, LLMFullResponseStartFrame())  # a typed turn too

        snap = timer.snapshot()

        def walk(node):
            if isinstance(node, str):
                self.fail(f"found a str in snapshot(): {node!r}")
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(snap)
        json.dumps(snap)  # must not raise


class TestCap(unittest.TestCase):
    def test_the_500_cap_drops_the_oldest_but_keeps_the_total(self):
        clock = FakeClock()
        with patch("hearth.session.turn_timer.time.monotonic", clock):
            timer = TurnTimer()
            for i in range(501):
                clock.now = i * 10.0
                _full_turn(timer, clock, audio=float(i))

        snap = timer.snapshot()
        self.assertEqual(snap["turns"], 501)
        self.assertEqual(len(snap["per_turn"]), 500)
        # the oldest turn (first_audio_s == 0.0) was dropped
        first_audio_values = [t[2] for t in snap["per_turn"]]
        self.assertNotIn(0.0, first_audio_values)
        self.assertIn(500.0, first_audio_values)


if __name__ == "__main__":
    unittest.main(verbosity=2)
