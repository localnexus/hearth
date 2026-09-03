"""Memory seam — digest, canonical records, the floor, and containment.

The substrate half of the seam: a deterministic extractive digest, records that
round-trip atomically at 0600/0700, floor recall (newest-first, limit honored,
provenance on every item), and the containment ladder that degrades a failing
backend to the floor without ever raising.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import stat
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import MemorySeam  # noqa: E402
from hearth.memory.backend import MemoryItem, SessionRecord, digest_record  # noqa: E402
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


class TestDigest(unittest.TestCase):
    def test_deterministic_and_extractive(self):
        r = _record("s1", "2026-08-29T11:00:00", name="tea talk")
        d1, d2 = digest_record(r), digest_record(r)
        self.assertEqual(d1, d2)
        self.assertIn("tea talk", d1)
        self.assertIn("user line 0 of s1", d1)      # opening line
        self.assertIn("assistant line 1 of s1", d1)  # closing line
        self.assertIn("2 exchanges", d1)

    def test_ignores_system_and_junk(self):
        r = SessionRecord(
            companion="c", session_id="s", started="", ended="",
            messages=[{"role": "system", "content": "SECRET ENVELOPE"},
                      {"role": "user", "content": "hello"},
                      {"role": "assistant", "content": "hi"}],
        )
        self.assertNotIn("SECRET ENVELOPE", digest_record(r))

    def test_skips_compaction_meta_as_representative_line(self):
        # Run-observed 2026-08-30: a compacted session's first user message is the
        # tool's meta-marker; the digest surfaced it (backup path included) as the
        # companion's remembered opening line. It must be skipped, not remembered.
        r = SessionRecord(
            companion="c", session_id="s", started="", ended="2026-08-30T03:00:00",
            messages=[
                {"role": "user", "content": "[session compact applied — full transcript "
                                            "backed up under pre-compaction-bak/2026.08.30/s.json; "
                                            "continue as established partners]"},
                {"role": "assistant", "content": "compact body summary"},
                {"role": "user", "content": "real opening line"},
                {"role": "assistant", "content": "real reply"},
            ],
        )
        d = digest_record(r)
        self.assertNotIn("session compact", d)
        self.assertNotIn("pre-compaction-bak", d)
        self.assertIn("real opening line", d)
        self.assertIn("1 exchange", d)  # meta line no longer counts as a user turn


class TestRecords(unittest.TestCase):
    def test_roundtrip_perms_order_and_malformed_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "records"
            p1 = records_mod.write_record(_record("s1", "2026-08-28T20:00:00"), d)
            p2 = records_mod.write_record(_record("s2", "2026-08-29T20:00:00"), d)
            self.assertEqual(stat.S_IMODE(p1.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
            (d / "junk.json").write_text("{not json", encoding="utf-8")
            (d / "wrongkind.json").write_text(json.dumps({"kind": "other"}), encoding="utf-8")
            newest = list(records_mod.iter_records("testchar", d, newest_first=True))
            self.assertEqual([r.session_id for r in newest], ["s2", "s1"])
            self.assertEqual(records_mod.load_record(p2).messages,
                             _record("s2", "2026-08-29T20:00:00").messages)

    def test_idempotent_per_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            records_mod.write_record(_record("s1", "2026-08-28T20:00:00"), d)
            records_mod.write_record(_record("s1", "2026-08-28T21:00:00"), d)
            got = list(records_mod.iter_records("testchar", d))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].ended, "2026-08-28T21:00:00")


class TestFloor(unittest.TestCase):
    def test_recall_provenance_limit_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for sid, ended in (("a", "2026-08-27T09:00:00"),
                               ("b", "2026-08-28T09:00:00"),
                               ("c", "2026-08-29T09:00:00")):
                records_mod.write_record(_record(sid, ended), d)
            items = FloorBackend(d).recall("testchar", "ignored", 2)
            self.assertEqual(len(items), 2)
            self.assertEqual([i.source_session for i in items], ["c", "b"])
            for i in items:
                self.assertIsInstance(i, MemoryItem)
                self.assertTrue(i.when.startswith("2026-08-2"))
                self.assertTrue(i.text)


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


class TestSeam(unittest.TestCase):
    def _seam(self, backend, floor_dir: Path) -> MemorySeam:
        seam = MemorySeam("testchar", "default", backend, {"recall_limit": 3})
        seam._floor = FloorBackend(floor_dir)
        return seam

    def test_augment_byte_identical_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            seam = self._seam(FloorBackend(Path(tmp)), Path(tmp))
            base = "SYSTEM PROMPT"
            self.assertEqual(seam.augment(base), base)

    def test_augment_injects_dated_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            seam = self._seam(FloorBackend(d), d)
            out = seam.augment("SYSTEM PROMPT")
            self.assertTrue(out.startswith("SYSTEM PROMPT"))
            self.assertIn("## MEMORY — from earlier conversations", out)
            self.assertIn("(2026-08-29)", out)

    def test_recall_degrades_to_floor_then_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            seam = self._seam(_BoomBackend(), d)
            items = seam.recall()   # backend raises → floor answers
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].source_session, "s1")
            seam_empty = self._seam(_BoomBackend(), Path(tmp) / "nowhere")
            self.assertEqual(seam_empty.recall(), [])

    def test_session_end_keeps_record_despite_backend_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            seam = self._seam(_BoomBackend(), d)
            msgs = [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"}]
            with mock.patch.object(records_mod, "records_dir", return_value=d):
                status = seam.on_session_end(msgs, store=None)
            self.assertIn("record kept", status)
            self.assertIn("index skipped", status)
            self.assertEqual(len(list(records_mod.iter_records("testchar", d))), 1)
            seam.close()  # contained close must not raise

    def test_session_end_skips_empty_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            seam = self._seam(FloorBackend(d), d)
            with mock.patch.object(records_mod, "records_dir", return_value=d):
                self.assertEqual(seam.on_session_end([], store=None), "")
            self.assertEqual(list(records_mod.iter_records("testchar", d)), [])

if __name__ == "__main__":
    unittest.main()
