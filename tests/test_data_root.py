"""test_data_root.py — the two anchors (HEARTH_ROOT / HEARTH_DATA) and what hangs off them.

Proves, on the real package:
  1. DATA defaults to ROOT (an unconfigured checkout is unchanged)
  2. with HEARTH_DATA elsewhere: shipped example character/model/baseline still resolve
     (DATA-first, ROOT fallback); an operator copy under DATA shadows the shipped one;
     runtime state (sessions/captures/transcripts) always lands under DATA
  3. a wrong HEARTH_ROOT (no config/) fails fast at import, naming the fix
  4. a schema-1 session file loads with persona "default"; schema-2 round-trips persona
  5. persona variants: persona.<name>.md beside persona.md; bad names rejected

Run:  .venv/bin/python -m unittest tests/test_data_root.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hearth.config import config_loader as cl
from hearth.session import session_store as ss

_PY = sys.executable
_ROOT = cl._ROOT


def _run(code: str, **env) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.pop("HEARTH_DATA", None)
    e.pop("HEARTH_ROOT", None)
    e.update(env)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([_PY, "-c", code], capture_output=True, text=True, env=e, cwd=str(_ROOT))


class DataRoot(unittest.TestCase):
    def test_data_defaults_to_root(self):
        r = _run("import hearth.config.config_loader as c; print(c.DATA_DIR == c._ROOT)")
        self.assertEqual(r.stdout.strip(), "True", r.stderr[-800:])

    def test_relocated_data_root_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            code = f"""
import hearth.config.config_loader as c, json
out = {{}}
out['model'] = str(c.model_dir('example'))
out['char'] = str(c.character_dir('example'))
out['vad'] = str(c.baseline_path('vad.toml'))
out['sessions'] = str(c.companion_state_dir('example', 'sessions'))
out['clip'] = str(c.resolve_data_path('characters/example/voices/default/sample.wav'))
out['voice_ok'] = c.load_voice('example', 'default')['tag']
print(json.dumps(out))
"""
            r = _run(code, HEARTH_DATA=d)
            self.assertEqual(r.returncode, 0, r.stderr[-1200:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
            root = str(_ROOT)
            self.assertEqual(out["model"], f"{root}/config/models/example")      # ROOT fallback
            self.assertEqual(out["char"], f"{root}/characters/example")
            self.assertEqual(out["vad"], f"{root}/config/vad.toml")
            self.assertEqual(out["sessions"], f"{d}/characters/example/sessions")  # state → DATA
            self.assertTrue(out["clip"].startswith(root))
            self.assertEqual(out["voice_ok"], "default")
            # an operator copy under DATA shadows the shipped one
            (Path(d) / "characters" / "example").mkdir(parents=True)
            (Path(d) / "config" / "vad.toml").parent.mkdir(parents=True)
            (Path(d) / "config" / "vad.toml").write_text("[live]\nconfidence = 0.5\n")
            r = _run("import hearth.config.config_loader as c; print(c.character_dir('example')); print(c.baseline_path('vad.toml'))",
                     HEARTH_DATA=d)
            lines = r.stdout.strip().splitlines()
            self.assertEqual(lines[-2], f"{d}/characters/example")
            self.assertEqual(lines[-1], f"{d}/config/vad.toml")

    def test_wrong_root_fails_fast(self):
        with tempfile.TemporaryDirectory() as d:
            r = _run("import hearth.config.config_loader", HEARTH_ROOT=d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("HEARTH_ROOT", r.stderr)
            self.assertIn("no config/", r.stderr)


class SessionSchema(unittest.TestCase):
    def test_schema1_loads_with_default_persona(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session-old.json"
            p.write_text(json.dumps({"schema": 1, "model": "m", "voice": "v", "prompt_sha256": "x",
                                     "started": "2026-01-01T00:00:00", "held": False,
                                     "messages": [{"role": "user", "content": "hi"}]}))
            metas = ss.list_sessions(Path(d))
            self.assertEqual(len(metas), 1)
            self.assertEqual(metas[0].persona, "default")
            self.assertIsNone(metas[0].character)

    def test_schema2_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            st = ss.SessionStore(session_id="s", model="m", voice="v", prompt_sha256="x",
                                 sessions_dir=Path(d), character="example", persona="night")
            st.snapshot([{"role": "user", "content": "hi"}])
            data = ss.load(st.path)
            self.assertEqual(data["schema"], 2)
            self.assertEqual(data["persona"], "night")
            self.assertEqual(data["character"], "example")
            self.assertEqual(ss.list_sessions(Path(d))[0].persona, "night")

    def test_store_defaults_to_companion_dir(self):
        orig = cl._DATA
        with tempfile.TemporaryDirectory() as d:
            cl._DATA = Path(d)
            try:
                st = ss.SessionStore(session_id="s", model="m", voice="v", prompt_sha256="x",
                                     character="example")
                self.assertEqual(st.sessions_dir, Path(d) / "characters" / "example" / "sessions")
                st.snapshot([])
                self.assertTrue(st.path.exists())
                self.assertEqual(ss.all_sessions_dirs(), [Path(d) / "characters" / "example" / "sessions"])
            finally:
                cl._DATA = orig


class PersonaVariants(unittest.TestCase):
    def test_variant_paths(self):
        cdir = cl.character_dir("example")
        self.assertEqual(cl.persona_path("example"), cdir / "persona.md")
        self.assertEqual(cl.persona_path("example", "default"), cdir / "persona.md")
        self.assertEqual(cl.persona_path("example", "night"), cdir / "persona.night.md")
        for bad in ("../x", "a/b", ".hidden", "sp ace"):
            with self.assertRaises(cl.ConfigError):
                cl.persona_path("example", bad)

    def test_variant_composes(self):
        orig = cl._DATA
        with tempfile.TemporaryDirectory() as d:
            cl._DATA = Path(d)
            try:
                cd = Path(d) / "characters" / "example"
                cd.mkdir(parents=True)
                (cd / "persona.md").write_text("## IDENTITY\nday\n\n## SOUL\nsun\n")
                (cd / "persona.night.md").write_text("## IDENTITY\nnight\n\n## SOUL\nmoon\n")
                self.assertEqual(cl.compose_persona("example"), "day\n\nsun")
                self.assertEqual(cl.compose_persona("example", "night"), "night\n\nmoon")
                with self.assertRaises(cl.ConfigError):
                    cl.compose_persona("example", "missing")
            finally:
                cl._DATA = orig


if __name__ == "__main__":
    unittest.main()
