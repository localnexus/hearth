"""The access-key explainer: one file, both front doors, and never a real key.

The launch page and the first-run page open on a card that asks for the access
key. ui/key_help.js says, in plain words, what the key is, where it lives and
what it looks like — with an EXAMPLE. Both pages are served without auth, so
the guard that matters is that the example is the ONLY 64-hex run either page
ever carries.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import unittest

from hearth.supervisor import firstrun as firstrun_mod
from hearth.supervisor import routes as routes_mod
from hearth.ui import key_help

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")

PAGES = (("launch", routes_mod._LAUNCH_PAGE), ("firstrun", firstrun_mod._PAGE))


class KeyHelp(unittest.TestCase):

    def test_both_front_doors_carry_the_same_explainer_once(self):
        source = key_help.PATH.read_text(encoding="utf-8")
        self.assertEqual(key_help.JS, source)
        for name, page in PAGES:
            with self.subTest(page=name):
                html = page()
                self.assertNotIn(key_help.PLACEHOLDER, html, "placeholder never replaced")
                self.assertEqual(html.count(source), 1, "explainer missing or doubled")
                self.assertIn('id="keyhelp"', html, "no mount inside the token card")

    def test_the_example_is_the_only_key_shaped_thing_on_the_page(self):
        """Served unauthed: a real key in the shell would be an open door."""
        self.assertIn(key_help.EXAMPLE, key_help.JS)
        for name, page in PAGES:
            with self.subTest(page=name):
                runs = set(HEX64.findall(page()))
                self.assertEqual(runs, {key_help.EXAMPLE})

    def test_the_example_is_visibly_not_random(self):
        """A stranger must be able to tell it is a pattern, not their key."""
        self.assertEqual(key_help.EXAMPLE, "0123456789abcdef" * 4)

    def test_the_splice_refuses_a_page_without_the_placeholder(self):
        with self.assertRaises(ValueError):
            key_help.splice("<script>nothing here</script>")


if __name__ == "__main__":
    unittest.main()
