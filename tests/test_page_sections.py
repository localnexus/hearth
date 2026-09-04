"""Three pages are split into their own files now, and the seams have to hold.

The control panel (6 sections), the roster page (3) and the settings page (4)
were each one file with several unrelated jobs sharing one scope. Their sections
live under `ui/` and are spliced back in at render, so the SERVED pages are what
they always were — only the sources are separable.

These are NOT the shared layer. `brand.css`, `switch_card.js` and
`admin_shell.js` are spliced into several pages BECAUSE they must not drift
between them; a page section serves one page and exists to give it seams. Same
mechanism, opposite reason — so `test_sections_belong_to_one_page` asserts the
exclusion, and "it is in ui/" stays a description rather than an invitation.

What this pins, per page:

  * every section lands, verbatim and exactly once (a second copy would be a
    redeclaration — a page that dies on load);
  * nothing declares a name another section or the page already declares, since
    the splice lands inside the page's own <script> block and its declarations
    become the page's top-level bindings;
  * the couplings that reach ACROSS sections still resolve by name;
  * each splice refuses a page that lost its placeholder — in production that
    lands at startup, not as a per-request 500;
  * the written order of the placeholders, and each page's size.

What runs the resulting pages is tests/supervisor/test_pages_load.py, which
executes them under Node and fails on a name nobody defines.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from hearth.control import control as control_mod
from hearth.supervisor import curation as curation_mod
from hearth.supervisor import roster as roster_mod
from hearth.supervisor import routes as routes_mod
from hearth.supervisor import settings as settings_mod
from hearth.ui import panel, pages, roster_sections, settings_sections

#: A top-level declaration in a spliced file: these become the PAGE's bindings.
DECL = re.compile(r"(?m)^(?:async function|function|const|let|var)\s+(\w+)")

#: page → (sections, the served page, the page's own source file)
SETS = {
    "panel": (panel.SECTIONS, control_mod._HTML,
              Path(control_mod.__file__).parent / "control_page.html"),
    "roster": (roster_sections.SECTIONS, roster_mod._PAGE,
               Path(roster_mod.__file__).parent / "roster_page.html"),
    "settings": (settings_sections.SECTIONS, settings_mod._PAGE,
                 Path(settings_mod.__file__).parent / "settings_page.html"),
}

#: Pages with no sections of their own — nothing here may reach them.
SECTIONLESS = {
    "launch": routes_mod._LAUNCH_PAGE,
    "pair": routes_mod._PAIR_PAGE,
    "memory": curation_mod._PAGE,
}

#: The order the placeholders appear in each page. Preserved DELIBERATELY: it is
#: the order the sections were written in, and the order they still read in.
#:
#: It does not bind today — a swap was tried under the Node harness and loaded
#: clean, because every cross-section read happens after an `await`, by which
#: point the whole script body has run. That is a fragile thing to be relying on
#: silently, which is the reason to pin the order rather than to shrug at it: one
#: synchronous cross-section read of a `let` (see COUPLINGS) and the order starts
#: mattering, with the failure landing as a blank page.
ORDER = {
    "panel": ("panel_style.css", "panel_record.js", "panel_status.js",
              "panel_knobs.js", "panel_manual.js", "panel_turn.js"),
    "roster": ("roster_onboard.js", "roster_edit.js", "roster_fork.js"),
    "settings": ("settings_schema.js", "settings_files.js",
                 "settings_form.js", "settings_confirm.js"),
}

#: The `let`/`const` bindings one section declares and ANOTHER reads — the
#: references that would break silently on a rename, since neither file mentions
#: the other. Function declarations are excluded: they hoist, and a missing one
#: is what test_pages_load catches.
COUPLINGS = {
    "panel": (("knob", "panel_knobs.js", "panel_status.js"),
              ("selVoice", "panel_knobs.js", "panel_status.js")),
    "roster": (("roster", "roster_edit.js", "roster_onboard.js"),
               ("roster", "roster_edit.js", "roster_fork.js")),
    "settings": (),  # the four `let`s live in the page, above every placeholder
}


class PageSections(unittest.TestCase):

    def test_every_section_lands_in_its_page(self):
        for page_name, (sections, page, _) in SETS.items():
            html = page()
            for placeholder, name, _, _ in sections.modules:
                with self.subTest(page=page_name, section=name):
                    self.assertNotIn(placeholder, html,
                                     "placeholder was never replaced")
                    self.assertIn(sections.text(name), html,
                                  f"{name} missing or altered in the served page")

    def test_every_section_lands_exactly_once(self):
        """Twice would be a redeclaration: a page that dies on load."""
        for page_name, (sections, page, _) in SETS.items():
            html = page()
            for name in sections.names:
                with self.subTest(page=page_name, section=name):
                    self.assertEqual(html.count(sections.text(name)), 1)

    def test_no_two_sections_of_a_page_declare_the_same_name(self):
        """They share one scope, so a collision is a browser SyntaxError."""
        for page_name, (sections, _, _) in SETS.items():
            seen: dict[str, str] = {}
            for name in sections.names:
                for decl in DECL.findall(sections.text(name)):
                    owner = seen.setdefault(decl, name)
                    with self.subTest(page=page_name, name=decl):
                        self.assertEqual(owner, name,
                                         f"{decl} is declared in both {owner} "
                                         f"and {name} — one page scope")

    def test_no_page_redeclares_what_a_section_owns(self):
        """What stays in a page is its markup and the state every section reads.
        If it re-declares a section's name, someone re-forked it."""
        for page_name, (sections, _, src) in SETS.items():
            own = set(DECL.findall(src.read_text(encoding="utf-8")))
            for name in sections.names:
                for decl in DECL.findall(sections.text(name)):
                    with self.subTest(page=page_name, section=name, name=decl):
                        self.assertNotIn(decl, own,
                                         f"{src.name} declares {decl!r} itself "
                                         f"— it belongs to {name}")

    def test_the_cross_section_bindings_still_resolve(self):
        """A rename on either side of one of these breaks the other silently:
        the page still loads and one thing stops working."""
        for page_name, couplings in COUPLINGS.items():
            sections = SETS[page_name][0]
            for binding, declared_in, read_by in couplings:
                with self.subTest(page=page_name, binding=binding):
                    self.assertRegex(sections.text(declared_in),
                                     rf"(?m)^let {binding}\b")
                    self.assertRegex(sections.text(read_by),
                                     rf"\b{binding}\b")

    def test_the_written_order_is_preserved(self):
        """See ORDER: pinned because the freedom it currently has is accidental,
        not because a swap breaks anything today."""
        for page_name, expected in ORDER.items():
            sections, page, _ = SETS[page_name]
            html = page()
            found = sorted(sections.names, key=lambda n: html.index(sections.text(n)))
            with self.subTest(page=page_name):
                self.assertEqual(tuple(found), expected)

    def test_every_splice_refuses_a_page_without_its_placeholder(self):
        """Production builds pages at IMPORT, so this lands at startup rather
        than as a page that renders markup with nothing behind it."""
        for page_name, (sections, _, _) in SETS.items():
            for splice in sections.splices:
                with self.subTest(page=page_name, splice=splice.__doc__):
                    with self.assertRaises(ValueError):
                        splice("<style></style><script>'use strict';</script>")

    def test_sections_belong_to_one_page(self):
        """Not a shared layer. A second page taking one of these is a decision
        to make deliberately, not a splice line to copy."""
        served = dict(SECTIONLESS)
        served.update({n: p for n, (_, p, _) in SETS.items()})
        for page_name, (sections, _, _) in SETS.items():
            for other_name, other in served.items():
                if other_name == page_name:
                    continue
                html = other()
                for name in sections.names:
                    with self.subTest(section=name, seen_in=other_name):
                        self.assertNotIn(sections.text(name), html)

    def test_every_split_page_is_under_the_working_size_limit(self):
        """The point of the split. 16 KiB is the line a file has to justify
        crossing; these three were 41.6, 21.5 and 19.9 KiB. Re-growth is the
        expected failure — a section added inline instead of as a new file."""
        for page_name, (_, _, src) in SETS.items():
            size = src.stat().st_size
            with self.subTest(page=page_name):
                self.assertLess(size, 16 * 1024,
                                f"{src.name} is back to {size} B — put the new "
                                "section in its own ui/ file")

    def test_the_sections_helper_is_the_only_way_they_are_declared(self):
        """Three pages deriving paths and splices three ways is the drift this
        whole queue exists to cure; pages.Sections is the one derivation."""
        for page_name, (sections, _, _) in SETS.items():
            with self.subTest(page=page_name):
                self.assertIsInstance(sections, pages.Sections)
                self.assertEqual(sections.page, page_name)


if __name__ == "__main__":
    unittest.main()
