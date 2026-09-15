"""test_session_fork.py — resume is a fork: a kept file is immutable to
sittings, keeping a fork supersedes the original, and an orphan never
blocks a start.

Run:  .venv/bin/python -m unittest tests.test_session_fork
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from hearth.session import session_cli, session_store


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_kept(tmp: Path, sid: str, *, title=None, messages=None) -> Path:
    """A kept (held) file on the shelf, written as a known fixture so its
    bytes can be hashed before/after a fork."""
    path = tmp / f"{sid}.json"
    payload = {
        "schema": 2, "model": "m", "voice": "v", "persona": "default",
        "started": "2026-01-01T00-00-00", "updated": "2026-01-01T00-00-00",
        "held": True, "retain": True,
    }
    if title:
        payload["title"] = title
    payload["messages"] = messages if messages is not None else [
        {"role": "user", "content": "placeholder one"},
        {"role": "assistant", "content": "placeholder two"},
    ]
    session_store._atomic_write_json(path, payload)
    return path


class ForkSession(unittest.TestCase):
    def test_fork_writes_new_file_and_leaves_source_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = _write_kept(tmp, "session-kept-a", title="my topic")
            before = _sha256(source)

            store, data = session_store.fork_session(
                source, model="m2", voice="v2", prompt_sha256="deadbeef",
                character="alpha", persona="default", sessions_dir=tmp)

            self.assertEqual(_sha256(source), before, "the source is opened read-only")
            self.assertNotEqual(store.path, source)
            self.assertTrue(store.path.exists())

            written = session_store.load(store.path)
            self.assertEqual(written["forked_from"], "session-kept-a")
            self.assertEqual(len(data["messages"]), 2)
            self.assertEqual(written["banked_from"], len(data["messages"]))
            self.assertFalse(written["retain"])
            self.assertFalse(written["held"])
            self.assertEqual(written["title"], "my topic")

    def test_finalize_kept_deletes_source_and_keeps_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = _write_kept(tmp, "session-kept-b")
            store, data = session_store.fork_session(
                source, model="m2", voice="v2", prompt_sha256="deadbeef",
                sessions_dir=tmp)
            store.retain = True
            out = {}
            session_store.finalize(store, data["messages"], outcome=out)
            self.assertFalse(source.exists(), "the source is true-deleted on keep")
            self.assertTrue(store.path.exists())
            written = session_store.load(store.path)
            self.assertEqual(written["forked_from"], "session-kept-b")
            self.assertEqual(out.get("superseded"), "session-kept-b")

    def test_finalize_unkept_deletes_fork_and_leaves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = _write_kept(tmp, "session-kept-c")
            before = _sha256(source)
            store, data = session_store.fork_session(
                source, model="m2", voice="v2", prompt_sha256="deadbeef",
                sessions_dir=tmp)
            session_store.finalize(store, data["messages"])
            self.assertFalse(store.path.exists(), "an unkept fork is deleted at close")
            self.assertTrue(source.exists(), "the original was never touched")
            self.assertEqual(_sha256(source), before)

    def test_resolve_session_resume_returns_a_fork(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = _write_kept(tmp, "session-kept-d")
            args = types.SimpleNamespace(resume=str(source), new=False)
            with mock.patch.object(session_store, "companion_sessions_dir", return_value=tmp):
                store, messages, descriptor = session_cli.resolve_session(
                    args, "m2", "v2", "prompt text", character="alpha")
            self.assertEqual(store.forked_from, "session-kept-d")
            self.assertNotEqual(store.path, source)

    def test_noninteractive_start_never_blocked_by_an_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            orphan = session_store.SessionStore(
                session_id="session-orphan", model="m", voice="v",
                prompt_sha256="x" * 64, sessions_dir=tmp)
            orphan.snapshot([{"role": "user", "content": "left behind"}])
            args = types.SimpleNamespace(resume=None, new=False)
            buf = io.StringIO()
            with mock.patch.object(session_store, "companion_sessions_dir", return_value=tmp), \
                 mock.patch("os.isatty", return_value=False), \
                 redirect_stdout(buf):
                store, messages, descriptor = session_cli.resolve_session(
                    args, "m2", "v2", "prompt text", character="alpha")
            self.assertEqual(descriptor, "New")
            self.assertIsNone(messages)
            self.assertIn("waiting", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
