"""The Stop card's two pure helpers: what the button says, and the name a
kept conversation is offered by default.

Both are cut out of the launch page and run under node, in the same spirit as
test_status_line.py — no DOM, no clock of their own, so a case is a one-line
assertion about the string that comes back.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from hearth.supervisor import routes as routes_mod

NODE = shutil.which("node")

STOP_BUTTON_LABEL_RE = re.compile(
    r"function stopButtonLabel\(keep, label\) \{.*?\n\}", re.S)
DEFAULT_LABEL_RE = re.compile(
    r"function defaultLabel\(character, when\) \{.*?\n\}", re.S)


@unittest.skipUnless(NODE, "node not installed — stop-card node tests skipped")
class StopCardNode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _cut(self, pattern, name):
        page = routes_mod._LAUNCH_PAGE()
        match = pattern.search(page)
        self.assertIsNotNone(match, f"{name} not found in the launch page")
        return match.group(0)

    def test_stop_button_label_and_default_label(self):
        script = self.dir / "stop_card.js"
        script.write_text(
            self._cut(STOP_BUTTON_LABEL_RE, "stopButtonLabel") + "\n" +
            self._cut(DEFAULT_LABEL_RE, "defaultLabel") + "\n"
            "const results = {};\n"
            'results.delete = stopButtonLabel(false, "x");\n'
            'results.keep = stopButtonLabel(true, "Example · 2026-09-15 03:10");\n'
            "results.label = defaultLabel(\"Example\", new Date(2026, 8, 15, 3, 10));\n"
            "console.log(JSON.stringify(results));\n",
            encoding="utf-8")
        r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        results = json.loads(r.stdout)
        self.assertEqual(results["delete"], "Stop and delete")
        self.assertEqual(results["keep"], 'Stop and keep "Example · 2026-09-15 03:10"')
        self.assertEqual(results["label"], "Example · 2026-09-15 03:10")


if __name__ == "__main__":
    unittest.main()
