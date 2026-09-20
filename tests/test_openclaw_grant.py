"""test_openclaw_grant.py — the bridge attach is the enforcement point.

The grant decides whether the two dispatch tools are ever put into the
character's request. With the install switch on and a character whose grant is
"none", maybe_attach must be byte-identical to the bridge being off: no
register_function, no set_tools, no bridge. With a grant, it attaches and the
tier rides on the bridge and into the log line.

A fake llm and context record every call, so "registered nothing" is an
assertion rather than a reading of the code. Nothing here touches a gateway.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth.bridges import openclaw_bridge as ob
from hearth.config import config_loader as cl

from scratch_root import patch_data_root

_CHAR = "zed"  # throwaway character name


class _Llm:
    def __init__(self):
        self.registered: list[str] = []

    def register_function(self, name, handler):
        self.registered.append(name)


class _Context:
    def __init__(self):
        self.tools = None
        self.set_tools_calls = 0

    def set_tools(self, tools):
        self.set_tools_calls += 1
        self.tools = tools


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

    def find(self, fragment: str) -> list[str]:
        return [line for line in self.lines if fragment in line]


class AttachGate(unittest.TestCase):
    """enabled=true is no longer enough — the character must be granted."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        patch_data_root(self, self.tmp)
        chars = self.tmp / "characters"
        (chars / _CHAR).mkdir(parents=True)
        self.openclaw = self.tmp / "config" / "openclaw.toml"
        self.openclaw.parent.mkdir(parents=True, exist_ok=True)
        self.openclaw.write_text("[openclaw]\nenabled = true\n", encoding="utf-8")
        for name, value in (("CHARACTERS_DIR", chars),
                            ("CONFIG_DIR", self.tmp / "config"),
                            ("OPENCLAW_TOML", self.openclaw)):
            p = mock.patch.object(cl, name, value)
            p.start()
            self.addCleanup(p.stop)
        # A token so the bridge can be built without a token file, and no
        # pre-warm: this test never speaks to a gateway.
        env = mock.patch.dict("os.environ",
                              {"OPENCLAW_GATEWAY_TOKEN": "zz-not-a-real-token"})
        env.start()
        self.addCleanup(env.stop)
        warm = mock.patch.object(ob.OpenClawBridge, "_schedule_prewarm", lambda self: None)
        warm.start()
        self.addCleanup(warm.stop)

    def grant(self, tier: str) -> None:
        (cl.CHARACTERS_DIR / _CHAR / "capabilities.toml").write_text(
            f'[tools]\ntier = "{tier}"\n', encoding="utf-8")

    def test_enabled_but_ungranted_registers_nothing(self):
        for label, prepare in (("absent grant", lambda: None),
                               ("tier none", lambda: self.grant("none"))):
            with self.subTest(case=label):
                prepare()
                llm, context = _Llm(), _Context()
                with _Sink() as sink:
                    self.assertIsNone(ob.maybe_attach(llm, context, character=_CHAR))
                self.assertEqual(llm.registered, [])
                self.assertEqual(context.set_tools_calls, 0)
                self.assertIsNone(context.tools)
                self.assertEqual(sink.find("bridge attached"), [])

    def test_granted_character_attaches_with_its_tier(self):
        self.grant("read-only")
        llm, context = _Llm(), _Context()
        with _Sink() as sink:
            bridge = ob.maybe_attach(llm, context, character=_CHAR)
        self.assertIsNotNone(bridge)
        self.assertEqual(bridge.tier, "read-only")
        self.assertEqual(llm.registered, ["dispatch_task", "check_tasks"])
        self.assertEqual(context.set_tools_calls, 1)
        self.assertEqual(
            [t.name for t in context.tools.standard_tools],
            ["dispatch_task", "check_tasks"])
        line = sink.find("bridge attached")
        self.assertEqual(len(line), 1)
        self.assertIn("tier=read-only", line[0])

    def test_the_switch_off_still_short_circuits(self):
        self.openclaw.write_text("[openclaw]\nenabled = false\n", encoding="utf-8")
        self.grant("full")
        llm, context = _Llm(), _Context()
        self.assertIsNone(ob.maybe_attach(llm, context, character=_CHAR))
        self.assertEqual(llm.registered, [])

    def test_two_arg_call_resolves_the_selected_character(self):
        """The old signature keeps working: with no character passed, the
        selection decides — and an unresolvable selection grants nothing."""
        self.grant("full")
        llm, context = _Llm(), _Context()
        self.assertIsNone(ob.maybe_attach(llm, context))  # no active.toml here
        self.assertEqual(llm.registered, [])

        (self.tmp / "config" / "active.toml").write_text(
            f'character = "{_CHAR}"\nmodel = "zz-m"\nvoice = "zz-v"\n', encoding="utf-8")
        with mock.patch.object(cl, "ACTIVE_TOML", self.tmp / "config" / "active.toml"):
            bridge = ob.maybe_attach(llm, context)
        self.assertIsNotNone(bridge)
        self.assertEqual(bridge.tier, "full")


if __name__ == "__main__":
    unittest.main()
