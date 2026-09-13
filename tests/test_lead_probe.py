"""test_lead_probe.py — the output-buffer lead beside /presence's level (S2 sibling).

Proves:
  1. honesty — no stream, or a stream that raises, or a zero sample rate reads
     as None (unknown), never a fabricated zero;
  2. calibration — an empty ring shows all of its capacity on first read, so
     that same free space reads back as zero queued;
  3. the arithmetic — queued frames become seconds against the sample rate,
     and capacity only ever grows to the largest free space ever observed;
  4. a failing stream is unknown, not zero or sticky — a later good read
     recovers.

Run:  .venv/bin/python -m unittest tests.test_lead_probe
"""

from __future__ import annotations

import types
import unittest

from hearth.control.features.presence import LeadProbe


class _Stream:
    def __init__(self, free: int) -> None:
        self.free = free
        self.raise_ = False

    def get_write_available(self) -> int:
        if self.raise_:
            raise OSError("stream unavailable")
        return self.free


def _out(free=None, rate=24000):
    return types.SimpleNamespace(
        _out_stream=None if free is None else _Stream(free),
        _sample_rate=rate)


class LeadProbeTests(unittest.TestCase):

    def test_no_stream_is_unknown(self):
        probe = LeadProbe(_out())
        self.assertIsNone(probe.lead_s())

    def test_empty_ring_calibrates_and_reads_zero(self):
        probe = LeadProbe(_out(free=4800))
        self.assertEqual(probe.lead_s(), 0.0)

    def test_queued_frames_become_seconds(self):
        out = _out(free=4800)
        probe = LeadProbe(out)
        probe.lead_s()  # calibrate
        out._out_stream.free = 2400
        self.assertAlmostEqual(probe.lead_s(), 0.1)

    def test_capacity_follows_the_largest_free_space_seen(self):
        out = _out(free=4800)
        probe = LeadProbe(out)
        self.assertEqual(probe.lead_s(), 0.0)
        out._out_stream.free = 9600
        self.assertEqual(probe.lead_s(), 0.0)
        out._out_stream.free = 4800
        self.assertAlmostEqual(probe.lead_s(), 0.2)

    def test_a_failing_stream_is_unknown_not_zero(self):
        out = _out(free=4800)
        probe = LeadProbe(out)
        probe.lead_s()  # a good read first
        out._out_stream.raise_ = True
        self.assertIsNone(probe.lead_s())
        out._out_stream.raise_ = False
        self.assertIsInstance(probe.lead_s(), float)

    def test_zero_rate_is_unknown(self):
        probe = LeadProbe(_out(free=4800, rate=0))
        self.assertIsNone(probe.lead_s())


if __name__ == "__main__":
    unittest.main()
