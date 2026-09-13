"""test_scratch_root.py — proves patch_data_root closes the fixture-lock gap.

(a) both config_loader names move together and every call-time anchor
(maintenance_lock, close_row, session_store) lands under the tmp root; (b) a
lock held during the test is dropped by cleanup, verified via doCleanups();
(c) every file that relocates the data root uses the helper and nothing else.

Run:  .venv/bin/python -m unittest tests.test_scratch_root
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hearth.config import config_loader
from hearth.session import close_row, maintenance_lock

from scratch_root import patch_data_root


class PatchDataRootShape(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_both_names_and_call_time_anchors_land_under_root(self):
        returned = patch_data_root(self, self.root)
        self.assertEqual(returned, self.root)
        self.assertEqual(config_loader.DATA_DIR, config_loader._DATA)
        self.assertTrue(maintenance_lock.lock_dir().is_relative_to(self.root))
        self.assertTrue(close_row.ledger_dir().is_relative_to(self.root))
        self.assertTrue((config_loader._DATA / "characters").is_relative_to(self.root))


class PatchDataRootLockCleanup(unittest.TestCase):
    def test_hold_lands_under_root_and_drops_on_cleanup(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        patch_data_root(self, root)

        self.assertTrue(maintenance_lock.hold("zz-fixture", op="session"))
        lock_path = maintenance_lock.lock_path("zz-fixture")
        self.assertTrue(lock_path.is_relative_to(root))
        self.assertTrue(lock_path.exists())
        self.assertIn("zz-fixture", maintenance_lock._HELD)

        # Drive this test's own registered cleanups now (rather than waiting for
        # the framework to run them after the test returns) so we can assert on
        # post-cleanup state within the test body itself.
        self.doCleanups()
        self.assertEqual(maintenance_lock._HELD, {})


_RELOCATING_FILES = [
    "test_live_switch.py",
    "test_profile_voice_pin.py",
    "test_switch.py",
    "test_maintenance_lock.py",
    "supervisor/test_roster_voices.py",
    "supervisor/test_curation_views.py",
    "supervisor/test_roster_persona.py",
    "supervisor/test_curation_forget.py",
    "supervisor/test_roster_onboarding.py",
    "supervisor/test_first_run.py",
    "supervisor/test_admin_routes.py",
    "supervisor/test_compact_watch.py",
    "supervisor/test_compact_routes.py",
]


class RelocatingFilesUseTheHelperOnly(unittest.TestCase):
    def test_every_relocating_file_uses_patch_data_root_only(self):
        here = Path(__file__).parent
        for rel in _RELOCATING_FILES:
            text = (here / rel).read_text(encoding="utf-8")
            with self.subTest(file=rel):
                self.assertIn("patch_data_root(", text)
                self.assertNotIn('patch.object(config_loader, "_DATA"', text)
                self.assertNotIn('patch.object(config_loader, "DATA_DIR"', text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
