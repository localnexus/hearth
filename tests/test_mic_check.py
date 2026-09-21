"""test_mic_check.py — the closing microphone check, driven by a fake stream.

No microphone is opened anywhere in this file and no audio is played: the
`sounddevice` stand-in below hands back frames the test wrote itself. That is
deliberate — a test that needed a real grant would fail on every machine that
has the bug the check exists to find.

Covered: raw int16 frames → peak; peak → the sentence a person reads; the
whole check with a loud stream, a silent stream, and a stream that refuses to
open; and the promise that none of the three changes an exit code.
"""

from __future__ import annotations

import array
import io
import unittest
from pathlib import Path

from hearth.init import mic_check


def _frames(value: int, count: int) -> bytes:
    """`count` mono int16 samples, all at `value`."""
    return array.array("h", [value] * count).tobytes()


class FakeStream:
    """The shape `mic_check.record` uses: a context manager with `.read(n)`."""

    def __init__(self, level: int, opened: list):
        self.level = level
        self.opened = opened
        self.kwargs: dict = {}

    def __enter__(self):
        self.opened.append(self.kwargs)
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n):
        return _frames(self.level, n), False


class FakeSoundDevice:
    def __init__(self, level: int):
        self.level = level
        self.opened: list = []

    def RawInputStream(self, **kwargs):  # noqa: N802 — matches sounddevice's own name
        stream = FakeStream(self.level, self.opened)
        stream.kwargs = kwargs
        return stream


class DeafSoundDevice:
    """A machine where the input device cannot be opened at all."""

    def RawInputStream(self, **kwargs):  # noqa: N802
        raise OSError("no default input device")


class PeakTests(unittest.TestCase):
    def test_no_frames_is_zero(self):
        self.assertEqual(mic_check.peak([]), 0)
        self.assertEqual(mic_check.peak([b"", b""]), 0)

    def test_loudest_sample_wins_across_chunks(self):
        self.assertEqual(mic_check.peak([_frames(10, 4), _frames(900, 4), _frames(3, 4)]), 900)

    def test_negative_samples_count_by_size(self):
        self.assertEqual(mic_check.peak([_frames(-4000, 8)]), 4000)

    def test_the_int16_floor_does_not_overflow_the_scale(self):
        self.assertEqual(mic_check.peak([_frames(-32768, 2)]), 32767)

    def test_an_odd_trailing_byte_is_ignored_not_fatal(self):
        self.assertEqual(mic_check.peak([_frames(500, 3) + b"\x01"]), 500)


class SentenceTests(unittest.TestCase):
    def test_a_loud_moment_is_heard(self):
        mark, text = mic_check.sentence(12000)
        self.assertEqual(mark, mic_check.MARK_HEARD)
        self.assertIn("heard you", text)
        self.assertIn("37%", text)

    def test_silence_is_a_note_and_points_at_the_page(self):
        mark, text = mic_check.sentence(0)
        self.assertEqual(mark, mic_check.MARK_SILENT)
        self.assertIn("heard nothing", text)
        self.assertIn("when-it-goes-wrong.md", text)

    def test_room_tone_is_still_nothing(self):
        mark, _ = mic_check.sentence(int(32767 * 0.005))
        self.assertEqual(mark, mic_check.MARK_SILENT)

    def test_the_threshold_itself_counts_as_heard(self):
        mark, _ = mic_check.sentence(int(32767 * mic_check.HEARD) + 1)
        self.assertEqual(mark, mic_check.MARK_HEARD)


class RecordTests(unittest.TestCase):
    def test_it_asks_for_one_mono_int16_channel(self):
        fake = FakeSoundDevice(level=1000)
        mic_check.record(fake, seconds=0.1, rate=16000)
        self.assertEqual(len(fake.opened), 1)
        opened = fake.opened[0]
        self.assertEqual(opened["channels"], 1)
        self.assertEqual(opened["dtype"], "int16")
        self.assertEqual(opened["samplerate"], 16000)

    def test_it_returns_the_peak_of_what_the_stream_gave(self):
        self.assertEqual(mic_check.record(FakeSoundDevice(level=777), seconds=0.1), 777)


class CheckTests(unittest.TestCase):
    def _run(self, sd) -> tuple[int, str]:
        out = io.StringIO()
        code = mic_check.check(sd=sd, seconds=0.1, out=out)
        return code, out.getvalue()

    def test_a_loud_stream_says_heard_you_and_returns_zero(self):
        code, text = self._run(FakeSoundDevice(level=20000))
        self.assertEqual(code, 0)
        self.assertIn("say something", text)
        self.assertIn(f"  {mic_check.MARK_HEARD} heard you", text)

    def test_a_silent_stream_is_a_note_not_a_failure(self):
        code, text = self._run(FakeSoundDevice(level=0))
        self.assertEqual(code, 0, "a silent microphone must never fail the install")
        self.assertIn(f"  {mic_check.MARK_SILENT} heard nothing", text)
        self.assertIn("when-it-goes-wrong.md", text)

    def test_a_device_that_will_not_open_is_skipped_not_fatal(self):
        code, text = self._run(DeafSoundDevice())
        self.assertEqual(code, 0)
        self.assertIn(f"  {mic_check.MARK_SKIPPED} ", text)
        self.assertIn("no default input device", text)

    def test_nothing_is_ever_played(self):
        """The module opens inputs only — no output stream, no playback call."""
        body = Path(mic_check.__file__).read_text(encoding="utf-8")
        for forbidden in ("OutputStream", "sd.play", ".play(", "RawOutputStream"):
            self.assertNotIn(forbidden, body, f"{forbidden} would play audio")


if __name__ == "__main__":
    unittest.main()
