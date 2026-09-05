"""test_init.py — the first-run bootstrap (python -m hearth.init), run for real.

Every case runs the module as a subprocess with HEARTH_DATA at a temp dir and
HEARTH_ROOT at this checkout, so the SHIPPED templates are what gets copied —
a template edit that breaks the bootstrap fails here, not on a stranger's
machine. Proves:
  1. fresh data root: templates copied, token minted 0600 and printed ONCE,
     both serve gates set by surgery (one line + one table; comments intact),
     memory left absent, load_active + load_serve_config succeed (facade-startable)
  2. re-run: every step reports exists/unchanged, token not printed, bytes identical
  3. copy-on-write: a hand-written active.toml is never touched
  4. --memory on: memory.toml copied and enabled, template keys intact
  5. the probe: one advertised id is taken; several with --yes leave the
     placeholder and say so; --model-id skips the probe
  6. refusal: an unparseable serve.toml stops the run, names the file, and is
     left byte-identical

Run:  .venv/bin/python -m unittest tests/test_init.py
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from hearth.config import config_loader as cl

_PY = sys.executable
_ROOT = cl._ROOT
_HEX64 = re.compile(r"^\s+([0-9a-f]{64})$", re.M)


def _init(data: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("HEARTH_DATA", None)
    env.update(HEARTH_DATA=str(data), HEARTH_ROOT=str(_ROOT), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([_PY, "-m", "hearth.init", "--yes", *args],
                          capture_output=True, text=True, env=env, cwd=str(_ROOT),
                          stdin=subprocess.DEVNULL)


def _loads(data: Path, *parts: str) -> dict:
    return tomllib.loads(data.joinpath(*parts).read_text(encoding="utf-8"))


class _Models(BaseHTTPRequestHandler):
    ids: list[str] = []

    def do_GET(self):  # noqa: N802
        body = json.dumps({"data": [{"id": i} for i in self.ids]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class FirstRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name) / "data"
        self.data.mkdir()

    # 1 ────────────────────────────────────────────────────────────────────────
    def test_fresh_root_becomes_facade_startable(self):
        r = _init(self.data, "--no-probe")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in ("config/active.toml", "config/models/example/model.toml",
                    "config/serve.toml", "config/serve-token"):
            self.assertTrue((self.data / rel).is_file(), rel)
        self.assertFalse((self.data / "config" / "memory.toml").exists())

        tok = self.data / "config" / "serve-token"
        self.assertEqual(stat.S_IMODE(tok.stat().st_mode), 0o600)
        printed = _HEX64.findall(r.stdout)
        self.assertEqual(len(printed), 1, "token printed exactly once")
        self.assertEqual(printed[0], tok.read_text().strip())

        serve = _loads(self.data, "config", "serve.toml")["serve"]
        self.assertTrue(serve["enabled"])
        self.assertTrue(serve["supervisor"]["enabled"])
        # Surgery, not rewrite: the template's prose is still there.
        text = (self.data / "config" / "serve.toml").read_text()
        self.assertIn("# Loopback by default.", text)
        self.assertIn("#[serve.supervisor]", text)
        self.assertEqual(text.count("\nenabled = true"), 2)
        self.assertIn("http://127.0.0.1:65001/admin/launch", r.stdout)
        self.assertIn("your-model-id-here", r.stdout)  # the honest placeholder warning

        code = ("from hearth.config import config_loader as c; a=c.load_active(); "
                "s=c.load_serve_config(); print(s['enabled'], s['supervisor']['enabled'])")
        env = {**os.environ, "HEARTH_DATA": str(self.data), "HEARTH_ROOT": str(_ROOT)}
        chk = subprocess.run([_PY, "-c", code], capture_output=True, text=True, env=env)
        self.assertEqual(chk.stdout.strip(), "True True", chk.stderr[-600:])

    # 2 ────────────────────────────────────────────────────────────────────────
    def test_rerun_is_idempotent_and_silent_about_the_token(self):
        self.assertEqual(_init(self.data, "--no-probe").returncode, 0)
        before = {p: p.read_bytes() for p in self.data.rglob("*") if p.is_file()}
        r = _init(self.data, "--no-probe")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_HEX64.findall(r.stdout), [])
        self.assertIn("not shown again", r.stdout)
        self.assertNotIn("  + ", r.stdout)  # nothing created, nothing set
        after = {p: p.read_bytes() for p in self.data.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    # 3 ────────────────────────────────────────────────────────────────────────
    def test_existing_file_is_never_overwritten(self):
        cfg = self.data / "config"
        cfg.mkdir()
        mine = 'character = "zz-a"\nmodel = "example"\nvoice = "zz-v"\n'
        (cfg / "active.toml").write_text(mine)
        r = _init(self.data, "--no-probe")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((cfg / "active.toml").read_text(), mine)
        self.assertIn("config/active.toml — left as it is", r.stdout)

    # 4 ────────────────────────────────────────────────────────────────────────
    def test_memory_on_copies_and_enables(self):
        r = _init(self.data, "--no-probe", "--memory", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        mem = _loads(self.data, "config", "memory.toml")["memory"]
        self.assertTrue(mem["enabled"])
        self.assertEqual(mem["backend"], "floor")  # the template's other keys survive
        self.assertNotIn("memory stays off", r.stdout)

    # 5 ────────────────────────────────────────────────────────────────────────
    def _serve(self, ids: list[str]) -> str:
        _Models.ids = ids
        srv = HTTPServer(("127.0.0.1", 0), _Models)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}/v1"

    def test_probe_single_id_is_taken(self):
        url = self._serve(["zz-only-model"])
        r = _init(self.data, "--lm-url", url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_loads(self.data, "config", "models", "example", "model.toml")["id"],
                         "zz-only-model")
        self.assertEqual(_loads(self.data, "config", "serve.toml")["serve"]["lm_base_url"], url)
        self.assertNotIn("your-model-id-here", r.stdout)

    def test_probe_several_ids_unattended_keeps_placeholder(self):
        url = self._serve(["zz-a", "zz-b"])
        r = _init(self.data, "--lm-url", url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_loads(self.data, "config", "models", "example", "model.toml")["id"],
                         "your-model-id-here")
        self.assertIn("2 models advertised", r.stdout)

    def test_model_id_flag_skips_probe(self):
        r = _init(self.data, "--model-id", "zz-given", "--lm-url", "http://127.0.0.1:9/v1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_loads(self.data, "config", "models", "example", "model.toml")["id"],
                         "zz-given")
        self.assertNotIn("no LLM server answering", r.stdout)

    def test_unreachable_server_is_a_note_not_a_failure(self):
        r = _init(self.data, "--lm-url", "http://127.0.0.1:9/v1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no LLM server answering", r.stdout)

    # 6 ────────────────────────────────────────────────────────────────────────
    def test_unparseable_serve_toml_refuses_and_leaves_bytes(self):
        cfg = self.data / "config"
        cfg.mkdir()
        broken = "[serve\nenabled = false\n"
        (cfg / "serve.toml").write_text(broken)
        r = _init(self.data, "--no-probe")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config/serve.toml", r.stderr)
        self.assertIn("does not parse", r.stderr)
        self.assertEqual((cfg / "serve.toml").read_text(), broken)
        self.assertFalse((cfg / "serve-token").exists())


if __name__ == "__main__":
    unittest.main()
