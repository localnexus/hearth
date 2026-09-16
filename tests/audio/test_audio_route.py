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
                                 "path": None, "buffer_ms": None, "shed_ms": 0,
                                 "grace_left": None})

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

    def test_the_desk_never_counts_down_however_lost_its_headset_is(self):
        """The difference between the two routes, as a field: a desk
        conversation waits for its headset for as long as it takes."""
        for halves in (None, {"input": {"state": "lost"}},
                       {"input": {"state": "unpinned"}}):
            with self.subTest(halves=halves):
                self.assertIsNone(audio_route.desk_route_state(halves)["grace_left"])


class TheReasonASittingClosedItself(unittest.TestCase):
    """A conversation that closes ITSELF has one thing to say afterwards and
    one place to say it: the status it exits with. The supervisor names the
    reason from that number, and it must be able to do so without importing a
    pipeline — which is why the number lives in this module, the one with no
    imports at all.
    """

    def setUp(self):
        audio_route.clear_device_gone()
        self.addCleanup(audio_route.clear_device_gone)

    def test_the_status_is_three_and_it_is_named_rather_than_written_out(self):
        self.assertEqual(audio_route.EXIT_DEVICE_GONE, 3)

    def test_every_other_ending_exits_zero(self):
        self.assertFalse(audio_route.device_gone())
        self.assertEqual(audio_route.exit_status(), 0)

    def test_the_flag_is_what_carries_the_reason_through_the_close_ladder(self):
        """The transport sets it before it signals; the entry point reads it
        after the ladder has run. Nothing in between has to know."""
        audio_route.mark_device_gone()
        self.assertTrue(audio_route.device_gone())
        self.assertEqual(audio_route.exit_status(), audio_route.EXIT_DEVICE_GONE)
        audio_route.clear_device_gone()
        self.assertEqual(audio_route.exit_status(), 0)

    def test_saying_it_twice_says_it_once(self):
        audio_route.mark_device_gone()
        audio_route.mark_device_gone()
        self.assertEqual(audio_route.exit_status(), 3)

    def test_the_module_still_imports_nothing_but_the_grammar_needs(self):
        """The whole point of the number living here: four places read this
        module, and none of them should have to load pipecat or PortAudio."""
        import ast
        from pathlib import Path as _Path

        tree = ast.parse(_Path(audio_route.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertEqual(imported, {"re", "__future__"})


if __name__ == "__main__":
    unittest.main()
