"""Facade-lane glue (serve/memory_glue.py) — the session lifecycle.

Session keys and hint sanitization, open -> append -> checkpoint -> close (the
record named "facade <channel>", the checkpoint removed), the idle sweep at
5 min voice / 480 min chat, and orphan finalization stamped at the crash.
All offline: no sockets, no sleeps, an injected clock instead of waiting.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import tempfile
import time
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import records as records_mod  # noqa: E402
from hearth.serve import memory_glue as glue_mod  # noqa: E402


class _StubBackend:
    """A backend that records what it was handed and nothing else."""

    name = "stub"

    def __init__(self) -> None:
        self.stored: list = []
        self.consolidated = 0
        self.closed = 0

    def recall(self, companion, query, limit):  # noqa: ANN001
        return []

    def store(self, companion, record):  # noqa: ANN001
        self.stored.append(record)

    def consolidate(self, companion):  # noqa: ANN001
        self.consolidated += 1

    def close(self):
        self.closed += 1


class TestServeGlue(unittest.TestCase):
    """The facade-lane session manager (serve/memory_glue.py).

    Fully offline: a stub backend, a scratch data tree, and an INJECTED clock so
    the idle sweep is tested without waiting on one.
    """

    KEY = ("testchar", "chat", "")

    def _cfg(self, **serve_over) -> dict:
        serve = {"enabled": True, "idle_close_voice": 5,
                 "idle_close_chat": 480, "checkpoint": True}
        serve.update(serve_over)
        return {"recall_limit": 3, "backend": "stub", "companions": {}, "serve": serve,
                "intent": {"enabled": False, "expiry_days": 14, "llm_provider": "ollama",
                           "llm_model": "testmodel", "llm_url": "", "companions": {}}}

    def _env(self, tmp: Path, backend=None):
        """Point the glue at a scratch tree; returns (checkpoint root, records, backend)."""
        root, recs = tmp / "characters", tmp / "records"
        backend = backend if backend is not None else _StubBackend()
        for patcher in (
            mock.patch.object(glue_mod, "_checkpoint_root", return_value=root),
            mock.patch.object(glue_mod, "_build_backend", return_value=backend),
            mock.patch.object(records_mod, "records_dir", return_value=recs),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return root, recs, backend

    def test_open_append_checkpoint_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)
            checkpoint = root / "testchar" / "memory" / "checkpoints" / "serve-chat.json"

            async def scenario():
                base = "SYSTEM PROMPT"
                # nothing recalled ⇒ the instruction is byte-identical
                self.assertEqual(
                    await glue.instruction("testchar", "default", None, None, base), base)
                self.assertIn(self.KEY, glue._sessions)
                # a later turn of the same conversation reuses the entry
                self.assertEqual(
                    await glue.instruction("testchar", "default", "chat", "", base), base)
                self.assertEqual(len(glue._sessions), 1)

                glue.note_exchange("testchar", "chat", "", "hello", "hi there")
                await glue.drain()
                self.assertTrue(checkpoint.is_file())
                self.assertEqual(stat.S_IMODE(checkpoint.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(checkpoint.parent.stat().st_mode), 0o700)
                self.assertEqual(list(checkpoint.parent.glob("*.tmp")), [])  # atomic
                data = json.loads(checkpoint.read_text(encoding="utf-8"))
                self.assertEqual(data["companion"], "testchar")
                self.assertEqual(data["channel"], "chat")
                self.assertEqual(data["persona"], "default")
                self.assertTrue(data["started"])
                self.assertTrue(data["session_id"].startswith("serve-chat-"))
                self.assertEqual(data["turns"],
                                 [{"role": "user", "content": "hello"},
                                  {"role": "assistant", "content": "hi there"}])

                self.assertEqual(backend.closed, 0)  # the seam is dropped, never closed
                await glue.close_session(self.KEY)
                self.assertFalse(checkpoint.exists())  # the transient is gone
                self.assertEqual(glue._sessions, {})
                await glue.stop()

            asyncio.run(scenario())
            got = list(records_mod.iter_records("testchar", recs))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].name, "facade chat")
            self.assertEqual(got[0].persona, "default")
            self.assertTrue(got[0].session_id.startswith("serve-chat-"))
            self.assertEqual(got[0].messages,
                             [{"role": "user", "content": "hello"},
                              {"role": "assistant", "content": "hi there"}])
            self.assertEqual(len(backend.stored), 1)
            self.assertEqual(backend.closed, 1)  # exactly once, at facade shutdown

    def test_session_hint_subdivides_the_channel_and_dirty_hints_are_hashed(self):
        dirty = "../../etc/passwd"
        digest = hashlib.sha256(dirty.encode("utf-8")).hexdigest()[:12]
        self.assertEqual(glue_mod.session_key("c", "voice", "walk-1"), ("c", "voice", "walk-1"))
        self.assertEqual(glue_mod.session_key("c", "smoke-signal", None), ("c", "chat", ""))
        self.assertEqual(glue_mod.session_key("c", "chat", dirty)[2], digest)
        self.assertEqual(glue_mod.sanitize_hint("x" * 65),
                         hashlib.sha256(("x" * 65).encode("utf-8")).hexdigest()[:12])

        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "thread-7", "P")
                await glue.instruction("testchar", "default", "chat", dirty, "P")
                self.assertEqual(sorted(glue._sessions),
                                 sorted([("testchar", "chat", "thread-7"),
                                         ("testchar", "chat", digest)]))
                glue.note_exchange("testchar", "chat", "thread-7", "a", "b")
                glue.note_exchange("testchar", "chat", dirty, "c", "d")
                await glue.drain()
                names = sorted(p.name for p in
                               (root / "testchar" / "memory" / "checkpoints").glob("*.json"))
                self.assertEqual(names, sorted([f"serve-chat-{digest}.json",
                                                "serve-chat-thread-7.json"]))
                await glue.stop()

            asyncio.run(scenario())
            ids = [r.session_id for r in records_mod.iter_records("testchar", recs)]
            self.assertEqual(len(ids), 2)
            self.assertTrue(any(i.startswith(f"serve-chat-{digest}-") for i in ids))
            self.assertTrue(any(i.startswith("serve-chat-thread-7-") for i in ids))

    def test_idle_sweep_closes_voice_at_five_minutes_and_chat_at_eight_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            now = [0.0]
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: now[0])
            voice, chat = ("testchar", "voice", ""), ("testchar", "chat", "")

            async def scenario():
                await glue.instruction("testchar", "default", "voice", "", "P")
                await glue.instruction("testchar", "default", "chat", "", "P")
                glue.note_exchange("testchar", "voice", "", "walking", "with you")
                glue.note_exchange("testchar", "chat", "", "desk line", "desk reply")
                await glue.drain()

                now[0] = 4 * 60.0
                await glue.sweep()
                self.assertEqual(sorted(glue._sessions), sorted([voice, chat]))

                now[0] = 5 * 60.0                     # idle_close_voice
                await glue.sweep()
                self.assertEqual(list(glue._sessions), [chat])

                now[0] = 479 * 60.0
                await glue.sweep()
                self.assertEqual(list(glue._sessions), [chat])

                now[0] = 480 * 60.0                   # idle_close_chat (the fallback)
                await glue.sweep()
                self.assertEqual(glue._sessions, {})
                await glue.stop()

            asyncio.run(scenario())
            self.assertEqual(sorted(r.name for r in records_mod.iter_records("testchar", recs)),
                             ["facade chat", "facade voice"])

    def test_orphan_checkpoint_becomes_a_record_stamped_at_the_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            checkpoints = root / "testchar" / "memory" / "checkpoints"
            checkpoints.mkdir(parents=True)
            path = checkpoints / "serve-voice.json"
            path.write_text(json.dumps({
                "schema": 1, "kind": "memory-checkpoint", "companion": "testchar",
                "persona": "default", "channel": "voice",
                "started": "2026-08-30T09:00:00",
                "session_id": "serve-voice-20260830T090000",
                "turns": [{"role": "user", "content": "still walking"},
                          {"role": "assistant", "content": "I am here"}],
            }), encoding="utf-8")
            crashed = time.mktime((2026, 8, 30, 9, 42, 0, 0, 0, -1))
            os.utime(path, (crashed, crashed))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.start()   # the orphan pass is scheduled, not awaited
                await glue.drain()
                await glue.stop()

            asyncio.run(scenario())
            self.assertFalse(path.exists())
            got = list(records_mod.iter_records("testchar", recs))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].session_id, "serve-voice-20260830T090000")
            self.assertEqual(got[0].name, "facade voice")
            self.assertEqual(got[0].started, "2026-08-30T09:00:00")
            # ended is when the facade DIED, not when it came back
            self.assertEqual(got[0].ended[:19], "2026-08-30T09:42:00")
            self.assertEqual(len(got[0].messages), 2)

if __name__ == "__main__":
    unittest.main()
