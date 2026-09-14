"""test_session_locator.py — [session] dir configurability (config side of the
save-to-path locator work; the pure locator functions live in test_session_store.py).

Proves, on the real artifacts:
  1. `[session] dir` in config/active.toml applies to the ACTIVE companion only —
     every other companion keeps its built-in per-companion dir (re-anchoring,
     2026-09-13).
  2. absent `[session]` table -> None -> the built-in per-companion dir.
  3. a relative `[session] dir` resolves under the data root.

Run:  .venv/bin/python -m unittest tests.test_session_locator
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hearth.config import config_loader as cl
from hearth.session import session_store as ss


class _RelocatedDataRoot(unittest.TestCase):
    """Shared fixture: relocate every anchor config_loader/session_store read the
    data root through, the way test_data_root.py's own fixtures do — patching
    `_DATA` alone leaves `CONFIG_DIR` / `ACTIVE_TOML` pointed at the OLD root,
    since those are computed once at import time."""

    def setUp(self):
        self._orig = (cl._DATA, cl.DATA_DIR, cl.CONFIG_DIR, cl.ACTIVE_TOML)
        self._tmp = tempfile.TemporaryDirectory()
        data = Path(self._tmp.name)
        cl._DATA = data
        cl.DATA_DIR = data
        cl.CONFIG_DIR = data / "config"
        cl.ACTIVE_TOML = cl.CONFIG_DIR / "active.toml"
        cl.CONFIG_DIR.mkdir(parents=True)
        self.data = data

    def tearDown(self):
        cl._DATA, cl.DATA_DIR, cl.CONFIG_DIR, cl.ACTIVE_TOML = self._orig
        self._tmp.cleanup()

    def _write_active(self, extra: str = "") -> None:
        cl.ACTIVE_TOML.write_text(
            'character = "demo"\nmodel = "example"\nvoice = "default"\n' + extra)


class DefaultSessionsDirActiveOnly(_RelocatedDataRoot):
    def test_active_companion_honors_configured_dir(self):
        elsewhere = self.data / "sessions-elsewhere"
        self._write_active(f'\n[session]\ndir = "{elsewhere.name}"\n')
        self.assertEqual(cl.load_active_session_dir(), elsewhere)
        self.assertEqual(ss.default_sessions_dir(), elsewhere)
        self.assertEqual(ss.default_sessions_dir("demo"), elsewhere)

    def test_other_companion_keeps_its_own_dir(self):
        elsewhere = self.data / "sessions-elsewhere"
        self._write_active(f'\n[session]\ndir = "{elsewhere.name}"\n')
        other = ss.default_sessions_dir("zz-other")
        self.assertEqual(other, self.data / "characters" / "zz-other" / "sessions")

    def test_absent_session_table_falls_back_to_companion_dir(self):
        self._write_active()
        self.assertIsNone(cl.load_active_session_dir())
        self.assertEqual(ss.default_sessions_dir("demo"),
                         self.data / "characters" / "demo" / "sessions")
        self.assertEqual(ss.default_sessions_dir(),
                         self.data / "characters" / "demo" / "sessions")

    def test_relative_session_dir_resolves_under_data_root(self):
        self._write_active('\n[session]\ndir = "tucked-away/sessions"\n')
        self.assertEqual(cl.load_active_session_dir(), self.data / "tucked-away" / "sessions")
        self.assertEqual(ss.default_sessions_dir(), self.data / "tucked-away" / "sessions")


if __name__ == "__main__":
    unittest.main()
