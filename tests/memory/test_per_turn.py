"""Per-turn targeted recall (design lane (b)) — seam side and facade side.

The cue passthrough, the seam's augment_turn guards (gate, min chars, dedupe
against the open set), and the facade glue's per-request instruction with its
deadline and one-slot cue cache.

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

from hearth import memory as seam_mod  # noqa: E402
from hearth.memory import MemorySeam  # noqa: E402
from hearth.memory.backend import MemoryItem  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory.floor import FloorBackend  # noqa: E402
from hearth.serve import memory_glue as glue_mod  # noqa: E402


class _CueBackend:
    """Standing query recalls the open set; a 'knight' cue surfaces more."""

    name = "cuespy"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.fail_on_cue = False

    def recall(self, companion, query, limit):  # noqa: ANN001
        self.queries.append(query)
        targeted = "knight" in query.lower()
        if targeted and self.fail_on_cue:
            raise RuntimeError("bank down")
        vanilla = MemoryItem(text="Favorite ice cream is vanilla",
                             source_session="s1", when="2026-08-30")
        if targeted:
            return [MemoryItem(text="Favorite show as a kid: Knight Rider",
                               source_session="s9", when="2026-09-01"),
                    MemoryItem(text="Watched it on a wood-panel TV",
                               source_session="s9", when="2026-09-01"),
                    vanilla][:limit]
        return [vanilla]

    def store(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def consolidate(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def close(self):
        pass


class TestPerTurnRecall(unittest.TestCase):
    """Design lane (b): augment_turn = open block + labeled targeted extras,
    every guard falling back to the open composition byte-identically."""

    CUE = "hey, what was my favorite show as a kid? knight rider, maybe?"

    def _seam(self, backend, tmp: Path, **per_turn) -> MemorySeam:
        cfg = {"recall_limit": 3,
               "per_turn": {"enabled": True, "limit": 3, "min_cue_chars": 12,
                            **per_turn}}
        seam = MemorySeam("testchar", "default", backend, cfg)
        seam._floor = FloorBackend(tmp / "floor")
        return seam

    def test_extras_ride_a_labeled_line_and_dedupe_against_the_open_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            opened = seam.augment("BASE")
            self.assertIn("vanilla", opened)
            out = seam.augment_turn("BASE", "  hey,  what was my favorite show"
                                            " as a kid? knight rider, maybe?  ")
            self.assertIn(seam_mod._TURN_HEADER, out)
            self.assertIn("Knight Rider", out)
            self.assertIn("wood-panel", out)
            self.assertEqual(out.count("vanilla"), 1)        # deduped, never repeated
            self.assertTrue(out.startswith("BASE"))
            self.assertEqual(backend.queries[-1], self.CUE)  # the cue verbatim, normalized

    def test_guards_serve_the_open_composition_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            opened = seam.augment("BASE")
            self.assertEqual(seam.augment_turn("BASE", "hi"), opened)       # short cue
            self.assertEqual(seam.augment_turn("BASE", ""), opened)         # no cue
            # a cue whose answers all dedupe away ⇒ no label, same string
            dup = seam.augment_turn("BASE", "tell me about vanilla ice cream")
            self.assertEqual(dup, opened)
            self.assertNotIn(seam_mod._TURN_HEADER, dup)
            # gate off ⇒ the backend never even sees a turn query
            off = self._seam(_CueBackend(), Path(tmp), enabled=False)
            off_open = off.augment("BASE")
            self.assertEqual(off.augment_turn("BASE", self.CUE), off_open)
            self.assertEqual(len(off.backend.queries), 1)    # the open query only

    def test_per_turn_limit_caps_the_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            seam = self._seam(_CueBackend(), Path(tmp), limit=1)
            seam.augment("BASE")
            out = seam.augment_turn("BASE", self.CUE)
            self.assertIn("Knight Rider", out)
            self.assertNotIn("wood-panel", out)

    def test_turn_failure_costs_only_the_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            opened = seam.augment("BASE")
            backend.fail_on_cue = True
            self.assertEqual(seam.augment_turn("BASE", self.CUE), opened)   # no raise

    def test_intent_line_rides_every_composition_but_consumes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            seam = self._seam(_CueBackend(), Path(tmp))
            slot = Path(tmp) / "intent.json"
            slot.write_text("{}", encoding="utf-8")
            seam._intent = {"text": "the walk debrief",
                            "stated_at": "2026-08-31", "path": slot}
            opened = seam.augment("BASE")
            self.assertIn("you agreed to pick up the walk debrief", opened)
            self.assertFalse(slot.exists())                  # consumed at augment
            out = seam.augment_turn("BASE", self.CUE)
            self.assertEqual(out.count("you agreed to pick up"), 1)

    def test_floor_fallback_answers_and_is_attributed_honestly(self):
        # Run-observed 2026-09-02: the primary failed every voice turn, the
        # floor filled in, and the success log credited the primary. The seam
        # must name the backend that actually answered.
        class _AnsweringFloor:
            name = "floor"

            def recall(self, companion, query, limit):  # noqa: ANN001
                return [MemoryItem(text="A floor digest line",
                                   source_session="s2", when="2026-08-29")]

        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            seam._floor = _AnsweringFloor()
            seam.augment("BASE")
            backend.fail_on_cue = True
            items, source = seam.recall_turn(self.CUE)
            self.assertEqual(source, "floor")                # not the primary's name
            self.assertEqual([i.text for i in items], ["A floor digest line"])
            out = seam.augment_turn("BASE", self.CUE)        # extras still ride
            self.assertIn("A floor digest line", out)

    def test_recall_turn_names_the_primary_when_it_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            seam = self._seam(_CueBackend(), Path(tmp))
            seam.augment("BASE")
            _items, source = seam.recall_turn(self.CUE)
            self.assertEqual(source, "cuespy")


class TestServeGluePerTurn(unittest.TestCase):
    """The chat-lane glue over the seam: cue upgrades, one-slot cache, channel
    scope, and containment — offline, same harness shape as TestServeGlue."""

    def _cfg(self) -> dict:
        return {"recall_limit": 3, "backend": "stub", "companions": {},
                "per_turn": {"enabled": True, "limit": 3, "min_cue_chars": 12},
                "serve": {"enabled": True, "idle_close_voice": 5,
                          "idle_close_chat": 480, "checkpoint": True},
                "intent": {"enabled": False, "expiry_days": 14,
                           "llm_provider": "ollama", "llm_model": "testmodel",
                           "llm_url": "", "companions": {}}}

    def _env(self, tmp: Path, backend):
        root, recs = tmp / "characters", tmp / "records"
        for patcher in (
            mock.patch.object(glue_mod, "_checkpoint_root", return_value=root),
            mock.patch.object(glue_mod, "_build_backend", return_value=backend),
            mock.patch.object(records_mod, "records_dir", return_value=recs),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cue_upgrades_caches_scopes_and_contains(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            self._env(Path(tmp), backend)
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                base = "SYSTEM PROMPT"
                cue = "what was my favorite show as a kid? knight rider?"
                # the OPENING request already benefits: the cue rides call one
                first = await glue.instruction("testchar", "default", "chat", "",
                                               base, cue=cue)
                self.assertIn("Knight Rider", first)
                self.assertIn(seam_mod._TURN_HEADER, first)
                calls = len(backend.queries)
                # the same cue again ⇒ the one-slot cache answers, no new recall
                again = await glue.instruction("testchar", "default", "chat", "",
                                               base, cue=cue)
                self.assertEqual(again, first)
                self.assertEqual(len(backend.queries), calls)
                # a short cue serves the cached OPEN instruction
                open_str = await glue.instruction("testchar", "default", "chat", "",
                                                  base, cue="hi")
                self.assertNotIn("Knight Rider", open_str)
                self.assertIn("vanilla", open_str)
                # the voice channel is out of scope this stroke: no turn query
                voice_calls = len(backend.queries)
                voiced = await glue.instruction("testchar", "default", "voice", "",
                                                base, cue=cue)
                self.assertNotIn("Knight Rider", voiced)
                self.assertEqual(len(backend.queries), voice_calls + 1)  # its OPEN only
                # a failing targeted recall costs only the extras
                backend.fail_on_cue = True
                degraded = await glue.instruction(
                    "testchar", "default", "chat", "", base,
                    cue="knight rider, once more with feeling")
                self.assertEqual(degraded, open_str)
                await glue.stop()

            asyncio.run(scenario())

if __name__ == "__main__":
    unittest.main()
