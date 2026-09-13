"""test_mute_gate.py — MuteGate's initial state.

Pins today's default (starts open) and the new optional initial state
(starts closed), plus that a PTT hold's latched-baseline restore and
set_muted both still work correctly from a closed start.

Run:  .venv/bin/python -m unittest tests.test_mute_gate
"""

from __future__ import annotations

import unittest

from hearth.control.control_taps import MuteGate


class MuteGateInitialState(unittest.TestCase):
    def test_default_starts_open(self):
        gate = MuteGate()
        self.assertFalse(gate.is_muted)

    def test_muted_true_starts_closed(self):
        gate = MuteGate(muted=True)
        self.assertTrue(gate.is_muted)

    def test_ptt_hold_on_a_closed_start_returns_to_closed(self):
        gate = MuteGate(muted=True)
        gate.ptt_press()
        self.assertFalse(gate.is_muted)
        gate.ptt_release()
        self.assertTrue(gate.is_muted)

    def test_set_muted_false_opens_a_closed_start(self):
        gate = MuteGate(muted=True)
        gate.set_muted(False)
        self.assertFalse(gate.is_muted)


if __name__ == "__main__":
    unittest.main()
