"""The admin pages' shell is ONE file, spliced into four pages.

Five authed pages (launch, first-run, roster, settings, memory) each carried their own copy
of the same preamble: the `$`/`el` helpers, the bearer in localStorage, the
authed `api()`, `show`/`report`, the token-entry wiring, the poll loop. They had
already drifted — the launch page's `api()` grew a `json:` convenience the others
never got, and it called its status line `say()` while the other three called the
same function `report()`. ui/admin_shell.js is now the single source.

DIVERGENCE GUARDS, the same idiom as test_shared_switch_card and
test_shared_brand: not what the helpers do, only that they are still shared.

The exclusion is part of the contract. pair_page.html must NOT take the shell:
it is the page a device without the bearer opens, and the shell exists to carry
the bearer. The control panel must not either — it is unauthed loopback and has
no token to hold.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import unittest

from hearth.control import control as control_mod
from hearth.supervisor import curation as curation_mod
from hearth.supervisor import firstrun as firstrun_mod
from hearth.supervisor import roster as roster_mod
from hearth.supervisor import routes as routes_mod
from hearth.supervisor import settings as settings_mod
from hearth.ui import admin_shell
from hearth.ui import switch_card

#: The five pages that take the shell, as served.
SHELLED = {
    "launch": routes_mod._LAUNCH_PAGE,
    "firstrun": firstrun_mod._PAGE,
    "roster": roster_mod._PAGE,
    "settings": settings_mod._PAGE,
    "memory": curation_mod._PAGE,
}

#: The two that must not — and why, so a future edit has to argue with it.
EXCLUDED = {
    "pair": (routes_mod._PAIR_PAGE,
             "the page a device WITHOUT the bearer opens"),
    "control": (control_mod._HTML,
                "unauthed loopback panel — it holds no token"),
}

# Declaring any of these in a page means someone re-forked the shell. These are
# the exact names the shell owns; a page that needs a variant should extend the
# shell, not shadow it (and a redeclaration is a SyntaxError in the browser,
# since the splice lands inside the page's own script scope).
OWNED = (
    re.compile(r"(?m)^\s*const \$ ="),
    re.compile(r"(?m)^\s*const el ="),
    re.compile(r"(?m)^\s*const TOKEN_KEY ="),
    re.compile(r"(?m)^\s*(async )?function (token|setToken|api|show|report|"
               r"wireToken|poll)\("),
)


class SharedAdminShell(unittest.TestCase):

    def test_every_authed_page_gets_the_shell(self):
        for name, page in SHELLED.items():
            with self.subTest(page=name):
                html = page()
                self.assertNotIn(admin_shell.PLACEHOLDER, html,
                                 "placeholder was never replaced")
                self.assertIn(admin_shell.JS, html, "shell missing or altered")

    def test_the_shell_appears_exactly_once_per_page(self):
        """Two copies would be a redeclaration — a page that dies on load."""
        marker = 'const TOKEN_KEY = "hearth_admin_token";'
        for name, page in SHELLED.items():
            with self.subTest(page=name):
                self.assertEqual(page().count(marker), 1)

    @staticmethod
    def _own_code(page) -> str:
        """The page minus every shared file it splices in — what it still says
        for itself. The switcher is stripped too: it is a closure with its own
        private helpers (its `say()` writes to the card's state line), and those
        are not the page re-declaring anything."""
        return page().replace(admin_shell.JS, "").replace(switch_card.JS, "")

    def test_no_page_redeclares_what_the_shell_owns(self):
        """The drift this cured: two spellings of api(), two names for the
        status line. A page that re-declares one has forked it again."""
        for name, page in SHELLED.items():
            body = self._own_code(page)
            for pattern in OWNED:
                with self.subTest(page=name, pattern=pattern.pattern):
                    found = pattern.search(body)
                    if found is not None:
                        self.fail(f"{name} declares {found.group(0).strip()!r} "
                                  "itself — put it in ui/admin_shell.js instead")

    def test_the_excluded_pages_stay_excluded(self):
        """Not an oversight — a deliberate boundary, stated with its reason."""
        for name, (page, why) in EXCLUDED.items():
            with self.subTest(page=name):
                self.assertNotIn(admin_shell.JS, page(),
                                 f"{name} must not take the shell: {why}")

    def test_pages_use_the_shell_rather_than_hand_rolling(self):
        """The call sites that prove the extraction actually landed: nobody
        re-wires the token field or re-implements render-now-then-poll."""
        for name, page in SHELLED.items():
            with self.subTest(page=name):
                body = self._own_code(page)
                self.assertIn("wireToken(refresh)", body,
                              "page re-wires token entry by hand")
                self.assertIn("poll(refresh", body,
                              "page re-implements the poll loop")
                self.assertNotIn("localStorage.setItem", body,
                                 "page writes the bearer itself")

    def test_the_status_line_has_one_name(self):
        """`say()` on one page and `report()` on three was the drift; the shell
        resolves #report or #msg, so the four pages call one function."""
        for name, page in SHELLED.items():
            with self.subTest(page=name):
                body = self._own_code(page)
                self.assertNotIn("say(", body)

    def test_splice_refuses_a_page_without_the_placeholder(self):
        """Silently serving a page whose helpers are undefined is a blank screen
        and a console error — discovered by a person, not by startup."""
        with self.assertRaises(ValueError):
            admin_shell.splice("<script>'use strict';</script>")


if __name__ == "__main__":
    unittest.main()
