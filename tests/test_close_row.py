"""close_row: the durable, machine-readable record of one graceful close.

CLOSE-UX Phase 0's second half. These tests carry a negative fixture for every
band decision — an outcome set the classifier MUST NOT call clean — because the
whole point of the row is to make a bad close legible, and a classifier that
only ever sees good input has proved nothing.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hearth.session import close_path, close_row, session_store  # noqa: E402


class _Store:
    character = "demo"
    session_id = "demo-01"
    name = "reunion"
    held = True
    memory_mode = "full"
    path = Path("/tmp/sessions/demo-reunion-01.json")


class _Seam:
    def __init__(self, outcome=None, boom=None):
        self._outcome, self._boom, self.closed = outcome or {}, boom, False

    def on_session_end(self, messages, store=None, *, outcome=None):
        if self._boom:
            raise self._boom
        if outcome is not None:
            outcome.update(self._outcome)
        return "record kept (demo-01)"

    def close(self):
        self.closed = True


def _run(seam=None, finalize=None, request=None, facts=None):
    """Run a close with the row captured instead of written."""
    rows = []
    close_path.run_close(
        _Store(), seam if seam is not None else _Seam({"result": "kept", "index": "ok",
                                                       "consolidate": "ok", "intent": "ok"}),
        [{"role": "user", "content": "the quick brown fox"}],
        finalize=finalize or (lambda st, m, outcome=None: outcome.update({"result": "held"})
                              or "held → demo-reunion-01.json"),
        request=request or (lambda st, live_tokens=None, outcome=None:
                            outcome.update({"result": "requested"}) or "requested"),
        emit=lambda _l: None,
        record=lambda store, outcomes, lines: rows.append(
            close_row.build(store, outcomes, lines)),
        facts=facts)
    return rows[0] if rows else None


class TestBands(unittest.TestCase):
    def test_a_clean_close_is_safe(self):
        row = _run()
        self.assertEqual(row["band"], close_row.SAFE)
        self.assertEqual(row["reasons"], [])

    def test_finalize_failure_is_unsafe_and_carries_the_errno(self):
        """§4 D5 — the conversation may not be on disk. §5's only provable UNSAFE."""
        def boom(st, m, outcome=None):
            raise OSError(28, "No space left on device")
        row = _run(finalize=boom)
        self.assertEqual(row["band"], close_row.UNSAFE)
        self.assertEqual(row["reasons"], ["session-finalize-failed"])
        self.assertEqual(row["outcomes"]["session"]["errno"], 28)   # spec §4: disk full rolls up here

    def test_unsafe_never_degrades_to_degraded_when_other_steps_also_fail(self):
        """The negative fixture that matters most: a close that failed to save
        the conversation must not be diluted by the noise around it."""
        def boom(st, m, outcome=None):
            raise OSError(28, "No space left on device")
        row = _run(finalize=boom, seam=_Seam({"result": "kept", "index": "deferred"}))
        self.assertEqual(row["band"], close_row.UNSAFE)

    def test_deferred_index_is_degraded_not_safe(self):
        row = _run(seam=_Seam({"result": "kept", "index": "deferred"}))   # §4 D1
        self.assertEqual(row["band"], close_row.DEGRADED)
        self.assertIn("index-deferred", row["reasons"])

    def test_skipped_index_and_failed_intent_are_both_named(self):
        row = _run(seam=_Seam({"result": "kept", "index": "skipped",      # §4 D2
                               "intent": "failed"}))                       # §4 D9
        self.assertEqual(row["band"], close_row.DEGRADED)
        self.assertIn("index-skipped", row["reasons"])
        self.assertIn("intent-failed", row["reasons"])

    def test_unqueued_compaction_is_degraded(self):
        """§4 D6 — before this, an auto lane that never armed left no machine trace."""
        row = _run(request=lambda st, live_tokens=None, outcome=None:
                   outcome.update({"result": "failed", "error": "OSError"}) or None)
        self.assertEqual(row["band"], close_row.DEGRADED)
        self.assertIn("compaction-not-queued", row["reasons"])

    def test_caller_facts_reach_the_row(self):
        """§4 D8 capture and D4 drain happen in bot.py before run_close."""
        row = _run(facts={"capture": {"result": "failed", "error": "RuntimeError"},
                          "drain": {"result": "timeout"}})
        self.assertEqual(row["band"], close_row.DEGRADED)
        self.assertIn("capture-failed", row["reasons"])
        self.assertIn("drain-timeout", row["reasons"])

    def test_unbanded_rows_are_noted_rather_than_silently_placed(self):
        """§5 places neither D7 nor a failed record write. They are banded
        conservatively AND named, so the gap is visible in the artifact."""
        row = _run(seam=_Seam({"result": "record-write-failed", "error": "OSError"}))
        self.assertEqual(row["band"], close_row.DEGRADED)
        self.assertTrue(any("recall of this session" in n for n in row["note"]))


class TestTheRowItself(unittest.TestCase):
    def test_the_row_never_carries_message_text(self):
        """Nothing private: ids and names are metadata; the conversation is not."""
        row = _run()
        self.assertNotIn("quick brown fox", json.dumps(row, ensure_ascii=False))

    def test_an_undetectable_mic_is_stated_not_implied(self):
        """§4 C1/C2 have no signal. A later reader must not read silence as clean."""
        self.assertTrue(any("mic-closed" in u for u in _run()["undetected"]))

    def test_the_row_is_written_even_when_the_memory_tail_raises(self):
        rows, seam = [], _Seam(boom=RuntimeError("index down"))
        with self.assertRaises(RuntimeError):
            close_path.run_close(
                _Store(), seam, [],
                finalize=lambda st, m, outcome=None: outcome.update({"result": "held"}) or "held",
                request=lambda st, live_tokens=None, outcome=None: None,
                emit=lambda _l: None,
                record=lambda s_, o_, l_: rows.append(close_row.build(s_, o_, l_)))
        self.assertTrue(seam.closed)
        self.assertEqual(len(rows), 1, "a close that raised must still leave a row")
        self.assertEqual(rows[0]["outcomes"]["memory"]["result"], "raised")

    def test_a_broken_recorder_never_breaks_the_close(self):
        def explode(*_a, **_k):
            raise RuntimeError("ledger on fire")
        lines = close_path.run_close(
            _Store(), _Seam(), [],
            finalize=lambda st, m, outcome=None: "held",
            request=lambda st, live_tokens=None, outcome=None: None,
            emit=lambda _l: None, record=explode)
        self.assertEqual(lines, ["[session] held", "[memory] record kept (demo-01)"])


class TestLedger(unittest.TestCase):
    def test_append_only_one_json_object_per_close(self):
        with TemporaryDirectory() as d:
            for _ in range(2):
                close_row.write({"schema": 1, "band": "safe"}, directory=Path(d))
            f = next(Path(d).glob("*.jsonl"))
            lines = f.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["band"], "safe")

    def test_an_unwritable_ledger_is_contained(self):
        with TemporaryDirectory() as d:
            blocked = Path(d) / "afile"
            blocked.write_text("not a directory")
            self.assertIsNone(close_row.write({"x": 1}, directory=blocked / "sub"))

    def test_a_broken_store_still_yields_a_row(self):
        class _Hostile:
            def __getattr__(self, name):
                raise RuntimeError("no attributes for you")
        row = close_row.build(_Hostile(), {}, [])
        self.assertEqual(row["band"], close_row.SAFE)
        self.assertIsNone(row["character"])


class TestHoldSplit(unittest.TestCase):
    """§4 D7 vs D5: a failed RENAME must not read as a failed SAVE."""

    class _Store:
        sessions_dir = Path("/tmp/sessions")
        path = Path("/tmp/sessions/demo-01.json")
        held = False
        name = ""

        def __init__(self, rename_raises=False):
            self._rename_raises, self.snapshots = rename_raises, 0

        def rename(self, name):
            if self._rename_raises:
                raise OSError(13, "Permission denied")
            self.path = self.path.with_name(f"{name}.json")

        def snapshot(self, messages):
            self.snapshots += 1

    def setUp(self):
        self._orig = session_store.read_hold_request
        session_store.read_hold_request = lambda _d: (True, "reunion")

    def tearDown(self):
        session_store.read_hold_request = self._orig

    def test_a_good_hold_records_held(self):
        out, st = {}, self._Store()
        session_store.finalize(st, [], outcome=out)
        self.assertEqual(out["result"], "held")
        self.assertTrue(out["hold_ok"])

    def test_a_failed_rename_still_saves_the_conversation(self):
        out, st = {}, self._Store(rename_raises=True)
        status = session_store.finalize(st, [], outcome=out)
        self.assertEqual(st.snapshots, 1, "D7 must not cost the snapshot — that would be D5")
        self.assertFalse(out["hold_ok"])
        self.assertEqual(out["hold_name"], "reunion")
        self.assertEqual(out["result"], "held-unnamed")
        self.assertIn("reunion", status)

    def test_the_failed_rename_bands_as_degraded_and_is_not_called_safe(self):
        band, reasons = close_row.band(
            {"session": {"result": "held-unnamed", "hold_ok": False}})
        self.assertEqual(band, close_row.DEGRADED)
        self.assertIn("hold-failed", reasons)


if __name__ == "__main__":
    unittest.main()
