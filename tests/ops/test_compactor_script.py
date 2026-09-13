"""test_compactor_script.py — end-to-end run of the compactor script against a
fake resident model server standing in for the companion's own door.

Run:  .venv/bin/python -m unittest discover -s tests -p "test_compactor_script.py" -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPACTION = _REPO_ROOT / "ops" / "compaction"
_SCRIPT = _COMPACTION / "compact-companion-session.sh"
_SHELL_FILES = [
    _SCRIPT,
    _COMPACTION / "compact-model-door.sh",
    _COMPACTION / "compact-model-llama.sh",
    _COMPACTION / "compact-queue-lib.sh",
]

_HEADINGS = [
    "Where we left off",
    "What matters",
    "Open threads",
    "Tone notes",
    "Continuity",
]


def _build_note(target_words: int = 400) -> str:
    """Exactly five '## ' sections, padded to land inside the 380-420 band."""
    sections = [f"## {h}\ncontent about {h.lower()}." for h in _HEADINGS]
    body = "\n\n".join(sections)
    pad = target_words - len(body.split())
    if pad > 0:
        body += " " + " ".join(["filler"] * pad)
    return body + "\n"


_NOTE = _build_note()


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.server.requests.append(("GET", self.path, self.headers.get("Authorization")))
            if self.path == "/v1/models":
                self._reply({"data": [{"id": "fake-resident"}]})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.server.requests.append(("POST", self.path, self.headers.get("Authorization")))
            if self.path == "/v1/chat/completions":
                self._reply(
                    {
                        "choices": [{"message": {"content": _NOTE}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 400},
                    }
                )
            else:
                self.send_response(404)
                self.end_headers()

    return Handler


_SESSION_MESSAGES = [
    {"role": "user" if i % 2 == 0 else "assistant", "content": f"filler {i}"}
    for i in range(30)
]


class CompactorScriptEndToEnd(unittest.TestCase):
    def _start_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler())
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # addCleanup runs LIFO: registered in reverse of the wanted order so
        # shutdown (unblocks serve_forever) -> join -> server_close (socket).
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def _make_root(self, base_url: str) -> Path:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "config").mkdir(parents=True)
        (root / "logs").mkdir()
        (root / "locks").mkdir()
        tok = root / "config" / "tok"
        tok.write_text("t", encoding="utf-8")
        (root / "config" / "serve.toml").write_text(
            f'[serve]\nlm_base_url = "{base_url}"\nlm_token_source = "{tok}"\n',
            encoding="utf-8",
        )
        sessions = root / "characters" / "demo" / "sessions"
        sessions.mkdir(parents=True)
        session = {
            "schema": 1,
            "name": "s1",
            "character": "demo",
            "held": True,
            "messages": _SESSION_MESSAGES,
        }
        (sessions / "s1.json").write_text(json.dumps(session), encoding="utf-8")
        return root

    def _run(self, root: Path, extra_args: list[str] | None = None):
        env = {**os.environ, "HEARTH_DATA": str(root), "HEARTH_PYTHON": sys.executable}
        argv = [str(_SCRIPT), "s1", "--character", "demo", "--yes", "--tail", "4"]
        argv += extra_args or []
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)

    # (a) default engine (door): rc 0, applied compaction, note fetched from
    # the fake resident server.
    def test_default_engine_door_compacts_end_to_end(self):
        server = self._start_server()
        root = self._make_root(f"http://127.0.0.1:{server.server_address[1]}/v1")
        proc = self._run(root)
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

        live = json.loads((root / "characters" / "demo" / "sessions" / "s1.json").read_text())
        self.assertEqual(len(live["messages"]), 6)
        self.assertEqual(live["compaction"]["method"], "local-door-scripted")

        baks = list((root / "characters" / "demo" / "sessions" / "pre-compaction-bak").glob("*/s1.json"))
        self.assertEqual(len(baks), 1)

        bodies = list((root / "ops" / "compaction" / "bodies").glob("*.md"))
        self.assertEqual(len(bodies), 1)

        got_auth_get = any(
            method == "GET" and path == "/v1/models" and auth == "Bearer t"
            for method, path, auth in server.requests
        )
        got_auth_post = any(
            method == "POST" and path == "/v1/chat/completions" and auth == "Bearer t"
            for method, path, auth in server.requests
        )
        self.assertTrue(got_auth_get, server.requests)
        self.assertTrue(got_auth_post, server.requests)

        self._case_a_stdout = proc.stdout

    # (b) --engine lms is the operator lane and does not ship.
    def test_engine_lms_refused(self):
        server = self._start_server()
        root = self._make_root(f"http://127.0.0.1:{server.server_address[1]}/v1")
        proc = self._run(root, ["--engine", "lms"])
        self.assertEqual(proc.returncode, 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        self.assertIn("operator lane", proc.stdout + proc.stderr)

    # (c) the model server is not up: fails with "not up at".
    def test_door_not_up_fails(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler())
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        server.shutdown()
        thread.join()
        server.server_close()

        root = self._make_root(f"http://127.0.0.1:{port}/v1")
        proc = self._run(root)
        self.assertEqual(proc.returncode, 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        self.assertIn("not up at", proc.stdout + proc.stderr)

    # (d) every sourced/shipped shell file parses, and the compactor stays
    # under its 15,000-byte cap.
    def test_shell_files_parse_and_size_cap(self):
        for path in _SHELL_FILES:
            result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")
        self.assertLessEqual(os.path.getsize(_SCRIPT), 15000)


if __name__ == "__main__":
    unittest.main()
