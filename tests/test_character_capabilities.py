"""test_character_capabilities.py — the per-character tool grant ("no hands by
default").

characters/<c>/capabilities.toml decides whether THIS character gets the
dispatch tools at all. Proves, on scratch roots only (throwaway character
names, never a real one):

  1. LOADER   — absent file reads "none"; each of the four tier words reads
                back; malformed TOML, an unknown tier, a missing [tools] table
                and a non-string tier all read "none" and say why once.
  2. GATE     — openclaw_effective() is empty when the install switch is off,
                empty when the switch is on and the character is "none", and
                carries the config plus the granted tier otherwise. One
                function, so tools and prompt can never disagree.
  3. SLOT     — the {{openclaw_tools}} paragraph renders empty for a "none"
                character even with enabled=true, and fills for a granted one.
  4. REGISTRY — the kind is declared, discovered by config.check, and NOT in
                the settings page's writable set.
  5. PANEL    — the Engine line's hands item: off / none / read-only.

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
from unittest import mock

from hearth.config import check
from hearth.config import config_loader as cl
from hearth.config import settings_registry as sr
from hearth.supervisor.settings import policy as settings_policy
from hearth.ui import panel as panel_mod

from scratch_root import patch_data_root

NODE = shutil.which("node")
PANEL_STATUS_JS = (Path(panel_mod.__file__).parent / "panel_status.js").read_text(encoding="utf-8")
HANDS_RE = re.compile(r"function handsWord\(eng\) \{.*?\n\}", re.S)

_CHAR = "zed"          # throwaway character name; nothing real is ever touched
_MODEL = "zz-model"
_BLOCK = "You have hands: dispatch_task and check_tasks."


class _Sink:
    """Captures loguru lines for the duration of a with-block."""

    def __enter__(self):
        from loguru import logger

        self.lines: list[str] = []
        self._logger = logger
        self._id = logger.add(lambda msg: self.lines.append(str(msg)))
        return self

    def __exit__(self, *exc):
        self._logger.remove(self._id)
        return False

    def grants(self) -> list[str]:
        return [line for line in self.lines if "[capabilities]" in line]


class _ScratchRoots(unittest.TestCase):
    """A synthetic data root: a characters/ tree, a model template with the
    slot, and an openclaw.toml this test owns."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        patch_data_root(self, self.tmp)
        chars = self.tmp / "characters"
        (chars / _CHAR).mkdir(parents=True)
        self.openclaw = self.tmp / "config" / "openclaw.toml"
        self.openclaw.parent.mkdir(parents=True, exist_ok=True)
        for name, value in (("CHARACTERS_DIR", chars),
                            ("CONFIG_DIR", self.tmp / "config"),
                            ("OPENCLAW_TOML", self.openclaw)):
            p = mock.patch.object(cl, name, value)
            p.start()
            self.addCleanup(p.stop)

    def grant(self, text: str, character: str = _CHAR) -> Path:
        path = cl.CHARACTERS_DIR / character / "capabilities.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def bridge_on(self, enabled: bool = True) -> None:
        self.openclaw.write_text(
            f"[openclaw]\nenabled = {str(enabled).lower()}\n"
            f'prompt_block = "{_BLOCK}"\n', encoding="utf-8")


class CapabilitiesLoader(_ScratchRoots):
    """Default-deny, and fail closed on every unreadable shape."""

    def test_absent_file_is_none_and_silent(self):
        with _Sink() as sink:
            self.assertEqual(cl.load_character_capabilities(_CHAR), "none")
        self.assertEqual(sink.grants(), [])  # the ordinary case is not a warning

    def test_each_tier_reads_back(self):
        for tier in cl.TOOL_TIERS:
            with self.subTest(tier=tier):
                self.grant(f'[tools]\ntier = "{tier}"\n')
                with _Sink() as sink:
                    self.assertEqual(cl.load_character_capabilities(_CHAR), tier)
                self.assertEqual(sink.grants(), [])

    def test_malformed_toml_is_none_and_warns(self):
        path = self.grant("[tools\ntier = read-only\n")
        with _Sink() as sink:
            self.assertEqual(cl.load_character_capabilities(_CHAR), "none")
        self.assertEqual(len(sink.grants()), 1)
        self.assertIn(str(path), sink.grants()[0])
        self.assertIn("unreadable", sink.grants()[0])

    def test_unknown_tier_is_none_and_warns(self):
        path = self.grant('[tools]\ntier = "root"\n')
        with _Sink() as sink:
            self.assertEqual(cl.load_character_capabilities(_CHAR), "none")
        self.assertEqual(len(sink.grants()), 1)
        self.assertIn(str(path), sink.grants()[0])
        self.assertIn("'root'", sink.grants()[0])

    def test_tier_not_a_string_is_none(self):
        self.grant("[tools]\ntier = true\n")
        with _Sink() as sink:
            self.assertEqual(cl.load_character_capabilities(_CHAR), "none")
        self.assertEqual(len(sink.grants()), 1)

    def test_no_tools_table_is_none_and_warns(self):
        self.grant('[hands]\ntier = "full"\n')
        with _Sink() as sink:
            self.assertEqual(cl.load_character_capabilities(_CHAR), "none")
        self.assertIn("[tools]", sink.grants()[0])

    def test_unusable_character_name_is_none(self):
        with _Sink() as sink:
            self.assertEqual(cl.load_character_capabilities("../elsewhere"), "none")
            self.assertEqual(cl.load_character_capabilities(""), "none")
        self.assertEqual(len(sink.grants()), 2)

    def test_the_grant_is_read_from_the_data_root_only(self):
        """A file in the engine tree must never hand a character hands."""
        self.assertEqual(cl.capabilities_path(_CHAR),
                         self.tmp / "characters" / _CHAR / "capabilities.toml")


class EffectiveGate(_ScratchRoots):
    """One function both consumers ask — so they cannot disagree."""

    def test_empty_when_the_switch_is_off(self):
        self.bridge_on(False)
        self.grant('[tools]\ntier = "full"\n')
        self.assertEqual(cl.openclaw_effective(_CHAR), {})

    def test_empty_when_enabled_but_the_character_is_none(self):
        self.bridge_on()
        self.assertEqual(cl.openclaw_effective(_CHAR), {})        # absent file
        self.grant('[tools]\ntier = "none"\n')
        self.assertEqual(cl.openclaw_effective(_CHAR), {})        # said so plainly

    def test_the_config_plus_the_tier_when_granted(self):
        self.bridge_on()
        self.grant('[tools]\ntier = "read-only"\n')
        cfg = cl.openclaw_effective(_CHAR)
        self.assertTrue(cfg)
        self.assertEqual(cfg["tier"], "read-only")
        self.assertEqual(cfg["agent"], "hands")                   # the openclaw defaults
        self.assertEqual(cfg["prompt_block"], _BLOCK)

    def test_hands_label_for_the_panel(self):
        self.assertEqual(cl.hands_label(_CHAR), "off")            # no openclaw.toml
        self.bridge_on(False)
        self.assertEqual(cl.hands_label(_CHAR), "off")
        self.bridge_on()
        self.assertEqual(cl.hands_label(_CHAR), "none")
        self.grant('[tools]\ntier = "read-only"\n')
        self.assertEqual(cl.hands_label(_CHAR), "read-only")


class PromptSlot(_ScratchRoots):
    """The capability paragraph follows the same gate as the tools."""

    TEMPLATE = "SYSTEM RULES\n\n{{openclaw_tools}}\n\n{{persona}}\n"

    def setUp(self):
        super().setUp()
        tpl = self.tmp / "config" / "models" / _MODEL
        tpl.mkdir(parents=True)
        (tpl / "system-prompt-template.md").write_text(self.TEMPLATE, encoding="utf-8")

    def _composed(self, character):
        return cl.compose_with_persona(_MODEL, "PERSONA", datetime_str="",
                                       character=character)

    def test_empty_with_enabled_and_a_none_character(self):
        self.bridge_on()
        composed = self._composed(_CHAR)
        self.assertNotIn(_BLOCK, composed)
        self.assertNotIn("{{openclaw_tools}}", composed)
        self.assertEqual(composed, "SYSTEM RULES\n\nPERSONA")  # byte-identical to bridge-off

    def test_filled_for_a_granted_character(self):
        self.bridge_on()
        self.grant('[tools]\ntier = "read-only"\n')
        self.assertIn(_BLOCK, self._composed(_CHAR))

    def test_the_switch_alone_does_not_fill_it(self):
        """Capability and prompt cannot disagree: enabled=true with a character
        that was never granted leaves the paragraph out."""
        self.bridge_on()
        self.grant('[tools]\ntier = "none"\n')
        self.assertNotIn(_BLOCK, self._composed(_CHAR))


class RegistryKind(_ScratchRoots):
    """Declared, discovered, documented — and never form-writable."""

    def test_entry_facts(self):
        entry = sr.REGISTRY["capabilities"]
        self.assertEqual(entry.path, "characters/<character>/capabilities.toml")
        self.assertEqual((entry.layer, entry.restart, entry.top_key),
                         ("identity", "bot", "tools"))
        self.assertEqual(entry.model.model_fields["tier"].default, "none")

    def test_package_reexports_the_model(self):
        self.assertIs(sr.CapabilitiesFile, sr.REGISTRY["capabilities"].model)

    def test_discovered_and_validated(self):
        path = self.grant('[tools]\ntier = "read-only"\n')
        found = [p for kind, p in check.discover() if kind == "capabilities"]
        self.assertIn(path.resolve(), found)
        self.assertEqual(check.check_file("capabilities", path), ("ok", [], []))

    def test_an_unknown_tier_is_loud_under_the_strict_check(self):
        path = self.grant('[tools]\ntier = "root"\n')
        verdict, errors, _ = check.check_file("capabilities", path)
        self.assertEqual(verdict, "INVALID")
        self.assertTrue(errors)

    def test_not_form_writable(self):
        self.assertNotIn("capabilities", settings_policy._WRITABLE)


@unittest.skipUnless(NODE, "node not installed — hands-item node test skipped")
class PanelHandsItem(unittest.TestCase):
    """The Engine line's one-word hands item, cut out of the panel and run
    under node — no DOM, so a case is one assertion about a string."""

    def test_helper_and_line_present(self):
        self.assertIsNotNone(HANDS_RE.search(PANEL_STATUS_JS))
        self.assertIn("· hands: ${handsWord(engine)}", PANEL_STATUS_JS)

    def test_cases(self):
        match = HANDS_RE.search(PANEL_STATUS_JS)
        self.assertIsNotNone(match)
        inputs = [{"hands": "off"}, {"hands": "none"}, {"hands": "read-only"},
                  {}, None]
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "hands.js"
            script.write_text(
                "const DASH = '—';\n" + match.group(0) + "\n"
                "const inputs = " + json.dumps(inputs) + ";\n"
                "console.log(JSON.stringify(inputs.map(handsWord)));\n",
                encoding="utf-8")
            r = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        self.assertEqual(json.loads(r.stdout),
                         ["off", "none", "read-only", "—", "—"])


if __name__ == "__main__":
    unittest.main()
