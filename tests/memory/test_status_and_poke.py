"""Seam status and the per-turn voice valve.

The JSON-safe status the panel's memory line reads (names, counts, gates,
timestamps — never content), and the runtime poke that pauses and resumes the
voice lane's per-turn recall mid-sitting.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import MemorySeam  # noqa: E402
from hearth.memory.backend import MemoryItem, SessionRecord  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory.floor import FloorBackend  # noqa: E402


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


def _record(sid: str, ended: str, n_turns: int = 2, name: str = "") -> SessionRecord:
    messages = []
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"user line {i} of {sid}"})
        messages.append({"role": "assistant", "content": f"assistant line {i} of {sid}"})
    return SessionRecord(
        companion="testchar", session_id=sid, started="2026-08-29T10:00:00",
        ended=ended, name=name, messages=messages,
    )


class TestSeamStatus(unittest.TestCase):
    """The panel's read-only memory tap (surfacing prelim 3): status() is
    JSON-safe, carries names/counts/gates only — never message content — and
    attributes each recall to the backend that ACTUALLY answered."""

    CUE = "hey, what was my favorite show as a kid? knight rider, maybe?"
    # kept identical to the per-turn suite's cue — both exercise the same guard

    def _seam(self, backend, tmp: Path, **per_turn) -> MemorySeam:
        cfg = {"recall_limit": 3,
               "per_turn": {"enabled": True, "limit": 3, "min_cue_chars": 12,
                            **per_turn}}
        seam = MemorySeam("testchar", "default", backend, cfg)
        seam._floor = FloorBackend(tmp / "floor")
        return seam

    def test_status_is_json_safe_and_content_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp), voice=True)
            seam.augment("BASE")
            seam.augment_turn("BASE", self.CUE)
            s = seam.status()
            payload = json.dumps(s)               # JSON-safe end to end
            self.assertEqual(s["backend"], backend.name)
            self.assertEqual(s["companion"], "testchar")
            self.assertTrue(s["retain"])
            self.assertEqual(s["per_turn"], {"chat": True, "voice": True, "limit": 3})
            self.assertEqual(s["open_recall"]["source"], backend.name)
            self.assertEqual(s["turn_recall"]["source"], backend.name)
            self.assertGreater(s["turn_recall"]["extras"], 0)
            # Discipline: recalled TEXT never rides the status payload.
            self.assertNotIn("vanilla", payload)
            self.assertNotIn("Knight Rider", payload)
            self.assertNotIn(self.CUE, payload)

    def test_voice_gate_reports_effective_not_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            # voice=true but enabled=false ⇒ the voice lane never lights
            # (bot.py requires BOTH) — status must say so.
            seam = self._seam(_CueBackend(), Path(tmp), enabled=False, voice=True)
            s = seam.status()
            self.assertFalse(s["per_turn"]["chat"])
            self.assertFalse(s["per_turn"]["voice"])

    def test_open_recall_attribution_names_the_answering_rung(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            seam = MemorySeam("testchar", "default", _BoomBackend(), {"recall_limit": 3})
            seam._floor = FloorBackend(d)
            self.assertIsNone(seam.status()["open_recall"])   # nothing ran yet
            seam.recall()                       # primary raises → floor answers
            got = seam.status()["open_recall"]
            self.assertEqual(got["source"], "floor")
            self.assertEqual(got["count"], 1)
            self.assertIn("at", got)

    def test_turn_recall_attribution_and_guard_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            seam.augment("BASE")
            self.assertIsNone(seam.status()["turn_recall"])   # no turn yet
            backend.fail_on_cue = True
            seam.augment_turn("BASE", self.CUE)  # primary fails → floor ([] here)
            got = seam.status()["turn_recall"]
            self.assertEqual(got["source"], "floor")          # never the primary
            self.assertEqual(got["extras"], 0)
            # A guard-skipped turn (short cue) leaves the last record standing.
            seam.augment_turn("BASE", "hi")
            self.assertEqual(seam.status()["turn_recall"], got)


class TestPerTurnVoicePoke(unittest.TestCase):
    """The panel memory tap's ONE runtime knob (decision signed 2026-09-02,
    runtime-only): POST /memory/per-turn-voice pokes the LIVE seam's voice
    gate — no file write — and every refusal on the gate ladder is honest
    (unwired 503, no seam / processor-not-built / chat-gate-off 409s)."""

    def _app_client(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from hearth.control.features import memory_status as ms

        app = web.Application()
        app.add_routes(ms.memory_status_routes(None))
        return ms, TestClient(TestServer(app))

    def _run(self, coro):
        return asyncio.run(coro)

    def _seam(self, enabled=True, voice=True):
        import types
        return types.SimpleNamespace(
            per_turn_enabled=enabled, per_turn_voice=voice,
            status=lambda: {"per_turn": {"chat": enabled, "voice": voice}})

    def _wired(self, ms, seam, built=True):
        import types
        ms.attach(types.SimpleNamespace(current_seam=seam), "full",
                  voice_prefetch_built=built)
        self.addCleanup(ms.attach, None, None, False)

    def test_unwired_answers_503(self):
        async def go():
            ms, client = self._app_client()
            ms.attach(None, None, False)
            async with client:
                self.assertEqual((await client.post(
                    "/memory/per-turn-voice", json={"on": False})).status, 503)
        self._run(go())

    def test_gate_ladder_refusals_are_honest(self):
        async def go():
            ms, client = self._app_client()
            async with client:
                self._wired(ms, seam=None, built=False)
                r = await client.post("/memory/per-turn-voice", json={"on": False})
                self.assertEqual(r.status, 409)  # no seam this sitting
                self._wired(ms, self._seam(), built=False)
                r = await client.post("/memory/per-turn-voice", json={"on": True})
                self.assertEqual(r.status, 409)
                self.assertIn("restart", (await r.json())["error"])  # names the fix
                self._wired(ms, self._seam(enabled=False), built=True)
                r = await client.post("/memory/per-turn-voice", json={"on": True})
                self.assertEqual(r.status, 409)  # chat gate off
                r = await client.post("/memory/per-turn-voice", json={"on": "yes"})
                self.assertEqual(r.status, 400)  # bool required, no coercion
        self._run(go())

    def test_poke_flips_the_live_seam_and_status_reflects_it(self):
        async def go():
            ms, client = self._app_client()
            seam = self._seam(voice=True)
            async with client:
                self._wired(ms, seam, built=True)
                r = await client.post("/memory/per-turn-voice", json={"on": False})
                self.assertEqual(r.status, 200)
                d = await r.json()
                self.assertFalse(seam.per_turn_voice)          # the actual poke
                self.assertIn("memory.toml unchanged", d["note"])
                g = await (await client.get("/memory")).json()
                self.assertTrue(g["voice_prefetch_built"])      # the panel's self-gate
                r = await client.post("/memory/per-turn-voice", json={"on": True})
                self.assertEqual(r.status, 200)
                self.assertTrue(seam.per_turn_voice)            # and back on
        self._run(go())

if __name__ == "__main__":
    unittest.main()
