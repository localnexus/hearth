"""Three-state session memory mode — off / recall-only / full.

recall-only keeps recall live but retains nothing and only peeks the intent
slot; off does neither. The suppression is proven at the seam, not the caller.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import MemorySeam, maybe_attach  # noqa: E402
from hearth.memory.backend import SessionRecord  # noqa: E402
from hearth.memory import intent as intent_mod  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory.floor import FloorBackend  # noqa: E402


def _record(sid: str, ended: str, n_turns: int = 2, name: str = "") -> SessionRecord:
    messages = []
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"user line {i} of {sid}"})
        messages.append({"role": "assistant", "content": f"assistant line {i} of {sid}"})
    return SessionRecord(
        companion="testchar", session_id=sid, started="2026-08-29T10:00:00",
        ended=ended, name=name, messages=messages,
    )


class _SpyBackend:
    """Counts store/consolidate calls (and recalls nothing)."""

    name = "spy"

    def __init__(self) -> None:
        self.stored: list = []
        self.consolidated = 0

    def recall(self, companion, query, limit):  # noqa: ANN001
        return []

    def store(self, companion, record):  # noqa: ANN001
        self.stored.append(record)

    def consolidate(self, companion):  # noqa: ANN001
        self.consolidated += 1

    def close(self):
        pass


class TestSessionMode(unittest.TestCase):
    """Per-session memory mode (off · recall-only · full): recall and retention
    are independent axes. recall-only must recall (and intent-inject) exactly
    like full while leaving ZERO durable memory artifacts — no record, no
    backend index, no intent capture, and a preserved (not consumed) slot."""

    MSGS = [{"role": "user", "content": "a private errand"},
            {"role": "assistant", "content": "understood"}]

    INTENT_CFG = {"recall_limit": 3,
                  "intent": {"enabled": True, "expiry_days": 14,
                             "llm_provider": "ollama", "llm_model": "testmodel",
                             "llm_url": "", "companions": {}}}

    def test_default_retains(self):
        # The serve glue constructs MemorySeam positionally with four args —
        # the default MUST stay retain=True or the facade lane goes silent.
        seam = MemorySeam("testchar", "default", _SpyBackend(), {})
        self.assertTrue(seam.retain)

    def test_mode_validation(self):
        with self.assertRaises(ValueError):
            maybe_attach("testchar", mode="ephemeral")

    def test_maybe_attach_modes(self):
        from hearth.config import config_loader
        cfg = {"backend": "floor", "companions": {}}
        with mock.patch.object(config_loader, "load_memory_config", return_value=cfg):
            self.assertIsNone(maybe_attach("testchar", mode="off"))
            ro = maybe_attach("testchar", mode="recall-only")
            self.assertIsNotNone(ro)
            self.assertFalse(ro.retain)
            self.assertTrue(maybe_attach("testchar", mode="full").retain)

    def test_recall_only_retains_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            spy = _SpyBackend()
            seam = MemorySeam("testchar", "default", spy, self.INTENT_CFG, retain=False)
            seam._floor = FloorBackend(d)
            with mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat") as llm:
                status = seam.on_session_end(self.MSGS, store=None)
            self.assertEqual(status, "conversation not kept — nothing remembered")
            self.assertEqual(list(records_mod.iter_records("testchar", d)), [])
            self.assertEqual(spy.stored, [])
            self.assertEqual(spy.consolidated, 0)
            self.assertEqual(llm.call_count, 0)   # intent capture suppressed too

    def test_recall_only_recalls_and_preserves_the_intent_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            intent_mod.write_slot("testchar", "the tea ceremony", "s0", path=slot)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d):
                seam = MemorySeam("testchar", "default", FloorBackend(d),
                                  self.INTENT_CFG, retain=False)
                seam._floor = FloorBackend(d)
                out = seam.augment("SYSTEM PROMPT")
                self.assertIn("## MEMORY — from earlier conversations", out)  # recall is live
                self.assertIn("the tea ceremony", out)     # opens aware of the plan…
                self.assertTrue(slot.is_file())            # …but the slot is peeked, not popped
                seam.on_session_end(self.MSGS, store=None)
                self.assertTrue(slot.is_file())             # recall-only never consumes, even at its own close
                # The next FULL boot still gets to consume it, at ITS close.
                full = MemorySeam("testchar", "default", FloorBackend(d), self.INTENT_CFG)
                full._floor = FloorBackend(d)
                out_full = full.augment("SYSTEM PROMPT")
                self.assertIn("the tea ceremony", out_full)
                self.assertTrue(slot.is_file())             # injected, not yet consumed
                full.on_session_end(self.MSGS, store=None)
            self.assertFalse(slot.is_file())                # consumed at close

    def test_recall_off_leaves_the_prompt_and_the_slot_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            intent_mod.write_slot("testchar", "the tea ceremony", "s0", path=slot)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                # a backend with real, recallable content: proves the OFF
                # switch skips recall entirely rather than merely finding
                # nothing to say.
                seam = MemorySeam("testchar", "default", FloorBackend(d),
                                  self.INTENT_CFG, recall=False)
                seam._floor = FloorBackend(d)
                out = seam.augment("SYSTEM PROMPT")
            self.assertEqual(out, "SYSTEM PROMPT")
            self.assertTrue(slot.is_file())

    def test_the_read_lane_injects_and_the_write_lane_consumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            intent_mod.write_slot("testchar", "the tea ceremony", "s0", path=slot)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d):
                seam = MemorySeam("testchar", "default", FloorBackend(d), self.INTENT_CFG)
                seam._floor = FloorBackend(d)
                seam.augment("SYSTEM PROMPT")
                self.assertTrue(slot.is_file())          # injected, not yet consumed
                seam.on_session_end(self.MSGS, store=None)
            self.assertFalse(slot.is_file())              # consumed once the record lands

    def test_recall_only_preserves_the_slot_through_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            intent_mod.write_slot("testchar", "the tea ceremony", "s0", path=slot)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d):
                seam = MemorySeam("testchar", "default", FloorBackend(d), self.INTENT_CFG,
                                  retain=False)
                seam._floor = FloorBackend(d)
                seam.augment("SYSTEM PROMPT")
                self.assertTrue(slot.is_file())
                seam.on_session_end(self.MSGS, store=None)
            self.assertTrue(slot.is_file())               # never consumed: recall-only

    def test_the_store_switch_wins_at_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(records_mod, "records_dir", return_value=d):
                spy = _SpyBackend()
                seam = MemorySeam("testchar", "default", spy, {}, retain=False)
                store = SimpleNamespace(session_id="s-store-wins", started="", name="",
                                        retain=True)
                status = seam.on_session_end(self.MSGS, store)
                self.assertTrue(status.startswith("record kept"), status)
                self.assertEqual(len(spy.stored), 1)

                spy2 = _SpyBackend()
                seam2 = MemorySeam("testchar", "default", spy2, {}, retain=True)
                store2 = SimpleNamespace(session_id="s-store-loses", started="", name="",
                                         retain=False)
                status2 = seam2.on_session_end(self.MSGS, store2)
                self.assertEqual(status2, "conversation not kept — nothing remembered")
                self.assertEqual(spy2.stored, [])

    def test_the_watermark_slices_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(records_mod, "records_dir", return_value=d):
                spy = _SpyBackend()
                seam = MemorySeam("testchar", "default", spy, {})
                four = [
                    {"role": "user", "content": "m0"},
                    {"role": "assistant", "content": "m1"},
                    {"role": "user", "content": "m2"},
                    {"role": "assistant", "content": "m3"},
                ]
                store = SimpleNamespace(session_id="s-water", started="", name="",
                                        banked_from=2)
                seam.on_session_end(four, store)
        self.assertEqual(len(spy.stored), 1)
        self.assertEqual([m["content"] for m in spy.stored[0].messages], ["m2", "m3"])

    def test_maybe_attach_switches(self):
        from hearth.config import config_loader
        cfg = {"backend": "floor", "companions": {}}
        with mock.patch.object(config_loader, "load_memory_config", return_value=cfg):
            self.assertIsNone(maybe_attach("testchar", recall=False, retain=False))
            ro = maybe_attach("testchar", mode="recall-only")
            self.assertTrue(ro.recall)
            self.assertFalse(ro.retain)
            off_but_kept = maybe_attach("testchar", mode="off", retain=True)
            self.assertIsNotNone(off_but_kept)
            self.assertFalse(off_but_kept.recall)
            self.assertTrue(off_but_kept.retain)

if __name__ == "__main__":
    unittest.main()


class TestCloseBudget(unittest.TestCase):
    """S2: the index tail is bounded; the record is on disk before it starts."""

    MSGS = [{"role": "user", "content": "hello there"}, {"role": "assistant", "content": "hi"}]

    def _seam(self, backend, budget):
        return MemorySeam("testchar", "persona", backend, {"close_budget_s": budget})

    def test_budget_exhausted_keeps_record_and_defers_index(self):
        import threading
        gate = threading.Event()

        class _Slow(_SpyBackend):
            def store(self, companion, record):  # noqa: ANN001
                gate.wait(5.0)
                super().store(companion, record)
        backend = _Slow()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(records_mod, "records_dir", return_value=Path(td)):
            status = self._seam(backend, 0.2).on_session_end(self.MSGS, store=None)
            self.assertIn("record kept", status)
            self.assertIn("close budget exhausted", status)
            self.assertEqual(len(list(Path(td).glob("*.json"))), 1)  # record on disk first
            gate.set()

    def test_zero_budget_runs_inline_like_before(self):
        backend = _SpyBackend()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(records_mod, "records_dir", return_value=Path(td)):
            status = self._seam(backend, 0).on_session_end(self.MSGS, store=None)
        self.assertTrue(status.startswith("record kept ("))
        self.assertNotIn("exhausted", status)
        self.assertEqual(len(backend.stored), 1)
        self.assertEqual(backend.consolidated, 1)

