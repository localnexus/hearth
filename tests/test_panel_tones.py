"""The control panel's status lines wear the launch page's three tones.

The shared switch card (ui/switch_card.js) already speaks warn/err/ok via
say(msg, tone). The launch page styles those classes; the panel's own
stylesheet (ui/panel_style.css) had no rule for any of them, so on the panel
the card's tones rendered as plain text, and the panel's own status() line
(control_page.html) had no tone parameter at all. This pins: the panel
palette carries the three classes, status() accepts and applies a tone, the
error/failure call sites pass 'err', and the switch-card mount hands the
card its base 'state' class.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "hearth"
CSS = (SRC / "ui" / "panel_style.css").read_text(encoding="utf-8")
PAGE = (SRC / "control" / "control_page.html").read_text(encoding="utf-8")

NODE = shutil.which("node")


class PanelTonesStatic(unittest.TestCase):

    def test_css_carries_the_three_tones_and_state(self):
        for selector in (".state{", ".warn{", ".ok{", ".err{"):
            with self.subTest(selector=selector):
                pattern = re.compile(r"^\s*" + re.escape(selector), re.M)
                hits = pattern.findall(CSS)
                self.assertEqual(len(hits), 1,
                                  f"expected exactly one {selector!r} rule, found {len(hits)}")

    def test_status_takes_a_tone_and_sets_classname(self):
        self.assertRegex(PAGE, r"const status = \(msg, tone\) =>")
        self.assertIn("s.className = tone || ''", PAGE)

    def test_every_error_status_call_passes_err(self):
        calls = re.findall(r"status\(\s*(?:j\.ok\s*\?\s*)?'error:[^;]*?\)", PAGE)
        self.assertGreater(len(calls), 0, "no status('error: ...) call sites found")
        for call in calls:
            with self.subTest(call=call):
                self.assertIn("'err'", call)

    def test_mount_hands_the_card_its_base_state_class(self):
        match = re.search(r"cls:\s*\{[^}]*\}", PAGE)
        self.assertIsNotNone(match, "switch-card mount's cls object not found")
        self.assertIn("state: 'state'", match.group(0))


STATUS_RE = re.compile(r"const status = \(msg, tone\) => \{.*?\};", re.S)

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const re = new RegExp(%s, "s");
const match = src.match(re);
if (!match) { console.error("status() not found"); process.exit(1); }

const fake = { textContent: "", className: "" };
global.$ = (id) => fake;

new Function(match[0] + " global.status = status;")();

status("x");
if (fake.className !== "") {
  console.error("plain: got className " + JSON.stringify(fake.className));
  process.exit(1);
}

status("boom", "err");
if (fake.className !== "err") {
  console.error("err: got className " + JSON.stringify(fake.className));
  process.exit(1);
}

console.log("OK");
process.exit(0);
""" % repr(STATUS_RE.pattern)


@unittest.skipUnless(NODE, "node not installed — panel-tones node test skipped")
class PanelTonesNode(unittest.TestCase):

    def test_status_applies_tone_in_a_real_interpreter(self):
        assert STATUS_RE.search(PAGE), "status() source not found in control_page.html"
        with tempfile.TemporaryDirectory() as d:
            page_path = Path(d) / "control_page.html"
            page_path.write_text(PAGE, encoding="utf-8")
            harness_path = Path(d) / "harness.js"
            harness_path.write_text(HARNESS, encoding="utf-8")
            r = subprocess.run([NODE, str(harness_path), str(page_path)],
                                capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr.strip())
            self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
