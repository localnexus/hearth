"""test_memory_prefetch.py — the voice-lane per-turn recall processor (lane (b)).

Prefetch-behind: recall runs in the
background after turn N, its extras injected before turn N+1 — zero added
latency, one-turn lag. Proves:

  1. cue extraction — last user turn, string and multimodal-parts content
  2. launch  — a long, new cue schedules recall; the (cue, block) lands in _pending
  3. guards  — a below-floor cue launches nothing (nothing rides: the block is
               ephemeral); a repeat cue re-rides the last block without a recall
  4. rebase  — a changed seam (live switch) drops the old prefetch
  5. gate    — voice OFF (or per-turn OFF) launches nothing and pends nothing
  6. frames  — through real pipecat run_test: the prefetched block rides the
               newest user message of a REQUEST COPY of the context; the live
               context is untouched; no settings frame is ever pushed (the
               system instruction stays byte-stable — 2026-09-05 measurement)

Run:  ./.venv/bin/python tests/test_memory_prefetch.py
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pipecat.frames.frames import LLMContextFrame, LLMUpdateSettingsFrame  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.tests.utils import run_test  # noqa: E402

from hearth.pipeline import memory_prefetch as mp  # noqa: E402


class _FakeSeam:
    def __init__(self, *, enabled=True, voice=True, min_chars=12):
        self.per_turn_enabled = enabled
        self.per_turn_voice = voice
        self.per_turn_min_chars = min_chars
        self.calls = []

    def turn_block(self, cue):
        self.calls.append(cue)
        if cue and "knight" in cue.lower():
            return "[EXTRA knight rider]"
        return ""  # nothing surfaced


class _FakeSwitcher:
    def __init__(self, seam, base="BASE"):
        self.current_seam = seam
        self.current_base_instruction = base


class _FakeContext:
    def __init__(self, messages):
        self.messages = messages


def _user(text):
    return [{"role": "user", "content": text}]


class TestCueExtraction(unittest.TestCase):
    def test_last_user_string(self):
        msgs = [{"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "the latest question"}]
        self.assertEqual(mp._last_user_text(msgs), "the latest question")

    def test_multimodal_parts(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "look at"}, {"type": "image_url", "image_url": {}},
            {"type": "text", "text": "this"}]}]
        self.assertEqual(mp._last_user_text(msgs), "look at this")

    def test_empty(self):
        self.assertEqual(mp._last_user_text([]), "")
        self.assertEqual(mp._last_user_text([{"role": "assistant", "content": "x"}]), "")


class TestPrefetchLogic(unittest.TestCase):
    def _proc(self, seam, messages, base="BASE"):
        sw = _FakeSwitcher(seam, base)
        return mp.MemoryPrefetch(switcher=sw, context=_FakeContext(messages)), sw

    def test_launch_sets_pending(self):
        async def go():
            seam = _FakeSeam()
            proc, _ = self._proc(seam, _user("what was my favorite knight show as a kid"))
            proc._launch(seam)
            await proc._task
            self.assertEqual(proc._pending,
                             ("what was my favorite knight show as a kid",
                              "[EXTRA knight rider]"))
            self.assertEqual(proc._last_block, "[EXTRA knight rider]")
        asyncio.run(go())

    def test_below_floor_launches_nothing(self):
        seam = _FakeSeam(min_chars=12)
        proc, _ = self._proc(seam, _user("hi"))
        proc._launch(seam)
        self.assertIsNone(proc._task)
        self.assertIsNone(proc._pending)   # nothing rides; nothing to clear
        self.assertIsNone(proc._last_launched)
        self.assertEqual(seam.calls, [])

    def test_repeat_cue_rides_the_last_block_without_recall(self):
        seam = _FakeSeam()
        proc, _ = self._proc(seam, _user("the same long question again"))
        proc._last_launched = "the same long question again"
        proc._last_block = "[EXTRA knight rider]"
        proc._launch(seam)
        self.assertEqual(seam.calls, [])
        self.assertEqual(proc._pending,
                         ("the same long question again", "[EXTRA knight rider]"))

    def test_rebase_on_switch(self):
        seam1 = _FakeSeam()
        proc, sw = self._proc(seam1, _user("x"), base="BASE-1")
        proc._pending = ("old", "stale")
        proc._last_launched = "old"
        proc._last_block = "stale"
        gen0 = proc._gen
        sw.current_seam = _FakeSeam()
        proc._rebase_if_switched()
        self.assertIsNone(proc._pending)
        self.assertIsNone(proc._last_launched)
        self.assertEqual(proc._last_block, "")
        self.assertGreater(proc._gen, gen0)

    def test_gate_off_launches_nothing(self):
        for seam in (_FakeSeam(voice=False), _FakeSeam(enabled=False)):
            proc, _ = self._proc(seam, _user("a long knight rider question here"))
            proc._launch(seam)
            self.assertEqual(seam.calls, [])
            self.assertIsNone(proc._pending)

    def test_runtime_poke_off_stops_the_cost(self):
        # The panel's per-turn-voice pause (runtime-only poke): gates are read
        # every turn; a mid-sitting OFF drops the pending block and supersedes
        # any in-flight recall. Nothing else to clear — the block was ephemeral.
        seam = _FakeSeam()
        proc, _ = self._proc(seam, _user("a long knight rider question here"))
        proc._pending = ("an earlier question", "[EXTRA knight rider]")
        gen0 = proc._gen
        seam.per_turn_voice = False  # the poke
        proc._launch(seam)
        self.assertIsNone(proc._pending)
        self.assertGreater(proc._gen, gen0)              # in-flight result discarded
        self.assertIsNone(proc._last_launched)

    def test_supersede_drops_stale_result(self):
        async def go():
            seam = _FakeSeam()
            proc, _ = self._proc(seam, _user("a first long knight question"))
            proc._launch(seam)
            task = proc._task
            proc._gen += 1  # a newer turn/switch superseded this launch
            await task
            self.assertIsNone(proc._pending)  # stale result discarded
        asyncio.run(go())


class TestPrefetchFrames(unittest.TestCase):
    def test_prefetched_block_rides_a_request_copy(self):
        # The block from the PRIOR turn's cue must reach the LLM stand-in inside
        # the context frame's messages — folded into the newest user message of
        # a COPY — while the live context stays byte-identical and no settings
        # frame is pushed. Short cue in the fake context ⇒ no new launch.
        async def go():
            seam = _FakeSeam()
            live = LLMContext(messages=[{"role": "system", "content": "S"},
                                        {"role": "user", "content": "ok"}])
            proc = mp.MemoryPrefetch(switcher=_FakeSwitcher(seam), context=live)
            proc._pending = ("a prior turn cue", "[EXTRA knight rider]")
            down, _up = await run_test(proc, frames_to_send=[LLMContextFrame(context=live)])
            self.assertFalse([f for f in down if isinstance(f, LLMUpdateSettingsFrame)])
            ctx_frames = [f for f in down if isinstance(f, LLMContextFrame)]
            self.assertEqual(len(ctx_frames), 1)
            sent = ctx_frames[0].context
            self.assertIsNot(sent, live)
            self.assertEqual(sent.messages[0], {"role": "system", "content": "S"})
            self.assertEqual(sent.messages[-1]["content"], "ok\n\n[EXTRA knight rider]")
            self.assertEqual(live.messages[-1]["content"], "ok")   # never mutated
            self.assertIsNone(proc._pending)                        # consumed
        asyncio.run(go())

    def test_empty_block_passes_the_live_frame_through(self):
        async def go():
            seam = _FakeSeam()
            live = LLMContext(messages=[{"role": "user", "content": "ok"}])
            proc = mp.MemoryPrefetch(switcher=_FakeSwitcher(seam), context=live)
            proc._pending = ("a prior turn cue", "")
            frame = LLMContextFrame(context=live)
            down, _up = await run_test(proc, frames_to_send=[frame])
            ctx_frames = [f for f in down if isinstance(f, LLMContextFrame)]
            self.assertIs(ctx_frames[0].context, live)
        asyncio.run(go())

    def test_gate_off_pushes_no_settings_frame_and_no_copy(self):
        async def go():
            seam = _FakeSeam(voice=False)
            live = LLMContext(messages=_user("a long knight rider question with plenty of chars"))
            proc = mp.MemoryPrefetch(switcher=_FakeSwitcher(seam), context=live)
            down, _up = await run_test(proc, frames_to_send=[LLMContextFrame(context=live)])
            self.assertFalse([f for f in down if isinstance(f, LLMUpdateSettingsFrame)])
            self.assertIs([f for f in down if isinstance(f, LLMContextFrame)][0].context, live)
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main(verbosity=2)
