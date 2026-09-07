"""D5 — epoch-keyed retain: a close after a compaction is its own document.

Run:  .venv/bin/python -m unittest tests.memory.test_epoch_retain
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import MemorySeam  # noqa: E402
from hearth.memory import __main__ as memory_cli  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory import __init__ as seam_mod  # noqa: E402,F401
from hearth import memory as seam_pkg  # noqa: E402
from hearth.memory.backend import SessionRecord  # noqa: E402
from hearth.memory.backend_hindsight.payload import _render_transcript  # noqa: E402
from hearth.memory.backend_hindsight.sidecar import Sidecar  # noqa: E402

MARKER = ("[session compact applied — full transcript backed up under "
          "pre-compaction-bak/2026.09.06/session-x.json; continue as established.")
TALK = [{"role": "user", "content": "hello again"}, {"role": "assistant", "content": "hi"}]


class _Spy:
    name = "spy"
    supports_fast_close = True

    def __init__(self) -> None:
        self.stored: list[SessionRecord] = []
        self.closed_fast: bool | None = None

    def recall(self, companion, query, limit):  # noqa: ANN001
        return []

    def store(self, companion, record):  # noqa: ANN001
        self.stored.append(record)

    def consolidate(self, companion):  # noqa: ANN001
        pass

    def close(self, *, fast: bool = False) -> None:
        self.closed_fast = fast


class TestEpochSuffix(unittest.TestCase):
    def test_no_marker_is_epoch_zero(self):
        self.assertEqual(records_mod.epoch_suffix(TALK), "")

    def test_marker_date_names_the_epoch(self):
        msgs = [{"role": "user", "content": MARKER}] + TALK
        self.assertEqual(records_mod.epoch_suffix(msgs), ".c2026.09.06")

    def test_marker_without_a_dated_backup(self):
        msgs = [{"role": "user", "content": "[session compact applied — no backup]"}]
        self.assertEqual(records_mod.epoch_suffix(msgs), ".c0")

    def test_newest_marker_wins(self):
        older = MARKER.replace("2026.09.06", "2026.09.02")
        msgs = [{"role": "user", "content": older}, {"role": "user", "content": MARKER}]
        self.assertEqual(records_mod.epoch_suffix(msgs), ".c2026.09.06")


class TestRecordAndDocumentPerEpoch(unittest.TestCase):
    def _close(self, backend, messages):
        store = types.SimpleNamespace(session_id="session-x", started="2026-07-12T03:36:30", name="")
        seam = MemorySeam("testchar", "persona", backend, {"close_budget_s": 0})
        return seam.on_session_end(messages, store)

    def test_compacted_close_gets_its_own_id_and_file(self):
        backend = _Spy()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(records_mod, "records_dir", return_value=Path(td)):
            self._close(backend, [{"role": "user", "content": MARKER}] + TALK)
            self.assertEqual(backend.stored[0].session_id, "session-x.c2026.09.06")
            self.assertTrue((Path(td) / "session-x.c2026.09.06.json").is_file())
            self.assertFalse((Path(td) / "session-x.json").exists())  # epoch 0 untouched

    def test_uncompacted_close_keeps_the_bare_id(self):
        backend = _Spy()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(records_mod, "records_dir", return_value=Path(td)):
            self._close(backend, TALK)
            self.assertEqual(backend.stored[0].session_id, "session-x")


class TestPayloadSkipsTheMarker(unittest.TestCase):
    def test_marker_never_reaches_the_extractor(self):
        rec = SessionRecord(companion="testchar", session_id="s.c2026.09.06", started="", ended="",
                            messages=[{"role": "user", "content": MARKER}] + TALK)
        text = _render_transcript(rec, 10_000)
        self.assertNotIn("session compact", text)
        self.assertIn("User: hello again", text)


class _ForgetBackend:
    name = "fake"

    def __init__(self) -> None:
        self.forgets: list[str] = []

    def forget(self, companion, session_id):  # noqa: ANN001
        self.forgets.append(session_id)
        return True


class TestForgetSpansEpochs(unittest.TestCase):
    def test_forget_takes_the_bare_record_and_every_epoch(self):
        def _rec(sid, ended):
            return SessionRecord(companion="testchar", session_id=sid, started="", ended=ended,
                                 messages=TALK)
        backend = _ForgetBackend()
        seam = types.SimpleNamespace(backend=backend, close=lambda: None)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(records_mod, "records_dir", lambda c: Path(td)), \
                mock.patch.object(seam_pkg, "maybe_attach", lambda c: seam):
            d = Path(td)
            records_mod.write_record(_rec("s1", "2026-09-01T10:00:00"), d)
            records_mod.write_record(_rec("s1.c2026.09.06", "2026-09-06T18:16:00"), d)
            records_mod.write_record(_rec("s2", "2026-09-02T10:00:00"), d)
            rc = memory_cli._cmd_forget("testchar", "s1", yes=True)
            self.assertEqual(rc, 0)
            self.assertEqual(backend.forgets, ["s1", "s1.c2026.09.06"])
            self.assertEqual(sorted(p.name for p in d.glob("*.json")), ["s2.json"])


class TestFastCloseAfterBudget(unittest.TestCase):
    def test_exhausted_budget_closes_the_backend_fast(self):
        import threading
        gate = threading.Event()

        class _Slow(_Spy):
            def store(self, companion, record):  # noqa: ANN001
                gate.wait(5.0)
        backend = _Slow()
        seam = MemorySeam("testchar", "persona", backend, {"close_budget_s": 0.2})
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(records_mod, "records_dir", return_value=Path(td)):
            status = seam.on_session_end(TALK, store=None)
            self.assertIn("exhausted", status)
            seam.close()
            gate.set()
        self.assertTrue(backend.closed_fast)

    def test_a_close_within_budget_closes_normally(self):
        backend = _Spy()
        seam = MemorySeam("testchar", "persona", backend, {"close_budget_s": 5})
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(records_mod, "records_dir", return_value=Path(td)):
            seam.on_session_end(TALK, store=None)
            seam.close()
        self.assertFalse(backend.closed_fast)


class TestSidecarStopWait(unittest.TestCase):
    def test_stop_honours_wait_and_kills_on_timeout(self):
        sc = Sidecar({})
        waits: list[float] = []
        killed: list[bool] = []

        class _Proc:
            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout):
                waits.append(timeout)
                raise subprocess.TimeoutExpired("sidecar", timeout)

            def kill(self):
                killed.append(True)
        sc._proc = _Proc()
        sc.stop(wait_s=0.1)
        self.assertEqual(waits, [0.1])
        self.assertEqual(killed, [True])
        self.assertIsNone(sc._proc)


if __name__ == "__main__":
    unittest.main()
