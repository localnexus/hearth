"""test_engine_mismatch.py — the panel says, in words, when the model server
serves a different model than the sitting was configured for.

Static: the pure helper and the sentence are in panel_status.js; the page
carries the line; the CSS tones it. Node: engineMismatch's cases. Python: the
identity_facts helper bot.py and its re-poll share.

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
from hearth.pipeline import model_residency
from hearth.ui import panel as panel_mod

NODE = shutil.which("node")
PANEL_STATUS_JS = (Path(panel_mod.__file__).parent / "panel_status.js").read_text(encoding="utf-8")
PANEL_STYLE_CSS = (Path(panel_mod.__file__).parent / "panel_style.css").read_text(encoding="utf-8")
CONTROL_PAGE_HTML = (Path(control_mod.__file__).parent / "control_page.html").read_text(encoding="utf-8")

MISMATCH_RE = re.compile(r"function engineMismatch\(eng\) \{.*?\n\}", re.S)


class IdentityFacts(unittest.TestCase):

    def test_no_answer_is_unknown_not_a_mismatch(self):
        self.assertEqual(model_residency.identity_facts(None, "a"),
                         {"served_model": None, "model_match": None})

    def test_served_and_matched(self):
        self.assertEqual(model_residency.identity_facts(["a"], "a"),
                         {"served_model": "a", "model_match": True})
        self.assertEqual(model_residency.identity_facts(["b", "a"], "a"),
                         {"served_model": "b", "model_match": True})

    def test_served_something_else(self):
        self.assertEqual(model_residency.identity_facts(["b"], "a"),
                         {"served_model": "b", "model_match": False})
        self.assertEqual(model_residency.identity_facts([], "a"),
                         {"served_model": None, "model_match": False})


class MismatchStatic(unittest.TestCase):

    def test_helper_and_sentence_present(self):
        self.assertIsNotNone(MISMATCH_RE.search(PANEL_STATUS_JS))
        self.assertIn("⚠ The model server is serving ${eng.served_model", PANEL_STATUS_JS)
        self.assertIn("Apply its row, then press Load", PANEL_STATUS_JS)
        self.assertIn("not the configured model", PANEL_STATUS_JS)

    def test_page_carries_the_line_after_ctxwarn(self):
        lines = CONTROL_PAGE_HTML.splitlines()
        self.assertIn('id="ctxwarn"', lines[73])       # the gauge test pins it there
        self.assertIn('id="enginewarn" class="hidden"', lines[74])

    def test_css_tone_rule_present(self):
        self.assertIn("#enginewarn{", PANEL_STYLE_CSS)
        self.assertIn("color:#e8b04b", PANEL_STYLE_CSS.split("#enginewarn{", 1)[1].split("}", 1)[0])


@unittest.skipUnless(NODE, "node not installed — engine mismatch node tests skipped")
class MismatchNode(unittest.TestCase):

    def test_cases(self):
        match = MISMATCH_RE.search(PANEL_STATUS_JS)
        self.assertIsNotNone(match)
        cases = [
            ({"model_match": True, "served_model": "a", "configured_model": "a"}, None),
            ({"model_match": None, "served_model": None, "configured_model": "a"}, None),
            ({}, None),
            (None, None),
            ({"model_match": False, "served_model": "b", "configured_model": "a"}, "b|a"),
            ({"model_match": False, "served_model": None, "configured_model": "a"}, "something else|a"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "mismatch.js"
            script.write_text(
                "const DASH = '—';\n" + match.group(0) + "\n"
                "const inputs = " + json.dumps([c[0] for c in cases]) + ";\n"
                "console.log(JSON.stringify(inputs.map(engineMismatch)));\n",
                encoding="utf-8")
            r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        results = json.loads(r.stdout)
        for (eng, want), got in zip(cases, results):
            with self.subTest(eng=eng):
                if want is None:
                    self.assertIsNone(got)
                else:
                    served, configured = want.split("|")
                    self.assertTrue(got.startswith("⚠ The model server is serving " + served), got)
                    self.assertIn("set up for " + configured, got)


if __name__ == "__main__":
    unittest.main()
