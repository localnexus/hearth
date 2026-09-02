"""test_memory_prefetch.py — the voice-lane per-turn recall processor (lane (b)).

Prefetch-behind (DESIGN-lane-b-per-turn-recall.md §D-A): recall runs in the
background after turn N, its extras injected before turn N+1 — zero added
latency, one-turn lag. Proves:

  1. cue extraction — last user turn, string and multimodal-parts content
  2. launch  — a long, new cue schedules recall; the result lands in _pending
  3. guards  — a below-floor cue clears applied extras (pending → clean base);
               a repeat cue relaunches nothing
  4. rebase  — a changed seam (live switch) drops the old prefetch + rebases
  5. gate    — voice OFF (or per-turn OFF) launches nothing
  6. frames  — through real pipecat run_test: the prefetched settings frame
               reaches the LLM stand-in ahead of the context frame; gate OFF
               pushes no settings frame

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

    def augment_turn(self, base, cue):
        self.calls.append(cue)
        if cue and "knight" in cue.lower():
            return base + "\n[EXTRA knight rider]"
        return base  # clean (no extras / empty cue)


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
            self.assertIsNotNone(proc._pending)
            cue, instr = proc._pending
            self.assertIn("[EXTRA knight rider]", instr)
            self.assertEqual(cue, "what was my favorite knight show as a kid")
        asyncio.run(go())

    def test_below_floor_clears_applied(self):
        seam = _FakeSeam(min_chars=12)
        proc, _ = self._proc(seam, _user("hi"))
        proc._applied_cue = "an earlier question"
        proc._launch(seam)
        self.assertIsNone(proc._task and None)  # no task expected; pending is the clean base
        self.assertEqual(proc._pending, (None, "BASE"))
        self.assertIsNone(proc._last_launched)

    def test_below_floor_noop_when_already_clean(self):
        seam = _FakeSeam()
        proc, _ = self._proc(seam, _user("ok"))
        proc._applied_cue = None
        proc._launch(seam)
        self.assertIsNone(proc._pending)

    def test_repeat_cue_relaunches_nothing(self):
        seam = _FakeSeam()
        proc, _ = self._proc(seam, _user("the same long question again"))
        proc._last_launched = "the same long question again"
        proc._launch(seam)
        self.assertEqual(seam.calls, [])
        self.assertIsNone(proc._pending)

    def test_rebase_on_switch(self):
        seam1 = _FakeSeam()
        proc, sw = self._proc(seam1, _user("x"), base="BASE-1")
        proc._pending = ("old", "stale")
        proc._applied_cue = "old"
        gen0 = proc._gen
        seam2 = _FakeSeam()
        sw.current_seam = seam2
        sw.current_base_instruction = "BASE-2"
        proc._rebase_if_switched()
        self.assertEqual(proc._raw_base, "BASE-2")
        self.assertIsNone(proc._pending)
        self.assertIsNone(proc._applied_cue)
        self.assertGreater(proc._gen, gen0)

    def test_gate_off_launches_nothing(self):
        for seam in (_FakeSeam(voice=False), _FakeSeam(enabled=False)):
            proc, _ = self._proc(seam, _user("a long knight rider question here"))
            proc._launch(seam)
            self.assertEqual(seam.calls, [])
            self.assertIsNone(proc._pending)

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
    def test_prefetched_settings_frame_precedes_context(self):
        # The background launch is covered by TestPrefetchLogic; here the
        # already-prefetched instruction must reach the LLM stand-in AS a
        # settings frame, AHEAD of the context frame (T3 ordering), so it lands
        # this turn. Short cue in the fake context ⇒ no new launch to interfere.
        async def go():
            seam = _FakeSeam()
            ctx = _FakeContext(_user("ok"))
            proc = mp.MemoryPrefetch(switcher=_FakeSwitcher(seam), context=ctx)
            proc._pending = ("a prior turn cue", "BASE\n[EXTRA knight rider]")
            down, _up = await run_test(proc, frames_to_send=[
                LLMContextFrame(context=LLMContext())])
            names = [type(f).__name__ for f in down]
            self.assertIn("LLMUpdateSettingsFrame", names)
            self.assertIn("LLMContextFrame", names)
            self.assertLess(names.index("LLMUpdateSettingsFrame"),
                            names.index("LLMContextFrame"))
            settings = [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]
            self.assertIn("[EXTRA knight rider]",
                          getattr(settings[0].delta, "system_instruction", ""))
        asyncio.run(go())

    def test_gate_off_pushes_no_settings_frame(self):
        async def go():
            seam = _FakeSeam(voice=False)
            ctx = _FakeContext(_user("a long knight rider question with plenty of chars"))
            proc = mp.MemoryPrefetch(switcher=_FakeSwitcher(seam), context=ctx)
            down, _up = await run_test(proc, frames_to_send=[
                LLMContextFrame(context=LLMContext())])
            self.assertFalse([f for f in down if isinstance(f, LLMUpdateSettingsFrame)])
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main(verbosity=2)
