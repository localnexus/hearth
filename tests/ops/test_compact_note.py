"""test_compact_note.py — compact-note.py against a fake OpenAI-style server.

Exercises `--model auto` (GET <base>/models, first id wins) against a real
HTTP server on 127.0.0.1:0, and confirms an explicit --model never touches
/models.

Run:  .venv/bin/python -m unittest discover -s tests -p "test_compact_note.py" -v
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess

_SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "compaction" / "compact-note.py"

_NOTE = (
    "## Where we left off\nfiller.\n\n"
    "## What matters\nfiller.\n\n"
    "## Open threads\nfiller.\n\n"
    "## Tone notes\nfiller.\n\n"
    "## Continuity\n" + ("filler word " * 380).strip() + "\n"
)


def _make_handler(models_data):
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
                self._reply({"data": models_data})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.server.requests.append(("POST", self.path, self.headers.get("Authorization")))
            self._reply(
                {
                    "choices": [{"message": {"content": _NOTE}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 400},
                }
            )

    return Handler


class CompactNoteAuto(unittest.TestCase):
    def _start(self, models_data):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(models_data))
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # addCleanup runs LIFO: registered in reverse of the wanted order so
        # shutdown (unblocks serve_forever) -> join -> server_close (socket).
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def _run(self, model, port):
        env = {
            **os.environ,
            "LM_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "LM_API_TOKEN": "t",
        }
        return subprocess.run(
            [sys.executable, str(_SCRIPT), "--model", model],
            input="a companion+partner transcript to condense",
            env=env,
            capture_output=True,
            text=True,
        )

    def test_auto_resolves_first_model_and_authenticates(self):
        server = self._start([{"id": "fake-resident"}])
        proc = self._run("auto", server.server_address[1])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, _NOTE.strip() + "\n")
        self.assertIn("resolved model 'fake-resident'", proc.stderr)
        methods = [r[0] for r in server.requests]
        self.assertIn("GET", methods)
        self.assertIn("POST", methods)
        for _, _, auth in server.requests:
            self.assertEqual(auth, "Bearer t")

    def test_auto_with_no_models_fails(self):
        server = self._start([])
        proc = self._run("auto", server.server_address[1])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no model resolved", proc.stderr)

    def test_explicit_model_never_calls_models_endpoint(self):
        server = self._start([{"id": "fake-resident"}])
        proc = self._run("fake-resident", server.server_address[1])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        methods = [r[0] for r in server.requests]
        self.assertNotIn("GET", methods)
        self.assertEqual(methods, ["POST"])


if __name__ == "__main__":
    unittest.main()
