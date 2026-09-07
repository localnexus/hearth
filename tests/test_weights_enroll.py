"""test_weights_enroll.py — enrollment writes a reference and nothing else.

Every case runs `python -m hearth.weights` in a SUBPROCESS against a temporary
data folder, because config_loader resolves its anchors once at import — the
same pattern tests/test_settings_registry.py's LoaderIntegration uses. Pins:

  1. `enroll` without --yes previews, exits 1, and writes nothing;
  2. `enroll --yes` writes a [weights] + [weights.header] block, and every other
     line of the model.toml — the comments included — survives byte-identical;
  3. config_loader.load_model() still loads that file AND warns about nothing:
     the schema knows `weights` now, so no "unknown key" appears on stderr;
  4. `list` shows it as present;
  5. with the file deleted, `check` reports a MISSING error, exits 1, and does
     not raise (guard rail R3: a reported state, never a fallback);
  6. `unenroll --yes` removes both blocks and restores the file byte-for-byte;
  7. a `[server]` key that is not a flag of the door named by --llama-server is
     an error naming that key; with no binary at all it is one warning.

Run:  .venv/bin/python -m unittest tests.test_weights_enroll
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_weights_scan import tiny_gguf

_PY = sys.executable
_ROOT = Path(__file__).resolve().parents[1]

MODEL_TOML = """\
# config/models/m1/model.toml — hand-written, mostly comments.
#
# Every one of these lines must survive an enroll and an unenroll untouched.

# The id the server advertises.
id = "zz-test-model"

# Sampling.
temperature = 0.7
reasoning_effort = "none"
"""

FAKE_DOOR = """\
#!/bin/sh
cat <<'HELP'
usage: llama-server [options]

  -c,    --ctx-size N            size of the prompt context
         --parallel N            number of parallel sequences
         --kv-unified            use single unified KV buffer
         --alias STRING          set alias for model name
         --chat-template-file F  set custom jinja chat template
HELP
"""


class _Case(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        (self.data / "config").mkdir(parents=True)
        (self.data / "config" / "active.toml").write_text(
            'character = "example"\nmodel = "m1"\nvoice = "default"\n', encoding="utf-8")
        self.model_dir = self.data / "config" / "models" / "m1"
        self.model_dir.mkdir(parents=True)
        self.model_toml = self.model_dir / "model.toml"
        self.model_toml.write_text(MODEL_TOML, encoding="utf-8")
        self.weights = tiny_gguf(Path(self.tmp.name) / "models" / "pub" / "repo" / "m.gguf")

    def run_cli(self, *args, module="hearth.weights"):
        env = dict(os.environ)
        env.pop("HEARTH_ROOT", None)
        env["HEARTH_DATA"] = str(self.data)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([_PY, "-m", module, *args], capture_output=True,
                              text=True, env=env, cwd=str(_ROOT))

    def run_py(self, code: str):
        env = dict(os.environ)
        env.pop("HEARTH_ROOT", None)
        env["HEARTH_DATA"] = str(self.data)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([_PY, "-c", code], capture_output=True, text=True,
                              env=env, cwd=str(_ROOT))

    def fake_door(self) -> str:
        path = Path(self.tmp.name) / "fake-llama-server"
        path.write_text(FAKE_DOOR, encoding="utf-8")
        path.chmod(0o755)
        return str(path)


class PreviewThenYes(_Case):

    def test_preview_writes_nothing_and_exits_one(self):
        before = self.model_toml.read_text(encoding="utf-8")
        r = self.run_cli("enroll", "m1", "--path", str(self.weights))
        self.assertEqual(r.returncode, 1, r.stderr[-800:])
        self.assertIn("re-run with --yes", r.stderr)
        self.assertIn(str(self.weights), r.stdout)
        self.assertIn("qwen35moe", r.stdout)
        self.assertEqual(before, self.model_toml.read_text(encoding="utf-8"))

    def test_yes_writes_the_block_and_keeps_every_other_line(self):
        before = self.model_toml.read_text(encoding="utf-8")
        r = self.run_cli("enroll", "m1", "--path", str(self.weights), "--yes")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        after = self.model_toml.read_text(encoding="utf-8")
        self.assertIn("[weights]", after)
        self.assertIn("[weights.header]", after)
        self.assertIn(f'path = "{self.weights.resolve()}"', after)
        self.assertIn('architecture = "qwen35moe"', after)
        for line in before.split("\n"):
            if line.strip():
                self.assertIn(line, after)

    def test_the_loader_reads_it_and_warns_about_nothing(self):
        self.run_cli("enroll", "m1", "--path", str(self.weights), "--yes")
        r = self.run_py("import hearth.config.config_loader as c\n"
                        "print('ID', c.load_model('m1')['id'])\n"
                        "print('PATH', c.load_model('m1')['weights']['path'])\n")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("ID zz-test-model", r.stdout)
        self.assertIn(str(self.weights.resolve()), r.stdout)
        self.assertNotIn("unknown key", r.stderr)

    def test_list_shows_it(self):
        self.run_cli("enroll", "m1", "--path", str(self.weights), "--yes")
        r = self.run_cli("list")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("m1", r.stdout)
        self.assertIn("present", r.stdout)
        self.assertIn("1 enrolled model(s)", r.stdout)

    def test_enrolling_into_a_directory_that_does_not_exist_refuses(self):
        r = self.run_cli("enroll", "nope", "--path", str(self.weights), "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not exist", r.stderr)
        self.assertIn("example", r.stderr)


class MissingIsAState(_Case):

    def test_a_deleted_file_is_an_error_finding_not_a_traceback(self):
        self.run_cli("enroll", "m1", "--path", str(self.weights), "--yes")
        self.weights.unlink()
        r = self.run_cli("check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("[ERROR]", r.stdout)
        self.assertIn("weights missing", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_replaced_file_changes_the_identity(self):
        self.run_cli("enroll", "m1", "--path", str(self.weights), "--yes")
        self.weights.unlink()
        tiny_gguf(self.weights, name="a-different-model-entirely", tensor=16)
        r = self.run_cli("check", "m1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("size changed", r.stdout)


class Unenrolling(_Case):

    def test_it_restores_the_file_byte_for_byte(self):
        before = self.model_toml.read_text(encoding="utf-8")
        self.run_cli("enroll", "m1", "--path", str(self.weights), "--yes")
        self.assertNotEqual(before, self.model_toml.read_text(encoding="utf-8"))

        r = self.run_cli("unenroll", "m1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("re-run with --yes", r.stderr)

        r = self.run_cli("unenroll", "m1", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertEqual(before, self.model_toml.read_text(encoding="utf-8"))
        self.assertTrue(self.weights.is_file(), "the weights file must survive")


class ServerKeys(_Case):

    def _add_server_table(self, body: str) -> None:
        with open(self.model_toml, "a", encoding="utf-8") as fh:
            fh.write(body)

    def test_a_bogus_key_is_named_against_the_doors_own_help(self):
        self._add_server_table('\n[server]\nc = 4096\n'
                               'zz-not-a-flag = true\n')
        r = self.run_cli("check", "m1", "--llama-server", self.fake_door())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("zz-not-a-flag", r.stdout)
        self.assertIn("[ERROR]", r.stdout)

    def test_real_flags_pass(self):
        self._add_server_table('\n[server]\nc = 4096\nparallel = 4\n'
                               'kv-unified = true\nalias = "zz-test-model"\n')
        r = self.run_cli("check", "m1", "--llama-server", self.fake_door())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("exist in the door's --help", r.stdout)

    def test_no_binary_is_one_warning_not_a_failure(self):
        self._add_server_table('\n[server]\nzz-not-a-flag = true\n')
        missing = str(Path(self.tmp.name) / "no-such-binary")
        r = self.run_cli("check", "m1", "--llama-server", missing)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("server keys not checked", r.stdout)


if __name__ == "__main__":
    unittest.main()
