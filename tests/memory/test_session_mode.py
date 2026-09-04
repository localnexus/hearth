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
            self.assertEqual(status, "recall-only session — nothing retained")
            self.assertEqual(list(records_mod.iter_records("testchar", d)), [])
            self.assertEqual(spy.stored, [])
            self.assertEqual(spy.consolidated, 0)
            self.assertEqual(llm.call_count, 0)   # intent capture suppressed too

    def test_recall_only_recalls_and_preserves_the_intent_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            intent_mod.write_slot("testchar", "the tea ceremony", "s0", path=slot)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                seam = MemorySeam("testchar", "default", FloorBackend(d),
                                  self.INTENT_CFG, retain=False)
                seam._floor = FloorBackend(d)
                out = seam.augment("SYSTEM PROMPT")
            self.assertIn("## MEMORY — from earlier conversations", out)  # recall is live
            self.assertIn("the tea ceremony", out)     # opens aware of the plan…
            self.assertTrue(slot.is_file())            # …but the slot is peeked, not popped
            # The next FULL boot still gets to consume it.
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                full = MemorySeam("testchar", "default", FloorBackend(d), self.INTENT_CFG)
                full._floor = FloorBackend(d)
                out_full = full.augment("SYSTEM PROMPT")
            self.assertIn("the tea ceremony", out_full)
            self.assertFalse(slot.is_file())

if __name__ == "__main__":
    unittest.main()
