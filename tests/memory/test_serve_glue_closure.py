"""Facade-lane glue (serve/memory_glue.py) — closure, opt-out, and containment.

Deliberate closure and its staleness guard, the cheap pre-filter in front of the
extraction seat, a companion mapped to "none", checkpoint=false, and the rule
that a failing backend never breaks a turn or a close.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import intent as intent_mod  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.serve import memory_glue as glue_mod  # noqa: E402


class _BoomBackend:
    name = "boom"

    def recall(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("backend down")

    def store(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("backend down")

    def consolidate(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("backend down")

    def close(self):
        raise RuntimeError("backend down")


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


class TestServeGlueClosure(unittest.TestCase):
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

    def test_deliberate_closure_closes_the_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "", "P")
                with mock.patch.object(glue_mod.intent_mod, "detect_closure_and_topic",
                                       return_value=(True, None)) as detect:
                    glue.note_exchange("testchar", "chat", "", "hello", "hi")
                    await glue.drain()
                    detect.assert_not_called()  # an opening exchange is never a goodbye
                    self.assertIn(self.KEY, glue._sessions)

                    glue.note_exchange("testchar", "chat", "", "goodnight", "sleep well")
                    await glue.drain()
                    self.assertEqual(detect.call_count, 1)
                self.assertEqual(glue._sessions, {})
                await glue.stop()

            asyncio.run(scenario())
            got = list(records_mod.iter_records("testchar", recs))
            self.assertEqual(len(got), 1)
            self.assertEqual(len(got[0].messages), 4)  # the whole conversation, verbatim

    def test_closure_prefilter_and_the_newer_exchange_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "", "P")
                await glue.instruction("testchar", "default", "voice", "", "P")
                glue.note_exchange("testchar", "chat", "", "hello", "hi")
                glue.note_exchange("testchar", "voice", "", "hello", "hi")
                with mock.patch.object(glue_mod.intent_mod, "detect_closure_and_topic",
                                       return_value=(True, None)) as detect:
                    glue.note_exchange("testchar", "chat", "", "what about tomorrow?", "sure")
                    glue.note_exchange("testchar", "chat", "", "x" * 400, "long one")
                    glue.note_exchange("testchar", "voice", "", "bye", "bye")
                    await glue.drain()
                    # a question, an essay, and the voice channel: none reach the seat
                    detect.assert_not_called()
                    self.assertIn(self.KEY, glue._sessions)

                    # the guard: the conversation moved on while the seat was asked
                    session = glue._sessions[self.KEY]
                    stale = session.seq
                    session.seq += 1
                    await glue._closure_check(self.KEY, session, stale)
                    self.assertIn(self.KEY, glue._sessions)  # skipped — it continued
                    await glue._closure_check(self.KEY, session, session.seq)
                    self.assertNotIn(self.KEY, glue._sessions)  # current ⇒ closed
                await glue.stop()

            asyncio.run(scenario())

    def test_companion_mapped_to_none_gets_no_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            cfg = self._cfg()
            cfg["companions"] = {"testchar": "none"}
            glue = glue_mod.ServeMemory(cfg, clock=lambda: 0.0)

            async def scenario():
                self.assertEqual(
                    await glue.instruction("testchar", "default", "chat", "", "SYSTEM PROMPT"),
                    "SYSTEM PROMPT")
                self.assertEqual(glue._sessions, {})
                glue.note_exchange("testchar", "chat", "", "hello", "hi")  # a no-op
                await glue.drain()
                await glue.stop()

            asyncio.run(scenario())
            self.assertFalse(recs.exists())
            self.assertEqual(backend.stored, [])

    def test_checkpoint_false_keeps_the_lane_off_disk_until_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(checkpoint=False), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "", "P")
                glue.note_exchange("testchar", "chat", "", "hello", "hi")
                await glue.drain()
                self.assertFalse((root / "testchar").exists())
                await glue.stop()

            asyncio.run(scenario())
            self.assertEqual(len(list(records_mod.iter_records("testchar", recs))), 1)

    def test_a_failing_backend_never_breaks_a_turn_or_a_close(self):
        """Containment: recall, store, consolidate and close all
        raise — the turn is still answered and the canonical record still
        lands."""
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp), backend=_BoomBackend())
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)
            checkpoint = root / "testchar" / "memory" / "checkpoints" / "serve-chat.json"

            async def scenario():
                base = "SYSTEM PROMPT"
                self.assertEqual(
                    await glue.instruction("testchar", "default", "chat", "", base), base)
                glue.note_exchange("testchar", "chat", "", "hello", "hi")
                await glue.drain()
                await glue.close_session(self.KEY)  # store + consolidate raise
                self.assertFalse(checkpoint.exists())
                await glue.stop()                   # backend.close raises

            asyncio.run(scenario())
            self.assertEqual(len(list(records_mod.iter_records("testchar", recs))), 1)

if __name__ == "__main__":
    unittest.main()
