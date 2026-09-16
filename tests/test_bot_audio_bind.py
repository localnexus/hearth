"""bot.py's audio bind, checked statically — including the import fence.

`bot.py` cannot be imported here (it loads the active selection at module
scope, and a test tree has no companion selected), so these are AST checks over
its source, in the same spirit as test_bot_wiring.py. That is not a weakness
for the thing being checked: the fence this file exists for is precisely a
statement about WHERE an import is written.

The fence, restated, because it is the sharpest hazard in the change: the desk
transport's recovery is a gate over PROCESS-level PortAudio state. A remote
sitting has no local device and must never put that state in play. So
`RecoveringLocalAudioTransport` and `LocalAudioTransportParams` must be
imported INSIDE the desk branch, not at module scope — otherwise every remote
sitting initialises PortAudio on the way past, and nothing in a passing test
suite would say so.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import hearth

SOURCE = Path(hearth.__file__).parent / "pipeline" / "bot.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))

DESK_IMPORTS = ("RecoveringLocalAudioTransport", "LocalAudioTransportParams",
                "recovering_transport", "pa_pool", "device_pin")


def _func(name: str):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in {SOURCE.name}")


def _imported_names(node) -> set:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom):
            names.add(child.module or "")
            names |= {alias.name for alias in child.names}
        elif isinstance(child, ast.Import):
            names |= {alias.name for alias in child.names}
    return names


class TheDeskImportsSitInsideTheDeskBranch(unittest.TestCase):

    def test_no_desk_audio_module_is_imported_at_module_scope(self):
        top = set()
        for node in TREE.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                top |= _imported_names(node)
        flat = " ".join(top)
        for name in DESK_IMPORTS:
            with self.subTest(name=name):
                self.assertNotIn(
                    name, flat,
                    f"{name} is imported at module scope — every remote sitting "
                    "would initialise PortAudio on the way past")

    def test_the_bind_imports_them_itself(self):
        """Not merely absent from the top: present where the desk branch runs,
        so 'moved out' cannot quietly become 'deleted'."""
        names = _imported_names(_func("build_transport"))
        self.assertIn("RecoveringLocalAudioTransport", names)
        self.assertIn("LocalAudioTransportParams", names)

    def test_the_remote_branch_calls_the_factory_the_tests_exercise(self):
        names = _imported_names(_func("build_transport"))
        self.assertIn("build_remote_transport", names,
                      "the bind must build the remote route through the same "
                      "factory the transport tests drive")

    def test_the_desk_construction_is_unchanged(self):
        """The desk keeps exactly the four parameters it had before the route
        word existed: this change is a branch, not a re-tuning."""
        source = ast.get_source_segment(
            SOURCE.read_text(encoding="utf-8"), _func("build_transport"))
        for line in ("audio_in_enabled=True", "audio_out_enabled=True",
                     "audio_in_sample_rate=16000",
                     "audio_out_sample_rate=SAMPLE_RATE"):
            with self.subTest(line=line):
                self.assertIn(line, source)


class TheFlagAndTheGuard(unittest.TestCase):

    def setUp(self):
        self.source = SOURCE.read_text(encoding="utf-8")

    def test_the_flag_is_audio_and_defaults_to_the_desk(self):
        self.assertIn('"--audio"', self.source)
        self.assertIn("default=audio_route.DESK", self.source)

    def test_the_flag_reaches_main_and_main_reaches_the_bind(self):
        self.assertIn("route=args.audio", self.source)
        self.assertIn("route=route", self.source)
        self.assertIn("build_transport(route)", self.source)

    def test_a_remote_sitting_without_the_key_refuses_to_start(self):
        """The exact line, because an operator greps for it — and the non-zero
        exit, because a sitting that came up anyway would be an ungated audio
        socket on the overlay network."""
        self.assertIn(
            "[audio] remote route needs config/serve-token — not starting",
            self.source)
        self.assertIn("raise SystemExit(2)", self.source)

    def test_the_route_object_is_attached_for_both_routes(self):
        self.assertIn("hearth.control.features.audio_route.attach(route_state)",
                      self.source)

    def test_the_reason_a_sitting_closed_itself_survives_the_close_ladder(self):
        """The seam, read where it has to be true: `main()` answers the status
        AFTER its `finally` — so the whole close runs first, exactly as the
        Stop button's does — and the entry point turns that into the exit. A
        sitting that closed itself over a device that never came back is the
        only completed run that exits non-zero.
        """
        main = _func("main")
        returns = [node for node in main.body if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1,
                         "one answer, and it is the last thing main does")
        self.assertEqual(ast.unparse(returns[0]),
                         "return audio_route.exit_status()")
        self.assertIs(main.body[-1], returns[0],
                      "after the ladder, not inside it")
        self.assertIn("_status = asyncio.run(main(", self.source)
        self.assertIn("raise SystemExit(_status)", self.source)

    def test_nothing_in_the_bot_writes_the_number_out(self):
        """Three is named once, in the module with no imports, because the
        supervisor has to read it without loading a pipeline. A literal here
        would be the second place it is written down and the first place it
        would go stale."""
        self.assertNotIn("SystemExit(3)", self.source)

    def test_the_device_pin_tap_is_attached_only_on_the_desk(self):
        """`/presence.audio` reads the desk transport's per-direction device
        state; a WebSocket has none, and handing it over would be a status
        route that raises rather than one that says 'unpinned'."""
        body = ast.get_source_segment(self.source, _func("build_pipeline"))
        attach = [line for line in body.splitlines()
                  if "presence.attach_audio(transport)" in line]
        self.assertEqual(len(attach), 1, attach)
        indent = len(attach[0]) - len(attach[0].lstrip())
        self.assertGreater(indent, 4, "attach_audio is not inside the desk guard")


if __name__ == "__main__":
    unittest.main()
