"""The wait for a device that went away — `audio/remote_grace.py`.

The whole module exists to be tested without spending the time it measures, so
every case here drives an injected clock: the window is three minutes long and
this file runs in milliseconds.

What is worth pinning is the behaviour a person will actually meet. The count
must never read below zero or jump about; a device that comes back must be told
how long it was gone (that gap is what goes in the log line); a window nobody
opened must not claim to have expired; and a wrong environment value must leave
the proven figure standing rather than produce a conversation that closes the
moment a phone blinks.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from hearth.audio import remote_grace


class _Clock:
    """A clock that only moves when a test says so."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


class TheWindowsLife(unittest.TestCase):

    def setUp(self):
        self.clock = _Clock()
        self.window = remote_grace.GraceWindow(180, clock=self.clock)

    def test_a_window_nobody_opened_is_not_running_and_has_not_expired(self):
        self.assertFalse(self.window.active)
        self.assertFalse(self.window.expired())
        self.assertEqual(self.window.left(), 0)
        self.assertEqual(self.window.elapsed(), 0.0)

    def test_the_count_starts_whole_and_walks_down_to_zero(self):
        self.window.lose()
        self.assertTrue(self.window.active)
        self.assertEqual(self.window.left(), 180)
        self.clock.tick(20)
        self.assertEqual(self.window.left(), 160, "2:40, the card's own reading")
        self.clock.tick(159.5)
        self.assertEqual(self.window.left(), 1, "rounded up: not yet gone")
        self.assertFalse(self.window.expired())

    def test_past_the_window_it_is_expired_and_never_counts_below_zero(self):
        self.window.lose()
        self.clock.tick(180)
        self.assertTrue(self.window.expired())
        self.assertEqual(self.window.left(), 0)
        self.clock.tick(600)
        self.assertEqual(self.window.left(), 0, "never below zero, however long")
        self.assertTrue(self.window.expired())

    def test_a_device_that_comes_back_is_told_how_long_it_was_gone(self):
        self.window.lose()
        self.clock.tick(12.4)
        self.assertAlmostEqual(self.window.rejoin(), 12.4)
        self.assertFalse(self.window.active)
        self.assertFalse(self.window.expired())
        self.assertEqual(self.window.left(), 0)

    def test_a_first_connection_is_not_a_return(self):
        self.assertEqual(self.window.rejoin(), 0.0)

    def test_a_second_loss_waits_for_the_device_that_just_left(self):
        self.window.lose()
        self.clock.tick(100)
        self.window.lose()
        self.assertEqual(self.window.left(), 180)

    def test_a_clock_that_goes_backwards_is_not_a_conversation_that_ends(self):
        """Not expected from a monotonic clock — but the count is read on every
        poll and a negative elapsed would show a window growing."""
        self.window.lose()
        self.clock.tick(-30)
        self.assertEqual(self.window.elapsed(), 0.0)
        self.assertEqual(self.window.left(), 180)


class TheWindowLength(unittest.TestCase):

    def setUp(self):
        self.addCleanup(self._unset)
        self._unset()

    def _unset(self):
        import os

        os.environ.pop(remote_grace.GRACE_ENV, None)

    def _set(self, value):
        import os

        os.environ[remote_grace.GRACE_ENV] = value

    def test_the_proven_figure_is_the_default(self):
        self.assertEqual(remote_grace.DEFAULT_GRACE_S, 180)
        self.assertEqual(remote_grace.grace_seconds(), 180.0)
        self.assertEqual(remote_grace.GraceWindow().seconds, 180.0)

    def test_the_environment_sets_it(self):
        self._set("45")
        self.assertEqual(remote_grace.grace_seconds(), 45.0)
        self.assertEqual(remote_grace.GraceWindow().seconds, 45.0)

    def test_nonsense_and_non_positive_values_leave_the_default_standing(self):
        """The expensive mistake is a conversation that closes the moment a
        phone blinks, so a wrong word falls back rather than being obeyed."""
        for bad in ("", "   ", "soon", "0", "-5", "nan", "inf"):
            with self.subTest(bad=bad):
                self._set(bad)
                self.assertEqual(remote_grace.grace_seconds(), 180.0)

    def test_an_explicit_length_is_held_to_the_same_rule(self):
        for bad in (0, -1, None, "words"):
            with self.subTest(bad=bad):
                self.assertEqual(remote_grace.GraceWindow(bad).seconds, 180.0)
        self.assertEqual(remote_grace.GraceWindow(30).seconds, 30.0)

    def test_the_window_reads_the_environment_when_it_is_built(self):
        self._set("60")
        window = remote_grace.GraceWindow(clock=_Clock())
        self.assertEqual(window.seconds, 60.0)

    def test_the_default_clock_is_the_monotonic_one(self):
        """A wall clock that a time change, a sleep or a daylight-saving hour
        can move is not a wait. Read off the signature, because the default is
        bound once and a patch of the module would not show it."""
        import inspect
        import time

        default = inspect.signature(
            remote_grace.GraceWindow).parameters["clock"].default
        self.assertIs(default, time.monotonic)


class TheStartWaitLength(unittest.TestCase):
    """The wait before any device: its own word, the same rule, the same
    default — and independent of the loss window's word, so lengthening one
    (a fifteen-minute loss window) does not silently lengthen the other."""

    def setUp(self):
        self._unset(); self.addCleanup(self._unset)

    def _unset(self):
        import os
        os.environ.pop(remote_grace.START_WAIT_ENV, None)
        os.environ.pop(remote_grace.GRACE_ENV, None)

    def test_the_default_is_the_same_proven_figure(self):
        self.assertEqual(remote_grace.DEFAULT_START_WAIT_S, 180)
        self.assertEqual(remote_grace.start_wait_seconds(), 180.0)

    def test_its_own_environment_word_sets_it(self):
        import os
        os.environ[remote_grace.START_WAIT_ENV] = "45"
        self.assertEqual(remote_grace.start_wait_seconds(), 45.0)

    def test_the_loss_windows_word_does_not_reach_it(self):
        import os
        os.environ[remote_grace.GRACE_ENV] = "900"
        self.assertEqual(remote_grace.start_wait_seconds(), 180.0)
        self.assertEqual(remote_grace.grace_seconds(), 900.0)

    def test_nonsense_and_non_positive_values_leave_the_default_standing(self):
        import os
        for bad in ("", "   ", "soon", "0", "-5", "nan", "inf"):
            with self.subTest(bad=bad):
                os.environ[remote_grace.START_WAIT_ENV] = bad
                self.assertEqual(remote_grace.start_wait_seconds(), 180.0)


if __name__ == "__main__":
    unittest.main()
