"""Hindsight backend — the curation verbs and their CLI.

Fact listing, deletion, and the keyed cascade, over a stubbed docs API; then the
same verbs driven through the memory CLI's curation lane.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth import memory as seam_mod  # noqa: E402
from hearth.memory import maybe_attach  # noqa: E402
from hearth.memory.backend import SessionRecord  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory import __main__ as memory_cli  # noqa: E402
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


class _CurationDocsApi:
    """The SDK's low-level DocumentsApi stand-in — its delete is async-only,
    which is exactly what the adapter's sync bridge exists to cross."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    async def delete_document(self, bank_id: str, document_id: str) -> None:
        self.calls.append((bank_id, document_id))
        if self._fail is not None:
            raise self._fail


class _CurationClient:
    """Hindsight SDK stand-in for the keyed-store/forget/clear surface."""

    def __init__(self, docs: _CurationDocsApi | None = None) -> None:
        self.retains: list[dict] = []
        self.deleted_banks: list[str] = []
        self.documents = docs if docs is not None else _CurationDocsApi()

    def retain(self, **kwargs) -> None:  # noqa: ANN003
        self.retains.append(kwargs)

    def delete_bank(self, bank_id: str) -> None:
        self.deleted_banks.append(bank_id)

    def close(self) -> None:
        pass


class _NotFound(Exception):
    status = 404  # the generated ApiException carries HTTP status here


class TestHindsightCuration(unittest.TestCase):
    """Record-level curation (D1/D2/D4): the store is session-keyed and
    replacing, forget cascade-deletes one session's document, clear drops the
    bank. Client-level fakes only — no hindsight install, no sidecar spawn."""

    def _backend(self, client):
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "python": sys.executable,
                              "llm_model": "m"})
        b._client = client  # bound directly: _ensure sees a live client, no spawn
        return b

    def test_store_is_keyed_replacing_and_dated(self):
        from datetime import datetime
        client = _CurationClient()
        rec = _record("session-a", "2026-09-01T10:30:00+02:00")
        self._backend(client).store("testchar", rec)
        (kw,) = client.retains
        self.assertEqual(kw["bank_id"], "testchar")
        self.assertEqual(kw["document_id"], "session-a")
        self.assertEqual(kw["update_mode"], "replace")
        self.assertEqual(kw["timestamp"],
                         datetime.fromisoformat("2026-09-01T10:30:00+02:00"))

    def test_store_with_unparsable_ended_still_stores_undated(self):
        client = _CurationClient()
        self._backend(client).store("testchar", _record("session-b", "not-a-date"))
        (kw,) = client.retains
        self.assertEqual(kw["document_id"], "session-b")
        self.assertIsNone(kw["timestamp"])

    def test_forget_cascade_deletes_the_session_document(self):
        docs = _CurationDocsApi()
        b = self._backend(_CurationClient(docs))
        self.assertTrue(b.forget("testchar", "session-a"))
        self.assertEqual(docs.calls, [("testchar", "session-a")])

    def test_forget_unkeyed_session_reports_false(self):
        # Facts stored before keyed retain have no document — the server 404s
        # and the verb must say "not excised", never pretend.
        b = self._backend(_CurationClient(_CurationDocsApi(fail=_NotFound())))
        self.assertFalse(b.forget("testchar", "pre-keying-session"))

    def test_forget_transport_errors_still_raise(self):
        b = self._backend(_CurationClient(_CurationDocsApi(fail=RuntimeError("boom"))))
        with self.assertRaises(RuntimeError):
            b.forget("testchar", "session-a")

    def test_clear_deletes_the_bank(self):
        client = _CurationClient()
        self._backend(client).clear("testchar")
        self.assertEqual(client.deleted_banks, ["testchar"])

    def test_floor_curation_is_trivially_complete(self):
        f = FloorBackend()
        self.assertTrue(f.forget("testchar", "anything"))
        f.clear("testchar")  # no-op, must not raise


class _CLIBackend:
    """Seam-backend stand-in for the curation CLI: records every call."""

    name = "fakebackend"

    def __init__(self, forget_result: bool = True,
                 forget_raises: Exception | None = None,
                 clear_raises: Exception | None = None) -> None:
        self.forgets: list[tuple[str, str]] = []
        self.clears: list[str] = []
        self.stored: list[str] = []
        self._forget_result = forget_result
        self._forget_raises = forget_raises
        self._clear_raises = clear_raises

    def forget(self, companion: str, session_id: str) -> bool:
        self.forgets.append((companion, session_id))
        if self._forget_raises is not None:
            raise self._forget_raises
        return self._forget_result

    def clear(self, companion: str) -> None:
        self.clears.append(companion)
        if self._clear_raises is not None:
            raise self._clear_raises

    def store(self, companion: str, record: SessionRecord) -> None:  # noqa: ARG002
        self.stored.append(record.session_id)


class TestHindsightCallLane(unittest.TestCase):
    """_call routes EVERY calling context onto the one persistent worker
    thread. The SDK's cached aiohttp session is loop-bound, so dispatching on
    the caller's context breaks the moment contexts mix (run-observed
    2026-09-02: RuntimeError on every voice-prefetch recall, which rides
    asyncio.to_thread — a thread with no running loop took the old direct
    path while the bot's async calls used the pool)."""

    def test_every_calling_context_lands_on_the_same_lane_thread(self):
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "python": sys.executable,
                              "llm_model": "m"})
        try:
            def lane():
                return threading.current_thread().name

            sync_lane = b._call(lane)                       # plain script (CLI)
            to_thread_lane = asyncio.run(asyncio.to_thread(b._call, lane))

            async def _from_loop():                          # the bot's loop
                return b._call(lane)

            loop_lane = asyncio.run(_from_loop())
            self.assertTrue(sync_lane.startswith("hindsight-io"))
            self.assertEqual(to_thread_lane, sync_lane)
            self.assertEqual(loop_lane, sync_lane)
        finally:
            if b._pool is not None:
                b._pool.shutdown(wait=True)


class TestCurationCLI(unittest.TestCase):
    """forget --session / rebuild --clean (record-level curation, D2/D3/D4):
    the confirm gate previews without deleting, backend-first ordering keeps a
    failed forget re-runnable, and --clean wipes exactly once before replay."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for sid, ended in (("session-1", "2026-08-30T10:00:00"),
                           ("session-2", "2026-08-31T10:00:00")):
            records_mod.write_record(_record(sid, ended), directory=self.dir)
        patcher = mock.patch.object(records_mod, "records_dir", lambda c: self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _with_seam(self, backend):
        import types
        seam = types.SimpleNamespace(backend=backend, close=lambda: None) \
            if backend is not None else None
        return mock.patch.object(seam_mod, "maybe_attach", lambda c: seam)

    def test_forget_without_yes_previews_and_deletes_nothing(self):
        def _boom(c):  # the seam must not even attach pre-confirm
            raise AssertionError("maybe_attach called before --yes")
        with mock.patch.object(seam_mod, "maybe_attach", _boom):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=False)
        self.assertEqual(rc, 1)
        self.assertTrue((self.dir / "session-1.json").is_file())

    def test_forget_yes_excises_and_deletes(self):
        backend = _CLIBackend(forget_result=True)
        with self._with_seam(backend):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 0)
        self.assertEqual(backend.forgets, [("testchar", "session-1")])
        self.assertFalse((self.dir / "session-1.json").exists())
        self.assertTrue((self.dir / "session-2.json").is_file())  # only the named one

    def test_forget_unkeyed_deletes_record_but_signals_rebuild(self):
        backend = _CLIBackend(forget_result=False)
        with self._with_seam(backend):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 1)  # not fully excised — the operator must know
        self.assertFalse((self.dir / "session-1.json").exists())

    def test_forget_backend_failure_keeps_the_record(self):
        backend = _CLIBackend(forget_raises=RuntimeError("bank down"))
        with self._with_seam(backend):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 1)
        self.assertTrue((self.dir / "session-1.json").is_file())  # re-runnable

    def test_forget_memory_disabled_still_deletes_the_record(self):
        with self._with_seam(None):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 0)
        self.assertFalse((self.dir / "session-1.json").exists())

    def test_forget_unknown_session_is_an_error(self):
        with self._with_seam(_CLIBackend()):
            rc = memory_cli._cmd_forget("testchar", "no-such-session", yes=True)
        self.assertEqual(rc, 1)

    def test_rebuild_clean_without_yes_wipes_nothing(self):
        backend = _CLIBackend()
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar", clean=True, yes=False)
        self.assertEqual(rc, 1)
        self.assertEqual(backend.clears, [])
        self.assertEqual(backend.stored, [])

    def test_rebuild_clean_yes_wipes_once_then_replays_oldest_first(self):
        backend = _CLIBackend()
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar", clean=True, yes=True)
        self.assertEqual(rc, 0)
        self.assertEqual(backend.clears, ["testchar"])
        self.assertEqual(backend.stored, ["session-1", "session-2"])

    def test_rebuild_plain_is_unchanged_additive_replay(self):
        backend = _CLIBackend()
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar")
        self.assertEqual(rc, 0)
        self.assertEqual(backend.clears, [])
        self.assertEqual(backend.stored, ["session-1", "session-2"])

    def test_rebuild_clean_failed_wipe_replays_nothing(self):
        backend = _CLIBackend(clear_raises=RuntimeError("bank down"))
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar", clean=True, yes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(backend.stored, [])

if __name__ == "__main__":
    unittest.main()
