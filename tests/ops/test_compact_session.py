"""test_compact_session.py — the standalone compact_session.py tool.

Loaded by path (it is a script, not a package) against a tmp sessions dir
fixture: a held session `s1` with 30 alternating filler messages. Never a
real companion name (fixtures use `demo` / `s1`).

Run:  .venv/bin/python -m unittest discover -s tests -p "test_compact_session.py" -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TOOL_PATH = (
    Path(__file__).resolve().parents[2] / "ops" / "compaction" / "compact_session.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("compact_session_tool", _TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cs = _load_tool()

_BODY = "\n\n".join(f"## Section {c}\nnote text for section {c}." for c in "ABCDE") + "\n"


class CompactSessionTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sessions_dir = Path(self._tmp.name)
        self.messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"filler {i}"}
            for i in range(30)
        ]
        self.session = {
            "schema": 1,
            "name": "s1",
            "character": "demo",
            "held": True,
            "messages": self.messages,
        }
        (self.sessions_dir / "s1.json").write_text(
            json.dumps(self.session), encoding="utf-8"
        )
        self.body_path = self.sessions_dir / "body.md"
        self.body_path.write_text(_BODY, encoding="utf-8")

    # (a) backup writes pre-compaction-bak/<today>/s1.json
    def test_backup_session_writes_dated_bak(self):
        dest = cs.backup_session("s1", sessions_dir=self.sessions_dir)
        today = time.strftime("%Y.%m.%d", time.localtime())
        expected = self.sessions_dir / "pre-compaction-bak" / today / "s1.json"
        self.assertEqual(dest, expected)
        self.assertTrue(expected.is_file())
        self.assertEqual(
            json.loads(expected.read_text(encoding="utf-8"))["messages"],
            self.messages,
        )

    # (b) compact with tail=4 over a 5-section body rewrites the live file
    def test_compact_session_rewrites_live_file(self):
        result = cs.compact_session(
            "s1",
            from_path=self.body_path,
            sessions_dir=self.sessions_dir,
            tail=4,
            method="test-method",
        )
        live = json.loads((self.sessions_dir / "s1.json").read_text(encoding="utf-8"))
        # preamble (meta-user + assistant body) + unstripped 4-message tail
        # (the tail's leading role is "user", so _trim_leading_assistants is a no-op)
        self.assertEqual(len(live["messages"]), 6)
        self.assertEqual(result["post_message_count"], 6)
        self.assertIn("compaction", live)
        self.assertEqual(live["compaction"]["method"], "test-method")
        today = time.strftime("%Y.%m.%d", time.localtime())
        bak = self.sessions_dir / "pre-compaction-bak" / today / "s1.json"
        self.assertTrue(bak.is_file())

    # (c) restore-from-bak brings the message count back to 30
    def test_restore_from_bak_recovers_original_count(self):
        cs.compact_session(
            "s1", from_path=self.body_path, sessions_dir=self.sessions_dir, tail=4,
        )
        cs.restore_from_bak("s1", sessions_dir=self.sessions_dir)
        live = json.loads((self.sessions_dir / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(len(live["messages"]), 30)

    # (d) stats surfaces bytes / msg_count / held
    def test_session_stats_fields(self):
        stats = cs.session_stats("s1", sessions_dir=self.sessions_dir)
        self.assertEqual(stats["msg_count"], 30)
        self.assertTrue(stats["held"])
        self.assertEqual(stats["bytes"], (self.sessions_dir / "s1.json").stat().st_size)

    # (e) CLI: --sessions-dir required
    # NB: --sessions-dir is a top-level (not per-subcommand) option, and
    # argparse's PARSER-nargs subparsers positional swallows everything from
    # the subcommand token onward before the top level's own required-arg
    # check runs — so the flag must precede the subcommand, not follow it.
    def test_cli_stats_requires_sessions_dir(self):
        rc = cs.main(["--sessions-dir", str(self.sessions_dir), "stats", "s1"])
        self.assertEqual(rc, 0)
        with self.assertRaises(SystemExit):
            cs.main(["stats", "s1"])


if __name__ == "__main__":
    unittest.main()
