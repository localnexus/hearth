"""test_weights_render.py — the launchd unit, derived rather than hand-kept.

The renderer's whole claim is that a unit file is a RENDERING of config that is
already true, so the tests are mostly one question asked in different ways: is
the argv what the config says, and is a unit already on the machine the same
unit? Fixtures are built in temp directories — never this machine's real data
folder, its real weights, or its real `~/Library/LaunchAgents` (the CLI cases
point `HEARTH_LAUNCH_AGENTS` at a temp dir, which is what that variable is for).

Pins:

  1. GOLDEN — an enrolled model with a full `[server]` table renders exactly the
     command line the hand-written unit carries, minus the four placement flags
     and with `--load-mode` in place of the deprecated `--mlock` /
     `--no-direct-io` pair;
  2. booleans are switches (`true` → bare flag, `false` → omitted) while a
     valued flag stays valued, `flash-attn = "on"` included; a one-letter key is
     a short flag;
  3. the plist has the shape launchd is given today: run at load, come back
     after a non-zero exit, ten seconds apart, both streams to
     `<log>.launchd.log` — and a deterministic key order;
  4. `--diff` classifies: placement flags and the deprecated loading spelling
     are differences that are not findings (exit 0); anything else is `real`
     and exits 1;
  5. `apply` previews by default and exits 1 having written nothing; with
     `--yes` it writes the unit, ARCHIVES whatever was there as `.prev-<date>`
     rather than deleting it, and prints the two launchctl lines without
     running them;
  6. render writes only into DATA/render/.

Run:  .venv/bin/python -m unittest tests.test_weights_render
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hearth.weights import render as render_mod
from hearth.weights import roots as roots_mod

_PY = sys.executable
_ROOT = Path(__file__).resolve().parents[1]

#: The door binary the fixtures name. A path, never run: the renderer starts
#: no program (guard rail R1), it only writes the name into the unit.
DOOR_BINARY = "/opt/homebrew/bin/llama-server"

MODEL_TOML = '''\
# config/models/my-model/model.toml — a fixture, in the shape of a real one.

id = "my-model"
temperature = 0.7
reasoning_effort = "none"

[weights]
path = "{weights}"
mmproj = "{mmproj}"
root = "models"
layout = "plain"
display_key = "pub/repo/my-model-Q8_0"
size_bytes = 37802153120
identity = "0123456789abcdef"
enrolled = "2026-09-07"

[weights.header]
architecture = "qwen35moe"
block_count = 41
context_length = 262144
head_count_kv = 2

[server]
alias = "my-model"
ctx-size = 262144
parallel = 4
n-cpu-moe = 0
batch-size = 2048
ubatch-size = 512
cache-type-k = "f16"
cache-type-v = "f16"
flash-attn = "on"
kv-offload = true
kv-unified = true
jinja = true
chat-template-file = "{template}"
spec-type = "draft-mtp"
spec-draft-n-max = 2
spec-draft-n-min = 0
spec-draft-p-min = 0.75
'''

WEIGHTS_TOML = '''\
[weights]
roots = ["{models}"]
product_dirs = false
llama_server = "{binary}"

[weights.door]
label = "com.hearth.llm"
host = "127.0.0.1"
port = 8080
api_key_file = "{key_file}"
threads = 24
load_mode = "mlock"
log_file = "{log_file}"
webui = false
'''


def _fixture(tmp: Path) -> Path:
    """A data folder with one enrolled model and a door table. → the data dir."""
    data = tmp / "data"
    (data / "config" / "models" / "my-model").mkdir(parents=True)
    (data / "logs").mkdir(parents=True)
    models = tmp / "models" / "pub" / "repo"
    models.mkdir(parents=True)
    weights = models / "my-model-Q8_0.gguf"
    mmproj = models / "my-model-mmproj-BF16.gguf"
    for path in (weights, mmproj):
        path.write_bytes(b"GGUF")
    template = data / "config" / "models" / "my-model" / "chat-template.jinja"
    template.write_text("{{ messages }}", encoding="utf-8")
    (data / "config" / "models" / "my-model" / "model.toml").write_text(
        MODEL_TOML.format(weights=weights, mmproj=mmproj, template=template),
        encoding="utf-8")
    (data / "config" / "weights.toml").write_text(
        WEIGHTS_TOML.format(models=tmp / "models", binary=DOOR_BINARY,
                            key_file=data / "config" / "llm-api-key",
                            log_file=data / "logs" / "llm-server.log"),
        encoding="utf-8")
    return data


def golden_argv(data: Path, tmp: Path) -> list[str]:
    """What the fixture above must render to, written out by hand."""
    models = tmp / "models" / "pub" / "repo"
    return [
        DOOR_BINARY,
        "--model", str(models / "my-model-Q8_0.gguf"),
        "--mmproj", str(models / "my-model-mmproj-BF16.gguf"),
        # [server], in the order the model.toml declares
        "--alias", "my-model",
        "--ctx-size", "262144",
        "--parallel", "4",
        "--n-cpu-moe", "0",
        "--batch-size", "2048",
        "--ubatch-size", "512",
        "--cache-type-k", "f16",
        "--cache-type-v", "f16",
        "--flash-attn", "on",          # a valued flag, not a switch
        "--kv-offload",                # true → bare
        "--kv-unified",
        "--jinja",
        "--chat-template-file", str(data / "config" / "models" / "my-model" / "chat-template.jinja"),
        "--spec-type", "draft-mtp",
        "--spec-draft-n-max", "2",
        "--spec-draft-n-min", "0",
        "--spec-draft-p-min", "0.75",
        # [weights.door]
        "--host", "127.0.0.1",
        "--port", "8080",
        "--api-key-file", str(data / "config" / "llm-api-key"),
        "--threads", "24",
        "--load-mode", "mlock",        # not --mlock + --no-direct-io
        "--log-file", str(data / "logs" / "llm-server.log"),
        "--no-webui",
    ]


def live_plist(path: Path, argv: list[str], label: str = "com.hearth.llm") -> Path:
    """A hand-written unit in today's shape, for --diff to be aimed at."""
    with open(path, "wb") as fh:
        plistlib.dump({
            "Label": label,
            "ProgramArguments": list(argv),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
        }, fh)
    return path


class _Case(unittest.TestCase):
    """Every case gets its own data folder, and its own LaunchAgents dir."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.data = _fixture(self.tmp)
        self.agents = self.tmp / "LaunchAgents"
        self.agents.mkdir()

    def env(self) -> dict:
        env = dict(os.environ)
        env.pop("HEARTH_ROOT", None)
        env.pop("SERVE_TOKEN", None)
        env["HEARTH_DATA"] = str(self.data)
        env["HEARTH_LAUNCH_AGENTS"] = str(self.agents)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def run_cli(self, *args):
        return subprocess.run([_PY, "-m", "hearth.weights", *args],
                              capture_output=True, text=True, env=self.env(),
                              cwd=str(_ROOT))

    def run_py(self, code: str):
        return subprocess.run([_PY, "-c", code], capture_output=True, text=True,
                              env=self.env(), cwd=str(_ROOT))

    def rendered_argv(self) -> list[str]:
        got = self.run_py(
            "import json\n"
            "from hearth.weights import render as r\n"
            "print(json.dumps(r.build_argv('my-model')))\n")
        self.assertEqual(got.returncode, 0, got.stderr[-1500:])
        return json.loads(got.stdout.strip().splitlines()[-1])


# ── 1 · the golden command line ──────────────────────────────────────────────

class Golden(_Case):

    def test_argv_is_exactly_what_the_config_says(self):
        self.assertEqual(self.rendered_argv(), golden_argv(self.data, self.tmp))

    def test_the_four_placement_flags_are_left_to_fit(self):
        argv = self.rendered_argv()
        for flag in ("--n-gpu-layers", "--main-gpu", "--tensor-split", "--split-mode"):
            self.assertNotIn(flag, argv)

    def test_the_deprecated_loading_pair_is_gone(self):
        argv = self.rendered_argv()
        self.assertNotIn("--mlock", argv)
        self.assertNotIn("--no-direct-io", argv)
        self.assertEqual(argv[argv.index("--load-mode") + 1], "mlock")

    def test_the_access_key_is_a_path_and_stays_shut(self):
        key = self.data / "config" / "llm-api-key"
        key.write_text("not-a-real-key\n", encoding="utf-8")
        argv = self.rendered_argv()
        self.assertIn(str(key), argv)
        self.assertNotIn("not-a-real-key", " ".join(argv))


# ── 2 · flags: switches, values, short names ─────────────────────────────────

class FlagRendering(unittest.TestCase):

    def test_true_is_a_bare_flag_and_false_is_nothing(self):
        self.assertEqual(render_mod.server_argv({"jinja": True, "kv-unified": False}),
                         ["--jinja"])

    def test_a_string_value_stays_valued(self):
        self.assertEqual(render_mod.server_argv({"flash-attn": "on"}),
                         ["--flash-attn", "on"])

    def test_numbers_keep_their_spelling(self):
        self.assertEqual(
            render_mod.server_argv({"ctx-size": 262144, "spec-draft-p-min": 0.75,
                                    "temp": 1.0}),
            ["--ctx-size", "262144", "--spec-draft-p-min", "0.75", "--temp", "1"])

    def test_a_one_letter_key_is_a_short_flag(self):
        self.assertEqual(render_mod.server_argv({"c": 4096}), ["-c", "4096"])

    def test_order_follows_the_file(self):
        self.assertEqual(render_mod.server_argv({"b": 1, "a": 2}), ["-b", "1", "-a", "2"])

    def test_door_flags_are_fixed_in_order_and_webui_is_a_negation(self):
        door = roots_mod.DoorConfig(host="127.0.0.1", port=9, threads=2,
                                    load_mode="dio", webui=False,
                                    args=["--extra", "1"])
        self.assertEqual(render_mod.door_argv(door),
                         ["--host", "127.0.0.1", "--port", "9", "--threads", "2",
                          "--load-mode", "dio", "--no-webui", "--extra", "1"])

    def test_webui_true_passes_no_flag_at_all(self):
        self.assertNotIn("--no-webui",
                         render_mod.door_argv(roots_mod.DoorConfig(webui=True)))


# ── 3 · the plist itself ─────────────────────────────────────────────────────

class Plist(unittest.TestCase):

    def test_shape_matches_the_unit_launchd_is_given_today(self):
        text = render_mod.build_plist("com.hearth.llm", ["/bin/door", "--port", "8080"],
                                      Path("/x/logs/llm-server.launchd.log"),
                                      working_dir=Path("/x"))
        unit = plistlib.loads(text.encode("utf-8"))
        self.assertEqual(unit["Label"], "com.hearth.llm")
        self.assertEqual(unit["ProgramArguments"], ["/bin/door", "--port", "8080"])
        self.assertIs(unit["RunAtLoad"], True)
        self.assertEqual(unit["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(unit["ThrottleInterval"], 10)
        self.assertEqual(unit["StandardOutPath"], "/x/logs/llm-server.launchd.log")
        self.assertEqual(unit["StandardErrorPath"], unit["StandardOutPath"])
        self.assertEqual(unit["WorkingDirectory"], "/x")

    def test_key_order_is_deterministic(self):
        text = render_mod.build_plist("l", ["/bin/door"], Path("/x.log"), Path("/x"))
        order = [line.split("<key>")[1].split("</key>")[0]
                 for line in text.splitlines() if "<key>" in line]
        order = [k for k in order if k in render_mod.PLIST_KEY_ORDER]
        self.assertEqual(order, list(render_mod.PLIST_KEY_ORDER))

    def test_launchd_log_sits_beside_the_doors_own(self):
        door = roots_mod.DoorConfig(log_file="/x/logs/llm-server.log")
        self.assertEqual(render_mod.launchd_log_path(door),
                         Path("/x/logs/llm-server.launchd.log"))

    def test_no_log_file_falls_back_to_a_name_under_the_data_folder(self):
        self.assertEqual(render_mod.launchd_log_path(roots_mod.DoorConfig()).name,
                         "com.hearth.llm.launchd.log")


# ── 4 · the equivalence check ────────────────────────────────────────────────

class Diff(_Case):

    def live(self, argv: list[str]) -> Path:
        return live_plist(self.tmp / "live.plist", argv)

    def test_the_same_command_line_is_no_difference_at_all(self):
        target = self.live(golden_argv(self.data, self.tmp))
        got = self.run_cli("render", "my-model", "--diff", "--against", str(target))
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr[-800:])
        self.assertIn("identical", got.stdout)

    def test_placement_and_deprecated_form_are_differences_but_not_findings(self):
        argv = golden_argv(self.data, self.tmp)
        # today's hand-written unit: placement set by hand, loading in the old
        # spelling, and no --load-mode.
        argv = [a for a in argv if a not in ("--load-mode", "mlock")]
        argv += ["--n-gpu-layers", "999999", "--main-gpu", "0",
                 "--tensor-split", "0", "--split-mode", "layer",
                 "--no-direct-io", "--mlock"]
        got = self.run_cli("render", "my-model", "--diff", "--against",
                           str(self.live(argv)))
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr[-800:])
        self.assertIn("0 real", got.stdout)
        self.assertIn("4 placement", got.stdout)
        for flag in ("n-gpu-layers", "main-gpu", "tensor-split", "split-mode"):
            self.assertIn(flag, got.stdout)
        self.assertIn("deprecated-form", got.stdout)

    def test_a_changed_value_is_real_and_exits_one(self):
        argv = golden_argv(self.data, self.tmp)
        argv[argv.index("262144")] = "131072"
        got = self.run_cli("render", "my-model", "--diff", "--against",
                           str(self.live(argv)))
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("real", got.stdout)
        self.assertIn("ctx-size", got.stdout)
        self.assertIn("131072", got.stdout)

    def test_a_flag_only_the_live_unit_carries_is_real(self):
        argv = golden_argv(self.data, self.tmp) + ["--cache-reuse", "256"]
        got = self.run_cli("render", "my-model", "--diff", "--against",
                           str(self.live(argv)))
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("cache-reuse", got.stdout)

    def test_classification_is_the_flag_name_and_nothing_else(self):
        self.assertEqual(render_mod.classify("n-gpu-layers"), "placement")
        self.assertEqual(render_mod.classify("mlock"), "deprecated-form")
        self.assertEqual(render_mod.classify("no-direct-io"), "deprecated-form")
        self.assertEqual(render_mod.classify("load-mode"), "deprecated-form")
        self.assertEqual(render_mod.classify("ctx-size"), "real")

    def test_short_and_long_spellings_are_the_same_flag(self):
        differences = render_mod.diff_argv(["/bin/door", "--ctx-size", "4096"],
                                           ["/bin/door", "-c", "4096"])
        self.assertEqual(differences, [])

    def test_two_spellings_of_one_file_are_one_value(self):
        """A unit that reaches the weights through a product's symlink and one
        that names the real path are the same unit (R2)."""
        import os

        real = self.tmp / "models" / "pub" / "repo" / "my-model-Q8_0.gguf"
        link = self.tmp / "by-another-name.gguf"
        os.symlink(real, link)
        differences = render_mod.diff_argv(["/bin/door", "--model", str(real)],
                                           ["/bin/door", "--model", str(link)])
        self.assertEqual(differences, [])

    def test_a_path_that_does_not_exist_is_never_quietly_matched(self):
        differences = render_mod.diff_argv(["/bin/door", "--model", "/nope/a.gguf"],
                                           ["/bin/door", "--model", "/nope/b.gguf"])
        self.assertEqual([d.kind for d in differences], ["real"])

    def test_a_different_binary_is_real(self):
        differences = render_mod.diff_argv(["/bin/door"], ["/other/door"])
        self.assertEqual([(d.flag, d.kind) for d in differences],
                         [("(program)", "real")])


# ── 5 · apply ────────────────────────────────────────────────────────────────

class Apply(_Case):

    def unit_path(self) -> Path:
        return self.agents / "com.hearth.llm.plist"

    def test_preview_writes_nothing_and_exits_one(self):
        got = self.run_cli("apply", "my-model")
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr[-800:])
        self.assertIn("re-run with --yes", got.stderr)
        self.assertEqual(list(self.agents.iterdir()), [])

    def test_preview_leaves_an_existing_unit_untouched(self):
        self.unit_path().write_text("<plist>old</plist>", encoding="utf-8")
        self.run_cli("apply", "my-model")
        self.assertEqual(self.unit_path().read_text(encoding="utf-8"),
                         "<plist>old</plist>")

    def test_yes_writes_the_unit_and_prints_the_two_launchctl_lines(self):
        got = self.run_cli("apply", "my-model", "--yes")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr[-800:])
        unit = plistlib.loads(self.unit_path().read_bytes())
        self.assertEqual(unit["ProgramArguments"], golden_argv(self.data, self.tmp))
        self.assertIn("launchctl bootout gui/$UID/com.hearth.llm", got.stdout)
        self.assertIn(f"launchctl bootstrap gui/$UID {self.unit_path()}", got.stdout)

    def test_the_previous_unit_is_archived_never_deleted(self):
        self.unit_path().write_text("<plist>old</plist>", encoding="utf-8")
        got = self.run_cli("apply", "my-model", "--yes")
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        archives = sorted(p for p in self.agents.iterdir() if ".prev-" in p.name)
        self.assertEqual(len(archives), 1, [p.name for p in self.agents.iterdir()])
        self.assertEqual(archives[0].read_text(encoding="utf-8"), "<plist>old</plist>")
        self.assertTrue(archives[0].name.startswith("com.hearth.llm.plist.prev-"))

    def test_a_second_archive_the_same_day_does_not_overwrite_the_first(self):
        first = self.tmp / "u.plist"
        first.write_text("a", encoding="utf-8")
        name = render_mod.archive_name(first)
        name.write_text("already here", encoding="utf-8")
        again = render_mod.archive_name(first)
        self.assertNotEqual(again, name)
        self.assertFalse(again.exists())

    def test_render_writes_only_under_the_data_folder(self):
        got = self.run_cli("render", "my-model")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr[-800:])
        written = self.data / "render" / "com.hearth.llm.plist"
        self.assertTrue(written.is_file(), got.stdout)
        self.assertIn(str(written), got.stdout)
        self.assertEqual(list(self.agents.iterdir()), [])


# ── 6 · refusals that are messages, not tracebacks ───────────────────────────

class Refusals(_Case):

    def test_an_unenrolled_model_is_told_to_enroll_first(self):
        toml = self.data / "config" / "models" / "bare" / "model.toml"
        toml.parent.mkdir(parents=True)
        toml.write_text('id = "bare"\n', encoding="utf-8")
        got = self.run_cli("render", "bare")
        self.assertEqual(got.returncode, 1)
        self.assertIn("no [weights] table", got.stderr)

    def test_a_load_mode_the_door_does_not_know_names_the_file(self):
        path = self.data / "config" / "weights.toml"
        path.write_text(path.read_text(encoding="utf-8")
                        .replace('load_mode = "mlock"', 'load_mode = "hold-it-all"'),
                        encoding="utf-8")
        got = self.run_cli("render", "my-model")
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("load_mode", got.stderr)
        self.assertNotIn("Traceback", got.stderr)

    def test_a_missing_plist_to_diff_against_is_a_message(self):
        got = self.run_cli("render", "my-model", "--diff", "--against",
                           str(self.tmp / "nope.plist"))
        self.assertEqual(got.returncode, 1)
        self.assertIn("cannot read", got.stderr)


if __name__ == "__main__":
    unittest.main()
