"""The route word — the one grammar four places have to agree on.

`desk` or `remote:<device-id>` travels from the launch page's radio, through
`POST /admin/bot/start` and `POST /admin/switch`, into `BotChild.start`'s argv,
and finally into `bot.py`'s bind. Every one of those validates it, and they all
validate it HERE, so the four cannot drift apart.

The device-id shape is the point of most of these cases: a route word becomes
a process argument, so anything that could be a shell word, a path segment or a
flag has to be refused before it gets there.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from hearth.audio import route as audio_route


class TheRouteGrammar(unittest.TestCase):

    def test_the_desk_is_the_default_and_the_absence(self):
        for word in (None, "", "   ", "desk"):
            with self.subTest(word=word):
                self.assertEqual(audio_route.parse(word), ("desk", None))
                self.assertTrue(audio_route.valid(word))

    def test_a_remote_route_carries_its_device(self):
        kind, device = audio_route.parse("remote:pixel")
        self.assertEqual((kind, device), ("remote", "pixel"))
        self.assertEqual(audio_route.device_of("remote:pixel"), "pixel")
        self.assertIsNone(audio_route.device_of("desk"))

    def test_device_ids_may_carry_word_characters_dot_and_dash(self):
        for device in ("a", "Pixel-9", "pixel.pro_2", "x" * 64):
            with self.subTest(device=device):
                self.assertEqual(audio_route.parse(f"remote:{device}"),
                                 ("remote", device))

    def test_anything_that_could_become_a_shell_word_is_refused(self):
        refused = (
            "remote:",                  # no device at all
            "remote:" + "x" * 65,       # past the length ceiling
            "remote:pix el",            # a space
            "remote:../../etc/passwd",  # a path
            "remote:pixel;rm -rf /",    # a command
            "remote:pixel\n--keep",     # a second flag on a new line
            "remote:pixel/desk",        # a path segment
            "--muted",                  # a flag wearing a route's clothes
            "deskish", "DESK", "local", "remote", "remote::x",
        )
        for word in refused:
            with self.subTest(word=word):
                self.assertFalse(audio_route.valid(word))
                with self.assertRaises(ValueError):
                    audio_route.parse(word)

    def test_the_refusal_names_the_word_and_the_grammar(self):
        """A person reading a 400 should be able to fix it without the source."""
        with self.assertRaises(ValueError) as caught:
            audio_route.parse("remote:pix el")
        message = str(caught.exception)
        self.assertIn("remote:pix el", message)
        self.assertIn("remote:<device-id>", message)

    def test_the_label_is_what_a_person_reads_back(self):
        self.assertEqual(audio_route.label("desk"), "the desk")
        self.assertEqual(audio_route.label(None), "the desk")
        self.assertEqual(audio_route.label("remote:Pixel"), "Pixel")


class TheDeskSideOfTheRouteObject(unittest.TestCase):
    """The desk reports per-direction device state; `/route` reports one word.

    The rule is the worst of the two halves, because a sitting with one half
    lost is a sitting that is not working — and reporting "pinned" while the
    microphone is gone would be the same class of fault as a check that reports
    green because it was pointed at the wrong file.
    """

    def test_both_halves_healthy_is_pinned(self):
        state = audio_route.desk_route_state(
            {"input": {"state": "ok"}, "output": {"state": "ok"}})
        self.assertEqual(state, {"kind": "desk", "device": None, "state": "pinned",
                                 "path": None, "buffer_ms": None, "shed_ms": 0})

    def test_one_half_lost_wins_over_the_other_half_healthy(self):
        for halves in ({"input": {"state": "lost"}, "output": {"state": "ok"}},
                       {"input": {"state": "ok"}, "output": {"state": "lost"}}):
            with self.subTest(halves=halves):
                self.assertEqual(audio_route.desk_route_state(halves)["state"], "lost")

    def test_recovered_shows_over_healthy_but_under_lost(self):
        self.assertEqual(audio_route.desk_route_state(
            {"input": {"state": "recovered"}, "output": {"state": "ok"}})["state"],
            "recovered")
        self.assertEqual(audio_route.desk_route_state(
            {"input": {"state": "recovered"}, "output": {"state": "lost"}})["state"],
            "lost")

    def test_an_unpinned_desk_says_so_rather_than_claiming_a_pin(self):
        self.assertEqual(audio_route.desk_route_state(
            {"input": {"state": "unpinned"}, "output": {"state": "unpinned"}})["state"],
            "unpinned")

    def test_nothing_to_report_is_the_resting_shape(self):
        for nothing in (None, {}, {"input": None}):
            with self.subTest(nothing=nothing):
                self.assertEqual(audio_route.desk_route_state(nothing)["state"], "pinned")
                self.assertEqual(audio_route.desk_route_state(nothing)["kind"], "desk")


if __name__ == "__main__":
    unittest.main()
