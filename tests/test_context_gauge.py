"""The context gauge's warning speaks in plain words, in two tones.

Phase 1 gauged token pressure against the model's measured reliable line but
spoke only in numbers, one tone, and jargon ("consolidating"). This pins:
the two named thresholds and the pure zone function they feed panel_status's
poll loop, the plain-words sentence per zone (amber "warn", red "over"), and
the two CSS rules that carry the tones. `contextZone` itself is checked under
Node, the same way test_status_line.py checks `clearOnEdge`.

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

from hearth.control import control as control_mod
from hearth.ui import panel as panel_mod

NODE = shutil.which("node")

PANEL_STATUS_JS = (Path(panel_mod.__file__).parent / "panel_status.js").read_text(encoding="utf-8")
PANEL_STYLE_CSS = (Path(panel_mod.__file__).parent / "panel_style.css").read_text(encoding="utf-8")
CONTROL_PAGE_HTML = (Path(control_mod.__file__).parent / "control_page.html").read_text(encoding="utf-8")

WARN_SENTENCE = ("⚠ Getting close to where the model stops working reliably "
                  "— a good moment to wrap up, or start a fresh conversation.")
OVER_SENTENCE = ("⚠ Past where the model works reliably "
                  "— wrap up now, or start a fresh conversation.")

CONTEXT_ZONE_RE = re.compile(
    r"const CTX_WARN_AT.*?\nconst CTX_OVER_AT.*?\nfunction contextZone\(held, budget\) \{.*?\n\}",
    re.S)


class ContextGaugeCopyStatic(unittest.TestCase):

    def test_thresholds_named_once_each(self):
        self.assertEqual(PANEL_STATUS_JS.count("CTX_WARN_AT ="), 1)
        self.assertEqual(PANEL_STATUS_JS.count("CTX_OVER_AT ="), 1)

    def test_no_bare_threshold_in_poll_usage(self):
        m = re.search(r"async function pollUsage\(\) \{.*?\n\}", PANEL_STATUS_JS, re.S)
        self.assertIsNotNone(m, "pollUsage not found")
        self.assertNotIn("0.75", m.group(0))

    def test_sentences_present_verbatim_in_js(self):
        self.assertIn(WARN_SENTENCE, PANEL_STATUS_JS)
        self.assertIn(OVER_SENTENCE, PANEL_STATUS_JS)

    def test_html_ctxwarn_default_text_is_warn_sentence(self):
        lines = CONTROL_PAGE_HTML.splitlines()
        line74 = lines[73]
        self.assertIn('id="ctxwarn"', line74)
        self.assertIn(WARN_SENTENCE, line74)

    def test_no_jargon_left(self):
        self.assertNotIn("consolidating", PANEL_STATUS_JS)
        self.assertNotIn("consolidating", CONTROL_PAGE_HTML)

    def test_css_tone_rules_present(self):
        self.assertIn("#ctxwarn.warn{color:#e8b04b}", PANEL_STYLE_CSS)
        self.assertIn("#ctxwarn.over{color:#e5534b}", PANEL_STYLE_CSS)


@unittest.skipUnless(NODE, "node not installed — context gauge node tests skipped")
class ContextZoneNode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_context_zone_cases(self):
        match = CONTEXT_ZONE_RE.search(PANEL_STATUS_JS)
        self.assertIsNotNone(match, "contextZone (with its constants) not found in panel_status.js")
        cases = [
            (50, None, "na"),
            (0, 100, "ok"),
            (74, 100, "ok"),
            (75, 100, "warn"),
            (99, 100, "warn"),
            (100, 100, "over"),
            (50, 0, "na"),
        ]
        inputs = [c[:2] for c in cases]
        script = self.dir / "context_zone.js"
        script.write_text(
            match.group(0) + "\n" +
            "const inputs = " + json.dumps(inputs) + ";\n"
            "console.log(JSON.stringify(inputs.map(c => contextZone(c[0], c[1]))));\n",
            encoding="utf-8")
        r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        results = json.loads(r.stdout)
        for (held, budget, want), got in zip(cases, results):
            with self.subTest(held=held, budget=budget):
                self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
