"""The doors that skip the bearer — pinned to the code.

Every path in the exempt set is something a caller can open WITHOUT the key, so
the set is worth stating out loud in one place and checking against the prose
that describes it. The count has drifted twice (four shells became five when
device pairing landed, and the docs lagged both times), which is why this is a
test and not a comment.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hearth.supervisor import routes as routes_mod


class UnauthedDoorsAreDocumented(unittest.TestCase):
    """The count of bearer-skipping paths has drifted twice; pin it to the code.

    Every path here is a door someone can open without the key, so the set is
    worth stating out loud in one place. If a route is added or removed, this
    test fails and names the docs that must be corrected in the same stroke.
    """

    DOCS = ("docs/runbook/02.5-control-panel/admin-surface.md",
            "docs/glossary/A-M.md")

    def test_the_exempt_set_is_exactly_what_we_think(self):
        from hearth.serve.app import _AUTH_EXEMPT
        self.assertEqual(sorted(_AUTH_EXEMPT), [
            "/admin/launch",
            "/admin/memory/ui",
            "/admin/pair/claim",
            "/admin/pair/ui",
            "/admin/roster",
            "/admin/settings/ui",
            "/health",
            "/ui/brand/favicon.png",
            "/ui/brand/mark.png",
        ], "the unauthed set changed — update " + " and ".join(self.DOCS))

    def test_the_docs_state_the_right_shell_count(self):
        """Five static shells; /health and the pairing claim make seven paths."""
        import hearth
        root = Path(hearth.__file__).parents[2]  # src/hearth/__init__.py → repo
        for rel in self.DOCS:
            text = (root / rel).read_text(encoding="utf-8")
            with self.subTest(doc=rel):
                self.assertIn("five unauthed static shells", text)
                self.assertNotIn("four unauthed static shells", text)

    def test_the_only_unauthed_assets_are_artwork(self):
        """The brand exemption is for two images and must never become a
        general static route: anything under /ui/ that reads operator state
        would be an unauthenticated door onto it."""
        from hearth.serve.app import _AUTH_EXEMPT
        from hearth.ui import brand

        assets = sorted(p for p in _AUTH_EXEMPT if p.startswith("/ui/"))
        self.assertEqual(assets, sorted(brand.ROUTES),
                         "/ui/ exemptions must be exactly the brand artwork")
        for path in assets:
            with self.subTest(path=path):
                self.assertTrue(path.endswith(".png"),
                                f"{path} is not artwork — it must not be unauthed")


if __name__ == "__main__":
    unittest.main()
