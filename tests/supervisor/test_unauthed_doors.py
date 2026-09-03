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
        ], "the unauthed set changed — update " + " and ".join(self.DOCS))

    def test_the_docs_state_the_right_shell_count(self):
        """Five static shells; /health and the pairing claim make seven paths."""
        root = Path(routes_mod.__file__).parent.parent.parent.parent
        for rel in self.DOCS:
            text = (root / rel).read_text(encoding="utf-8")
            with self.subTest(doc=rel):
                self.assertIn("five unauthed static shells", text)
                self.assertNotIn("four unauthed static shells", text)


if __name__ == "__main__":
    unittest.main()
