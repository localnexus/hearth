"""test_weights_guardrails.py — the five rails that keep enrollment from
becoming a dependency on somebody else's product.

They were written as acceptance criteria before the code was (the design record
`arch-model-management-on-the-door.md` §9), and they are checked here by reading
the source rather than by trusting a review:

  R1  the readers touch files and GGUF headers only. No module in the package
      may launch another program except the operator's own door binary and
      `sysctl`; in particular nothing may run `lms`, `ollama` or `lmstudio`. And
      the one product cache directory named anywhere in the package is named in
      the skip list, which is the only place it belongs.
  R2  enrollment stores the resolved real path — see test_weights_scan.py,
      RealPaths (a symlinked root yields real paths); pinned there because it
      needs a filesystem, not an AST.
  R3  missing weights are a reported state, not an exception — see
      test_weights_enroll.py, MissingIsAState.
  R4  no layout reader is imported by the live conversation loop. The pipeline,
      the running program, and every page route must be reachable without
      `hearth.weights` existing at all.
  R1+ (G3) the admin model surface starts nothing either. `supervisor/models/`
      is the one place outside the CLI allowed to import this package (the §9
      amendment), and what it hands the operator is a launchd argv as DATA for
      the actuator frame — a bounded, logged, never-a-child command run by the
      actuator runner. The package itself spawns nothing at all, and R4 below
      is untouched by its existence, because it does not live under routes/.

  R1+ (G2) the renderer starts NOTHING. It writes a launchd unit and prints the
      two launchctl lines; it never runs launchctl, never runs the door, and the
      one path into `~/Library/LaunchAgents` goes through the single injectable
      helper, so a test can never be pointed at the real one by accident.

  R5  a scan is identical with every product absent. The scan tests run with
      none installed by construction; what is checked here is that no product
      SDK could ever be imported.

Run:  .venv/bin/python -m unittest tests.test_weights_guardrails
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = _SRC / "hearth" / "weights"

#: Executables the package may name literally. The door binary is not here
#: because it is never a literal — it comes from config or from PATH.
ALLOWED_LITERAL_EXECUTABLES = {"sysctl"}

#: Command names that would re-create the dependency this design exists to avoid.
FORBIDDEN_EXECUTABLES = ("lms", "ollama", "lmstudio", "ollama-server", "lms-cli")

#: Nothing in the package may import a product's own client library.
FORBIDDEN_IMPORTS = ("lmstudio", "ollama", "huggingface_hub", "lmstudio_sdk",
                     "llama_cpp", "openai")

#: The live path: none of these may reach the scanner.
LIVE_MODULES = (
    _SRC / "hearth" / "pipeline" / "bot.py",
    _SRC / "hearth" / "pipeline" / "switcher.py",
    _SRC / "hearth" / "serve" / "app.py",
    _SRC / "hearth" / "serve" / "__init__.py",
    *sorted((_SRC / "hearth" / "supervisor" / "routes").glob("*.py")),
)

_LAUNCHERS = {("subprocess", "run"), ("subprocess", "call"),
              ("subprocess", "check_call"), ("subprocess", "check_output"),
              ("subprocess", "Popen"), ("os", "system"), ("os", "popen"),
              ("os", "execv"), ("os", "spawnv")}


def _package_modules() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _launch_calls(tree: ast.Module) -> list[ast.Call]:
    """Every call site that starts another program."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in _LAUNCHERS:
                out.append(node)
        elif isinstance(func, ast.Name) and func.id in {"system", "popen"}:
            out.append(node)
    return out


def _argv_head(call: ast.Call):
    """The first element of the argv literal, if it IS a literal."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


def _imported_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # a relative import — inside this package
                out.add("." + (node.module or ""))
                continue
            base = node.module or ""
            out.add(base)
            out |= {f"{base}.{alias.name}" if base else alias.name
                    for alias in node.names}
    return out


class R1_ReadersTouchFilesAndHeadersOnly(unittest.TestCase):

    def test_the_package_has_modules_to_check(self):
        self.assertGreaterEqual(len(_package_modules()), 6,
                                "the guard rail is only as good as its corpus")

    def test_no_product_command_is_ever_launched(self):
        for path in _package_modules():
            for call in _launch_calls(_tree(path)):
                head = _argv_head(call)
                with self.subTest(module=path.name, line=call.lineno, argv=head):
                    if head is None:
                        continue  # a variable: the operator's own door binary
                    self.assertNotIn(Path(head).name, FORBIDDEN_EXECUTABLES)
                    self.assertIn(
                        Path(head).name, ALLOWED_LITERAL_EXECUTABLES,
                        f"{path.name}:{call.lineno} names an executable that is "
                        "neither the configured door binary nor an allowed one")

    def test_no_product_client_library_is_imported(self):
        for path in _package_modules():
            names = _imported_names(_tree(path))
            for forbidden in FORBIDDEN_IMPORTS:
                with self.subTest(module=path.name, forbidden=forbidden):
                    self.assertFalse(
                        any(n == forbidden or n.startswith(forbidden + ".")
                            for n in names),
                        f"{path.name} imports {forbidden}")

    def test_the_one_product_cache_directory_is_named_only_in_the_skip_list(self):
        from hearth.weights import scan as scan_mod

        self.assertIn(".internal", scan_mod.HIDDEN_SKIP)
        occurrences = {p.name: p.read_text(encoding="utf-8").count(".internal")
                       for p in _package_modules()}
        self.assertEqual(occurrences.pop("scan.py"), 1,
                         "scan.py must name it exactly once — in HIDDEN_SKIP")
        for name, count in occurrences.items():
            with self.subTest(module=name):
                self.assertEqual(count, 0)


class R1_TheRendererStartsNothing(unittest.TestCase):
    """G2's half of R1: rendering a unit is a file act, not a process act."""

    def module(self):
        return PACKAGE / "render.py"

    def test_the_renderer_exists_and_launches_no_program_at_all(self):
        self.assertTrue(self.module().is_file())
        self.assertEqual(_launch_calls(_tree(self.module())), [],
                         "render.py must start no program — not launchctl, not "
                         "the door itself")

    def test_launchctl_is_only_ever_a_printed_line(self):
        from hearth.weights import render as render_mod

        lines = render_mod.launchctl_lines(Path("/x/com.example.plist"), "com.example")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("launchctl bootout "))
        self.assertTrue(lines[1].startswith("launchctl bootstrap "))
        # …and the whole package launches nothing, so those strings can only
        # ever reach a terminal.
        for path in _package_modules():
            for call in _launch_calls(_tree(path)):
                head = _argv_head(call)
                if head is not None:
                    self.assertNotIn("launchctl", head)

    def test_the_one_road_to_launchagents_is_the_injectable_helper(self):
        source = self.module().read_text(encoding="utf-8")
        body = [line for line in source.splitlines() if '"LaunchAgents"' in line]
        self.assertEqual(len(body), 1, body)
        self.assertIn("Library", body[0])

    def test_an_access_key_is_a_path_and_is_never_opened(self):
        """`api_key_file` reaches the argv; nothing ever reads what is in it."""
        from hearth.weights import render as render_mod, roots as roots_mod

        door = roots_mod.DoorConfig(api_key_file="/x/llm-api-key")
        self.assertIn("/x/llm-api-key", render_mod.door_argv(door))
        source = self.module().read_text(encoding="utf-8")
        for line in source.splitlines():
            if "api_key_file" in line:
                self.assertNotIn("read_text", line)
                self.assertNotIn("open(", line)


class R1_TheModelSurfaceStartsNothing(unittest.TestCase):
    """G3's half of R1: the /admin/models package is file work and JSON.

    The two launchctl lines it derives are actuator CONFIG — argv handed to
    `ActuatorSet`, which is where a bounded, logged, non-child command belongs.
    Nothing in the package may start a program itself.
    """

    PACKAGE = _SRC / "hearth" / "supervisor" / "models"

    def modules(self) -> list:
        return sorted(self.PACKAGE.glob("*.py"))

    def test_the_package_exists_and_is_not_under_routes(self):
        self.assertTrue(self.PACKAGE.is_dir(), self.PACKAGE)
        self.assertGreaterEqual(len(self.modules()), 4)
        for path in self.modules():
            self.assertNotIn("routes", path.parts,
                             "R4 scans routes/ — this package must stay beside it")

    def test_no_module_starts_a_program(self):
        for path in self.modules():
            with self.subTest(module=path.name):
                self.assertEqual(_launch_calls(_tree(path)), [],
                                 f"{path.name} must start nothing — the actuator "
                                 "runner is what presses a command")

    def test_launchctl_is_only_ever_actuator_argv(self):
        from hearth.supervisor.models import door as door_mod
        from hearth.weights import roots as roots_mod

        cfg = roots_mod.WeightsConfig(door_declared=True)
        derived = door_mod.door_actuators(cfg, uid=501)
        self.assertEqual(sorted(derived), [door_mod.LOAD, door_mod.UNLOAD])
        for name, block in derived.items():
            self.assertEqual(block["command"][0], "/bin/launchctl")
            self.assertEqual(block["guard"], "companion")
        # …and no module in the package ever calls one.
        for path in self.modules():
            for call in _launch_calls(_tree(path)):
                self.assertIsNone(_argv_head(call))

    def test_no_door_table_means_no_derived_actuators(self):
        from hearth.supervisor.models import door as door_mod
        from hearth.weights import roots as roots_mod

        self.assertEqual(door_mod.door_actuators(roots_mod.WeightsConfig()), {})

    def test_the_key_path_is_redacted_rather_than_reported(self):
        from hearth.supervisor.models import facts as facts_mod

        argv = ["/x/llama-server", "--api-key-file", "/x/llm-api-key", "--port", "8080"]
        redacted = facts_mod.redact_argv(argv)
        self.assertIn("--api-key-file", redacted)
        self.assertNotIn("/x/llm-api-key", redacted)
        self.assertIn("8080", redacted)


class R4_TheLiveLoopNeverImportsTheScanner(unittest.TestCase):

    def test_the_live_modules_exist(self):
        for path in LIVE_MODULES:
            self.assertTrue(path.is_file(), path)
        self.assertGreaterEqual(len(LIVE_MODULES), 8)

    def test_none_of_them_reaches_for_hearth_weights(self):
        for path in LIVE_MODULES:
            names = _imported_names(_tree(path))
            with self.subTest(module=str(path)):
                self.assertNotIn("hearth.weights", names)
                self.assertFalse(any(n.startswith("hearth.weights.") for n in names))
                # `from hearth import weights`
                self.assertNotIn("hearth.weights", {f"hearth.{n}" for n in names})


class R5_AScanNeedsNoProductPresent(unittest.TestCase):

    def test_the_only_third_party_import_is_the_gguf_reader(self):
        third_party = set()
        stdlib_and_ours = ("hearth", "__future__")
        for path in _package_modules():
            for name in _imported_names(_tree(path)):
                top = name.split(".")[0]
                if top and not top.startswith(stdlib_and_ours):
                    third_party.add(top)
        import sys
        third_party = {n for n in third_party
                       if n not in sys.stdlib_module_names and n}
        self.assertEqual(third_party, {"gguf"}, third_party)


if __name__ == "__main__":
    unittest.main()
