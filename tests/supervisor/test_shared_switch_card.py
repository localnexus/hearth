"""The companion switcher is ONE implementation, served by two surfaces.

The control panel (:65000) and the facade's launch page (:65001) both offer
"pick who's live". They used to build that form twice, and drifted: the panel
grew persona + model pickers, the launch page grew session + memory, and
neither learned the other's fields. ui/switch_card.js is now the single source,
spliced into both pages at import.

These are DIVERGENCE GUARDS. They do not test behaviour — they test that the
shared thing is still shared, so the next feature lands in one place instead of
being copied into the page that happened to need it first.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from hearth.control import control as control_mod
from hearth.supervisor import routes as routes_mod
from hearth.ui import switch_card

CARD = switch_card.PATH

# The fields the card owns. Anything here must be added ONCE, in the card.
SELECTION = ("character", "voice", "persona", "model")

# Ids of the hand-built pickers this refactor removed. Their reappearance in a
# page means someone rebuilt the form locally instead of extending the card.
RETIRED_IDS = ("sw-char", "sw-voice", "sw-persona", "sw-model", "sw-go",
               "pick-char", "pick-voice", "startbtn")


class SharedSwitchCard(unittest.TestCase):

    def test_both_surfaces_serve_the_same_card(self):
        """One file on disk, byte-identical in both pages — the whole point."""
        source = CARD.read_text(encoding="utf-8")
        self.assertEqual(switch_card.JS, source)
        for name, page in (("launch", routes_mod._LAUNCH_PAGE()),
                           ("control", control_mod._HTML())):
            with self.subTest(page=name):
                self.assertIn(source, page, f"{name} serves a different card")

    def test_the_splice_refuses_a_page_without_the_placeholder(self):
        """Serving the placeholder verbatim would ship a dead switcher."""
        with self.assertRaises(ValueError):
            switch_card.splice("<script>nothing here</script>")

    def test_both_pages_actually_splice_it(self):
        """A page that ships the placeholder ships a switcher that cannot run."""
        for name, page in (("launch", routes_mod._LAUNCH_PAGE()),
                           ("control", control_mod._HTML())):
            with self.subTest(page=name):
                self.assertNotIn("/*SWITCH_CARD_JS*/", page,
                                 "placeholder was never replaced")
                self.assertIn("window.HearthSwitchCard = (function", page)
                self.assertIn("HearthSwitchCard.mount(", page)

    def test_the_card_appears_exactly_once_per_page(self):
        """Two copies in one page would mean two switchers fighting over ids."""
        for name, page in (("launch", routes_mod._LAUNCH_PAGE()),
                           ("control", control_mod._HTML())):
            with self.subTest(page=name):
                self.assertEqual(page.count("window.HearthSwitchCard = (function"), 1)

    def test_neither_page_rebuilds_the_pickers(self):
        """The retired ids are the fingerprint of a locally rebuilt form."""
        # The RAW file, not the rendered page — a rebuilt picker would be in
        # the page's own markup. Each Page knows where it was read from, which
        # is one fewer path to keep in step with the tree.
        for name, page_obj in (("launch", routes_mod._LAUNCH_PAGE),
                               ("control", control_mod._HTML)):
            page = page_obj.path.read_text(encoding="utf-8")
            for bad in RETIRED_IDS:
                with self.subTest(page=name, id=bad):
                    self.assertNotIn(bad, page,
                                     f"{name} page rebuilds a picker ({bad}) — "
                                     "extend ui/switch_card.js instead")

    def test_the_field_set_is_declared_in_the_card(self):
        """The selection fields live in one array, in one file."""
        source = CARD.read_text(encoding="utf-8")
        match = re.search(r"const SELECTION = \[(.*?)\];", source, re.S)
        self.assertIsNotNone(match, "the card no longer declares SELECTION")
        declared = tuple(re.findall(r'"([a-z_]+)"', match.group(1)))
        self.assertEqual(declared, SELECTION)
        # Every declared field is actually offered as a picker.
        for field in SELECTION:
            with self.subTest(field=field):
                self.assertIn(f'["{field}", "', source.replace("'", '"'))


if __name__ == "__main__":
    unittest.main()
