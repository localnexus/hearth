"""The control panel is five files now, and the seams have to hold.

control_page.html was 822 lines and 41.6 KiB, with the mic controls, the status
meters, the hot knobs and the manual reader all sharing one flat scope. Four
files came out of it (ui/panel_style.css, panel_status.js, panel_knobs.js,
panel_manual.js) and are spliced back in at render, so the SERVED page is what it
always was — only the sources are separable.

That mechanism buys nothing unless three things stay true, which is what this
file pins:

  1. every module actually lands in the page, verbatim and exactly once;
  2. the splice ORDER is preserved — `panel_status`'s renderAgent() reads the
     `knob` and `selVoice` that `panel_knobs` declares with `let`, so knobs
     spliced first would put the page's first paint in the temporal dead zone;
  3. nothing declares a name another module already declares. The splice lands
     inside the page's own <script> block, so its declarations are the page's
     top-level bindings and a collision is a browser SyntaxError, not a shadow.

These are single-page files, unlike ui/brand.css, switch_card.js and
admin_shell.js — those are shared BETWEEN pages and exist to stop drift; these
exist to give one page seams. The last test states that difference so a future
edit has to argue with it rather than assume "it is in ui/" means "reusable".

What runs the resulting page is tests/supervisor/test_pages_load.py, which
executes it under Node and fails on a name nobody defines.

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
from hearth.ui import pages, panel

PAGE_SRC = Path(control_mod.__file__).parent / "control_page.html"

#: The other five served pages — none of them may take a panel module.
OTHER_PAGES = {
    "launch": routes_mod._LAUNCH_PAGE,
    "pair": routes_mod._PAIR_PAGE,
    "roster": roster_mod._PAGE,
    "settings": settings_mod._PAGE,
    "memory": curation_mod._PAGE,
}

#: A top-level declaration in a spliced file: these become the PAGE's bindings.
DECL = re.compile(r"(?m)^(?:async function|function|const|let|var)\s+(\w+)")


def module_text(name: str) -> str:
    return pages.text(panel.PATHS[name])


class PanelModules(unittest.TestCase):

    def test_every_module_lands_in_the_page(self):
        html = control_mod._HTML()
        for placeholder, name, _, _ in panel.MODULES:
            with self.subTest(module=name):
                self.assertNotIn(placeholder, html,
                                 "placeholder was never replaced")
                self.assertIn(module_text(name), html,
                              f"{name} missing or altered in the served page")

    def test_every_module_lands_exactly_once(self):
        """Twice would be a redeclaration: a page that dies on load."""
        html = control_mod._HTML()
        for _, name, _, _ in panel.MODULES:
            with self.subTest(module=name):
                self.assertEqual(html.count(module_text(name)), 1)

    def test_status_is_spliced_before_knobs(self):
        """The one ordering CONTRACT. renderAgent() (status) reads `knob` and
        `selVoice`, which knobs declares with `let` — safe today only because
        the read happens after an await, with the whole script body already run.
        Swap the two and the page's first paint raises on the dead zone."""
        html = control_mod._HTML()
        self.assertLess(html.index(module_text("panel_status.js")),
                        html.index(module_text("panel_knobs.js")))

    def test_the_pair_that_reaches_across_still_matches(self):
        """A rename on either side of the status/knobs seam breaks the other
        silently — the page loads and one line stops painting."""
        status, knobs = module_text("panel_status.js"), module_text("panel_knobs.js")
        for name in ("knob", "selVoice"):
            with self.subTest(name=name, declared_in="panel_knobs.js"):
                self.assertRegex(knobs, rf"(?m)^let {name}\b")
                self.assertIn(name, status)
        self.assertRegex(status, r"(?m)^function renderAgent\(")
        self.assertIn("renderAgent()", knobs)

    def test_no_two_modules_declare_the_same_name(self):
        """They share one scope, so this is a SyntaxError in the browser."""
        seen: dict[str, str] = {}
        for _, name, _, _ in panel.MODULES:
            if not name.endswith(".js"):
                continue
            for decl in DECL.findall(module_text(name)):
                owner = seen.setdefault(decl, name)
                with self.subTest(name=decl):
                    self.assertEqual(owner, name,
                                     f"{decl} is declared in both {owner} and "
                                     f"{name} — they share the page's scope")

    def test_the_page_does_not_redeclare_what_a_module_owns(self):
        """What stays in the page is the transport ($ / status / post) and the
        controls. If it re-declares a module's name, someone re-forked it."""
        own = PAGE_SRC.read_text(encoding="utf-8")
        page_names = set(DECL.findall(own))
        for _, name, _, _ in panel.MODULES:
            if not name.endswith(".js"):
                continue
            for decl in DECL.findall(module_text(name)):
                with self.subTest(module=name, name=decl):
                    self.assertNotIn(
                        decl, page_names,
                        f"control_page.html declares {decl!r} itself — it "
                        f"belongs to {name}")

    def test_every_splice_refuses_a_page_without_its_placeholder(self):
        """Production builds pages at IMPORT, so this failure lands at startup
        rather than as a panel that renders markup with nothing behind it."""
        for splice in panel.SPLICES:
            with self.subTest(splice=splice.__doc__):
                with self.assertRaises(ValueError):
                    splice("<style></style><script>'use strict';</script>")

    def test_the_modules_belong_to_this_page_alone(self):
        """Not a shared layer. brand/switch_card/admin_shell are spliced into
        several pages BECAUSE they must not drift between them; these four serve
        one page and were split to give it seams. A second page taking one is a
        decision to make deliberately, not by copying a splice line."""
        for page_name, page in OTHER_PAGES.items():
            html = page()
            for _, name, _, _ in panel.MODULES:
                with self.subTest(page=page_name, module=name):
                    self.assertNotIn(module_text(name), html)

    def test_the_page_stays_under_the_working_size_limit(self):
        """The point of the split. 16 KiB is the line a file has to justify
        crossing; this page was 41.6 KiB before it. Re-growth is the expected
        failure — a section added inline instead of as a fifth module."""
        size = PAGE_SRC.stat().st_size
        self.assertLess(size, 16 * 1024,
                        f"control_page.html is back to {size} B — put the new "
                        "section in its own ui/panel_*.js file")


if __name__ == "__main__":
    unittest.main()
