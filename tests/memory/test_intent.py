"""Memory seam — the intent slot (intent-primed boot recall).

Capture writes a 0600 slot; boot steers the query, injects a dated line, and
consumes the slot. "none" / disabled / stale / broken all leave the boot
byte-identical. The extraction LLM is always mocked — no network.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import stat
import tempfile
import time
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import MemorySeam  # noqa: E402
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


class _QueryBackend:
    """Records the recall query the seam composed (and recalls nothing)."""

    name = "queryspy"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def recall(self, companion, query, limit):  # noqa: ANN001
        self.queries.append(query)
        return []

    def store(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def consolidate(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def close(self):
        pass


class TestIntentSlot(unittest.TestCase):
    """Intent-primed boot recall. The LLM transport is mocked in every test —
    these must make zero network calls and must not depend on today's date."""

    MSGS = [{"role": "user", "content": "next time let's talk about the tea ceremony"},
            {"role": "assistant", "content": "I'd like that."}]

    def _cfg(self, enabled: bool = True, **over) -> dict:
        cfg = {"recall_limit": 3}
        if enabled or over:
            cfg["intent"] = {"enabled": enabled, "expiry_days": 14,
                             "llm_provider": "ollama", "llm_model": "testmodel",
                             "llm_url": "", "companions": {}}
            cfg["intent"].update(over)
        return cfg

    def _seam(self, tmp: Path, cfg: dict, backend=None) -> MemorySeam:
        backend = backend if backend is not None else FloorBackend(tmp)
        seam = MemorySeam("testchar", "default", backend, cfg)
        seam._floor = FloorBackend(tmp)
        return seam

    def test_capture_then_boot_steers_injects_and_consumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(
                     intent_mod, "_ollama_chat",
                     return_value='{"closure": true, "topic": "the tea ceremony"}') as llm:
                status = self._seam(d, self._cfg()).on_session_end(self.MSGS, store=None)
                self.assertIn("record kept", status)
                self.assertEqual(llm.call_count, 1)

                # the slot: shape + the records' own permission class
                self.assertEqual(stat.S_IMODE(slot.stat().st_mode), 0o600)
                data = json.loads(slot.read_text(encoding="utf-8"))
                self.assertEqual(data["text"], "the tea ceremony")
                self.assertTrue(data["source_session"])
                stated_day = data["stated_at"][:10]

                # next boot: query steered, dated line injected, slot consumed
                spy = _QueryBackend()
                out = self._seam(d, self._cfg(), spy).augment("SYSTEM PROMPT")
            self.assertIn("the tea ceremony", spy.queries[0])          # steered
            self.assertTrue(spy.queries[0].startswith(                  # …and standing
                "the user's life, preferences, and recent conversations"))
            self.assertIn("## MEMORY — from earlier conversations", out)
            self.assertIn(f"On {stated_day} you agreed to pick up the tea ceremony next time.",
                          out)
            self.assertFalse(slot.exists())  # consume-once

    def test_answer_none_writes_no_slot_and_boot_unchanged(self):
        """A deliberate close that named no topic: the model says so, and the
        slot stays absent — she was told goodbye, not what to pick up."""
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat",
                                   return_value='{"closure": true, "topic": "none"}'):
                self._seam(d, self._cfg()).on_session_end(self.MSGS, store=None)
                self.assertFalse(slot.exists())
                out = self._seam(d, self._cfg()).augment("SYSTEM PROMPT")
                # baseline AFTER the close, so both see the same record set
                baseline = self._seam(d, self._cfg(False)).augment("SYSTEM PROMPT")
            self.assertEqual(out, baseline)

    def test_default_off_never_calls_the_llm(self):
        """No [memory.intent] ⇒ the transport is never reached and both hooks
        behave exactly as they did before the feature existed."""
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat") as llm:
                status = self._seam(d, {"recall_limit": 3}).on_session_end(
                    self.MSGS, store=None)
                out = self._seam(d, {"recall_limit": 3}).augment("SYSTEM PROMPT")
                # the pre-feature seam, same state: byte-identical composition
                baseline = MemorySeam("testchar", "default", FloorBackend(d),
                                      {"recall_limit": 3}).augment("SYSTEM PROMPT")
            llm.assert_not_called()
            self.assertIn("record kept", status)
            self.assertEqual(out, baseline)
            self.assertFalse(slot.exists())

    def test_disabled_ignores_an_existing_slot_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                intent_mod.write_slot("testchar", "the tea ceremony", "s1")
                out = self._seam(d, {"recall_limit": 3}).augment("SYSTEM PROMPT")
            self.assertEqual(out, "SYSTEM PROMPT")
            self.assertTrue(slot.exists())  # re-enabling must find the plan intact

    def test_stale_slot_is_skipped_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            slot.write_text(json.dumps({
                "schema": 1, "kind": "memory-intent", "text": "the tea ceremony",
                "stated_at": "2020-01-01T00:00:00", "source_session": "old",
            }), encoding="utf-8")
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                out = self._seam(d, self._cfg()).augment("SYSTEM PROMPT")
            self.assertEqual(out, "SYSTEM PROMPT")
            self.assertNotIn("pick up", out)
            self.assertFalse(slot.exists())

    def test_transport_failure_leaves_close_normal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat",
                                   side_effect=OSError("connection refused")):
                status = self._seam(d, self._cfg()).on_session_end(self.MSGS, store=None)
            self.assertIn("record kept", status)
            self.assertEqual(len(list(records_mod.iter_records("testchar", d))), 1)
            self.assertFalse(slot.exists())

    def test_malformed_slot_is_removed_and_boot_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            baseline = self._seam(d, self._cfg(False)).augment("SYSTEM PROMPT")
            slot.write_text("{not json", encoding="utf-8")
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                out = self._seam(d, self._cfg()).augment("SYSTEM PROMPT")
            self.assertEqual(out, baseline)
            self.assertFalse(slot.exists())

    def test_parser_is_conservative(self):
        for answer in ("", "  ", "none", "None.", "NONE", "x" * 500):
            self.assertIsNone(intent_mod.parse_answer(answer), answer[:20])
        self.assertEqual(intent_mod.parse_answer('  "the tea ceremony"\nbecause…'),
                         "the tea ceremony")
        self.assertEqual(intent_mod.parse_answer("<think>hmm</think> the tea ceremony"),
                         "the tea ceremony")

if __name__ == "__main__":
    unittest.main()
