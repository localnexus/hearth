"""The two reasons a sitting closes itself, and the status each exits with —
`audio/route.py`. Nothing else exits non-zero from a completed sitting, so the
launch page's inference rests on these numbers staying distinct.
"""

from __future__ import annotations

import unittest

from hearth.audio import route as audio_route


class TheExitReasons(unittest.TestCase):

    def setUp(self):
        audio_route.clear_device_gone()
        self.addCleanup(audio_route.clear_device_gone)

    def test_a_completed_sitting_exits_zero(self):
        self.assertEqual(audio_route.exit_status(), 0)

    def test_a_device_that_went_away_is_three(self):
        audio_route.mark_device_gone()
        self.assertEqual(audio_route.exit_status(), audio_route.EXIT_DEVICE_GONE)
        self.assertEqual(audio_route.EXIT_DEVICE_GONE, 3)

    def test_a_device_that_never_arrived_is_four(self):
        audio_route.mark_device_never_came()
        self.assertEqual(audio_route.exit_status(), audio_route.EXIT_DEVICE_NEVER_CAME)
        self.assertEqual(audio_route.EXIT_DEVICE_NEVER_CAME, 4)
        self.assertNotEqual(audio_route.EXIT_DEVICE_NEVER_CAME, audio_route.EXIT_DEVICE_GONE)

    def test_clearing_forgets_both(self):
        audio_route.mark_device_gone()
        audio_route.mark_device_never_came()
        audio_route.clear_device_gone()
        self.assertFalse(audio_route.device_gone())
        self.assertFalse(audio_route.device_never_came())
        self.assertEqual(audio_route.exit_status(), 0)


if __name__ == "__main__":
    unittest.main()
