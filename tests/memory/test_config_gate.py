"""Memory config gate — config/memory.toml.

Absent or disabled ⇒ None; enabled ⇒ defaults plus the per-companion map;
"none" opts a companion out; an unknown backend ⇒ ConfigError. Run in a
subprocess with a scratch HEARTH_DATA, because the anchors resolve at import
(the test_data_root shape).

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import maybe_attach  # noqa: E402


# ── the config gate: anchors resolve at import ⇒ subprocess (test_data_root shape) ──

_GATE_PROBE = """
import json
from hearth.config import config_loader
from hearth import memory
cfg = config_loader.load_memory_config()
out = {"cfg": cfg}
if cfg is not None:
    seam_ex = memory.maybe_attach("example")
    seam_off = memory.maybe_attach("guest")
    out["example_backend"] = seam_ex.backend.name if seam_ex else None
    out["guest_attached"] = seam_off is not None
print(json.dumps(out))
"""


class TestConfigGate(unittest.TestCase):
    def _run(self, memory_toml: str | None, probe: str = _GATE_PROBE):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config").mkdir()
            if memory_toml is not None:
                (Path(tmp) / "config" / "memory.toml").write_text(memory_toml, encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("HEARTH_ROOT", "HEARTH_DATA")}
            env["HEARTH_DATA"] = tmp
            env["PYTHONPATH"] = str(REPO / "src")
            return subprocess.run([sys.executable, "-c", probe], capture_output=True,
                                  text=True, env=env, cwd=str(REPO))

    def test_absent_and_disabled_mean_none(self):
        for toml in (None, "[memory]\nenabled = false\n"):
            res = self._run(toml)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIsNone(json.loads(res.stdout)["cfg"])

    def test_enabled_defaults_and_per_companion_map(self):
        res = self._run(
            "[memory]\nenabled = true\n"
            "[memory.companions]\nguest = \"none\"\n"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(out["cfg"]["backend"], "floor")
        self.assertEqual(out["cfg"]["recall_limit"], 6)
        self.assertEqual(out["example_backend"], "floor")
        self.assertFalse(out["guest_attached"])

    def test_intent_defaults_off_and_inherit_hindsight_llm(self):
        """Absent [memory.intent] ⇒ normalized, disabled. Present ⇒ its LLM
        settings fall back to the extraction model [memory.hindsight] names."""
        res = self._run("[memory]\nenabled = true\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        intent = json.loads(res.stdout)["cfg"]["intent"]
        self.assertFalse(intent["enabled"])
        self.assertEqual(intent["expiry_days"], 14)
        self.assertEqual(intent["companions"], {})

        res = self._run(
            "[memory]\nenabled = true\n"
            "[memory.intent]\nenabled = true\nexpiry_days = 7\n"
            "[memory.intent.companions]\nguest = false\n"
            "[memory.hindsight]\nllm_provider = \"ollama\"\nllm_model = \"qwen3-coder:30b\"\n"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        intent = json.loads(res.stdout)["cfg"]["intent"]
        self.assertTrue(intent["enabled"])
        self.assertEqual(intent["expiry_days"], 7)
        self.assertEqual(intent["llm_provider"], "ollama")
        self.assertEqual(intent["llm_model"], "qwen3-coder:30b")
        self.assertEqual(intent["companions"], {"guest": False})

    def test_serve_defaults_off_and_normalized(self):
        """Absent [memory.serve] ⇒ normalized, disabled — the facade lane ships
        dark. Present ⇒ its boundaries are honored."""
        res = self._run("[memory]\nenabled = true\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        serve = json.loads(res.stdout)["cfg"]["serve"]
        self.assertFalse(serve["enabled"])
        self.assertEqual(serve["idle_close_voice"], 5)
        self.assertEqual(serve["idle_close_chat"], 480)
        self.assertTrue(serve["checkpoint"])

        res = self._run(
            "[memory]\nenabled = true\n"
            "[memory.serve]\nenabled = true\nidle_close_voice = 3\n"
            "idle_close_chat = 60\ncheckpoint = false\n"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        serve = json.loads(res.stdout)["cfg"]["serve"]
        self.assertTrue(serve["enabled"])
        self.assertEqual(serve["idle_close_voice"], 3)
        self.assertEqual(serve["idle_close_chat"], 60)
        self.assertFalse(serve["checkpoint"])

    def test_unknown_backend_is_config_error(self):
        res = self._run("[memory]\nenabled = true\nbackend = \"warpdrive\"\n")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ConfigError", res.stderr)
        self.assertIn("memory.toml", res.stderr)

if __name__ == "__main__":
    unittest.main()
