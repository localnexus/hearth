"""test_dev_reload.py — page files: read once, or per request while editing.

ui/pages.py exists to make page work cheap without giving up the property that
makes page serving safe. Both halves are tested here, because each is invisible
from the other side:

  PRODUCTION (default) — the page is read and transformed ONCE, at import. A
  handler cannot be handed different bytes than the process started with, no
  request touches the disk, and a page that cannot be built fails at STARTUP,
  loudly, rather than 500-ing one request at a time.

  DEV RELOAD (HEARTH_DEV_RELOAD=1) — every call re-reads, including the shared
  files a transform splices in (brand.css, switch_card.js). Editing a page then
  costs a browser refresh instead of a facade or bot restart.

The flag is read once at import, so these tests patch pages.DEV_RELOAD directly
rather than the environment — that IS the seam, and patching it here is the same
thing the process does at startup.

Run:  .venv/bin/python -m unittest tests.test_dev_reload
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth.ui import pages


class FlagParsing(unittest.TestCase):
    """Off unless someone means it — this decides a production property."""

    def test_off_by_default(self):
        self.assertFalse(pages._flag(""))
        for value in ("0", "false", "FALSE", "no", "  ", " no "):
            with self.subTest(value=value):
                self.assertFalse(pages._flag(value))

    def test_on_when_asked(self):
        for value in ("1", "true", "TRUE", "yes", " 1 ", "on"):
            with self.subTest(value=value):
                self.assertTrue(pages._flag(value))

    def test_the_running_process_is_not_in_dev_mode(self):
        """A test run is a production-shaped import; if this ever fails, the
        suite is measuring the wrong mode everywhere else."""
        self.assertFalse(pages.DEV_RELOAD)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "page.html"
        self.path.write_text("<p>first</p>", encoding="utf-8")
        # The cache is module state keyed by path; keep tests independent.
        self.addCleanup(pages._CACHE.clear)

    def dev(self, on: bool):
        patch = mock.patch.object(pages, "DEV_RELOAD", on)
        patch.start()
        self.addCleanup(patch.stop)


class ProductionReadsOnce(_Tmp):

    def test_an_edit_does_not_reach_a_running_process(self):
        self.dev(False)
        page = pages.Page(self.path)
        self.path.write_text("<p>second</p>", encoding="utf-8")
        self.assertEqual(page(), "<p>first</p>")
        self.assertEqual(page(), "<p>first</p>")

    def test_text_caches_per_path(self):
        self.dev(False)
        self.assertEqual(pages.text(self.path), "<p>first</p>")
        self.path.write_text("<p>second</p>", encoding="utf-8")
        self.assertEqual(pages.text(self.path), "<p>first</p>")

    def test_a_page_that_cannot_be_built_fails_at_construction(self):
        """Import time is startup. A page whose transform refuses it (brand.splice
        on a page that lost its placeholder) must take the process down then —
        never serve a 500 per request in production."""
        self.dev(False)
        def refuse(_src):
            raise ValueError("no placeholder")
        with self.assertRaises(ValueError):
            pages.Page(self.path, refuse)


class DevReloadRereads(_Tmp):

    def test_an_edit_lands_on_the_next_call(self):
        self.dev(True)
        page = pages.Page(self.path)
        self.assertEqual(page(), "<p>first</p>")
        self.path.write_text("<p>second</p>", encoding="utf-8")
        self.assertEqual(page(), "<p>second</p>")

    def test_shared_sources_reload_too(self):
        """The reason to run dev reload at all is usually the SHARED file — the
        switch card or the palette. A page that reloaded but kept a stale card
        would be a trap."""
        self.dev(True)
        shared = self.path.with_name("shared.css")
        shared.write_text("a{}", encoding="utf-8")
        self.path.write_text("<style>/*X*/</style>", encoding="utf-8")
        page = pages.Page(self.path,
                          lambda src: src.replace("/*X*/", pages.text(shared)))
        self.assertEqual(page(), "<style>a{}</style>")
        shared.write_text("b{}", encoding="utf-8")
        self.assertEqual(page(), "<style>b{}</style>")

    def test_the_transform_still_runs(self):
        self.dev(True)
        page = pages.Page(self.path, lambda src: src.upper())
        self.assertEqual(page(), "<P>FIRST</P>")

    def test_a_broken_edit_raises_at_call_not_at_construction(self):
        """The documented cost of dev reload: a bad edit is a 500 on refresh
        instead of a startup failure. Stated as a test so it stays deliberate."""
        self.dev(True)
        def refuse(_src):
            raise ValueError("no placeholder")
        page = pages.Page(self.path, refuse)  # construction is fine
        with self.assertRaises(ValueError):
            page()


class TheRealPagesUseIt(unittest.TestCase):
    """Every served page goes through Page — otherwise the flag would silently
    skip whichever one was wired by hand."""

    def test_all_six_pages_are_page_objects(self):
        from hearth.control import control as control_mod
        from hearth.supervisor import curation, roster, routes, settings

        for name, obj in (("control", control_mod._HTML),
                          ("launch", routes._LAUNCH_PAGE),
                          ("pair", routes._PAIR_PAGE),
                          ("roster", roster._PAGE),
                          ("settings", settings._PAGE),
                          ("memory", curation._PAGE)):
            with self.subTest(page=name):
                self.assertIsInstance(obj, pages.Page)
                self.assertTrue(obj.path.is_file(), f"{name} page file missing")
                self.assertIn("<", obj(), "page did not render")


if __name__ == "__main__":
    unittest.main()
