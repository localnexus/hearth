"""Auto-compaction — the facade's compact watch.

One tick of the watch: what it picks up from the queue, what it defers on the
RAM floor, and what it never retries.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from hearth import supervisor


# ── the queue readout (what the launch page can see) ─────────────────────────

class QueueStatus(unittest.TestCase):
    """compact_watch.queue_status — names and states, never content.

    The case that matters is `.failed`: a run that dies in its first second
    holds the maintenance lock for less than one poll, so the queue file is
    the only trace the panel can render.
    """

    def setUp(self):
        from unittest import mock
        from hearth.config import config_loader
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.object(config_loader, "DATA_DIR", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.qdir = self.root / "ops" / "compact-queue"

    def _write(self, name, **info):
        self.qdir.mkdir(parents=True, exist_ok=True)
        (self.qdir / name).write_text(json.dumps(info), encoding="utf-8")

    def test_absent_queue_is_empty_not_an_error(self):
        from hearth.supervisor import compact_watch
        self.assertEqual(compact_watch.queue_status(), [])

    def test_each_suffix_reads_as_its_state(self):
        from hearth.supervisor import compact_watch
        self._write("zz-a.s1.request", character="zz-a", session="s1")
        self._write("zz-b.s2.running", character="zz-b", session="s2")
        self._write("zz-c.s3.failed", character="zz-c", session="s3")
        got = {e["session"]: e["state"] for e in compact_watch.queue_status()}
        self.assertEqual(got, {"s1": "parked", "s2": "running", "s3": "failed"})

    def test_failure_reason_is_carried_when_the_compactor_stamped_one(self):
        from hearth.supervisor import compact_watch
        self._write("zz-a.s1.failed", character="zz-a", session="s1",
                    source="manual", step="5. model bracket + summarize",
                    error="lms CLI not on PATH")
        entry, = compact_watch.queue_status()
        self.assertEqual(entry["error"], "lms CLI not on PATH")
        self.assertEqual(entry["step"], "5. model bracket + summarize")
        self.assertEqual(entry["source"], "manual")

    def test_an_older_breadcrumb_without_a_reason_still_reads(self):
        """Pre-2026-09-04 .failed files carry no error — must not vanish."""
        from hearth.supervisor import compact_watch
        self._write("zz-a.s1.failed", character="zz-a", session="s1")
        entry, = compact_watch.queue_status()
        self.assertEqual(entry["state"], "failed")
        self.assertIsNone(entry["error"])

    def test_unrelated_files_are_ignored(self):
        from hearth.supervisor import compact_watch
        self.qdir.mkdir(parents=True, exist_ok=True)
        (self.qdir / "notes.txt").write_text("x", encoding="utf-8")
        (self.qdir / "zz-a.s1.request").write_text("{}", encoding="utf-8")
        self.assertEqual(len(compact_watch.queue_status()), 1)

    def test_an_unreadable_file_degrades_instead_of_raising(self):
        from hearth.supervisor import compact_watch
        self.qdir.mkdir(parents=True, exist_ok=True)
        (self.qdir / "zz-a.s1.failed").write_text("{not json", encoding="utf-8")
        entry, = compact_watch.queue_status()
        self.assertEqual((entry["state"], entry["character"]), ("failed", "?"))



# ── auto-compaction: the compact watch + the start-door guard ────────────────

class CompactWatchTick(unittest.IsolatedAsyncioTestCase):
    """compact_watch.tick against a scratch DATA root and a dict app."""

    def setUp(self):
        from unittest import mock
        from hearth.config import config_loader
        from hearth.session import maintenance_lock
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.object(config_loader, "DATA_DIR", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        maintenance_lock._HELD.clear()
        self.addCleanup(lambda: [maintenance_lock.drop(c)
                                 for c in list(maintenance_lock._HELD)])
        self.qdir = self.root / "ops" / "compact-queue"

    def _app(self, bot_state="stopped"):
        return {"bot_child": SimpleNamespace(status=lambda: {"state": bot_state})}

    def _request(self, char="example", session="long-run"):
        self.qdir.mkdir(parents=True, exist_ok=True)
        req = self.qdir / f"{char}.{session}.request"
        req.write_text(json.dumps({"character": char, "session": session,
                                   "est_tokens": 50_000}))
        return req

    def _script(self, parts=("compaction", "compact-companion-session.sh")):
        script = self.root.joinpath("ops", *parts)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\n"
                          f"printf '%s\\n' \"$@\" > '{self.root}/spawn-args.txt'\n")
        script.chmod(0o755)
        return script

    async def test_noop_when_bot_up_or_queue_absent(self):
        from hearth.supervisor import compact_watch
        self.assertIsNone(await compact_watch.tick(self._app()))  # no queue dir
        req = self._request()
        self.assertIsNone(await compact_watch.tick(self._app("running")))
        self.assertTrue(req.exists())  # untouched while a bot is up

    async def test_parked_without_compactor(self):
        from hearth.supervisor import compact_watch
        req = self._request()
        app = self._app()
        self.assertIsNone(await compact_watch.tick(app))
        self.assertTrue(req.exists())
        self.assertTrue(app.get("compact_watch_no_script_logged"))

    async def test_compactor_resolves_new_layout_and_pre_split_fallback(self):
        """The compactor moved into ops/compaction/ on 2026-09-04. An install
        that has not moved yet must still be FOUND, not silently parked."""
        from hearth.supervisor import compact_watch
        want = self.root / "ops" / "compaction" / "compact-companion-session.sh"
        # Nothing installed: the preferred path is named, so the log line
        # tells a person where to put it.
        self.assertEqual(compact_watch.compactor_path(), want)
        legacy = self._script(("compact-companion-session.sh",))
        self.assertEqual(compact_watch.compactor_path(), legacy)
        self._script()  # both present → the new layout wins
        self.assertEqual(compact_watch.compactor_path(), want)

    async def test_fires_and_claims(self):
        from hearth.supervisor import compact_watch
        self._request()
        self._script()
        app = self._app()
        note = await compact_watch.tick(app)
        self.assertEqual(note, "started example/long-run")
        running = self.qdir / "example.long-run.running"
        self.assertTrue(running.exists())
        self.assertIn("claimed_ts", json.loads(running.read_text()))
        args_file = self.root / "spawn-args.txt"
        for _ in range(40):  # detached child — give it a beat
            if args_file.exists():
                break
            await asyncio.sleep(0.05)
        argv = args_file.read_text().split()
        self.assertEqual(argv[0], "long-run")
        self.assertIn("--character", argv)
        self.assertIn("example", argv)
        self.assertIn("--yes", argv)
        self.assertIn("--request-file", argv)
        # a fresh young claim (lock free, just claimed) is left alone
        self.assertIsNone(await compact_watch.tick(app))

    async def test_manual_compaction_lock_blocks_firing(self):
        from hearth.session import maintenance_lock
        from hearth.supervisor import compact_watch
        req = self._request()
        self._script()
        maintenance_lock.hold("other", op="compact", session="desk-run")
        try:
            self.assertIsNone(await compact_watch.tick(self._app()))
            self.assertTrue(req.exists())
        finally:
            maintenance_lock.drop("other")

    async def test_stale_running_reclaimed_as_failed(self):
        from hearth.supervisor import compact_watch
        self.qdir.mkdir(parents=True, exist_ok=True)
        stale = self.qdir / "example.long-run.running"
        stale.write_text(json.dumps({"character": "example", "session": "long-run",
                                     "claimed_ts": 1.0}))  # epoch — long dead
        note = await compact_watch.tick(self._app())
        self.assertEqual(note, "reclaimed example.long-run.failed")
        self.assertTrue((self.qdir / "example.long-run.failed").exists())
        self.assertFalse(stale.exists())

    async def test_unreadable_request_failed(self):
        from hearth.supervisor import compact_watch
        self.qdir.mkdir(parents=True, exist_ok=True)
        (self.qdir / "example.bad.request").write_text("not json")
        self._script()
        note = await compact_watch.tick(self._app())
        self.assertEqual(note, "bad request example.bad.request")
        self.assertTrue((self.qdir / "example.bad.failed").exists())

    async def test_deferred_request_parks_then_retries(self):
        import time as _time
        from hearth.supervisor import compact_watch
        self._script()
        req = self._request()
        # a RAM-deferred run stamped deferred_ts on its way out — fresh = parked
        info = json.loads(req.read_text())
        info["deferred_ts"] = _time.time()
        req.write_text(json.dumps(info))
        self.assertIsNone(await compact_watch.tick(self._app()))
        self.assertTrue(req.exists())
        # stale stamp = eligible again
        info["deferred_ts"] = _time.time() - compact_watch.DEFER_RECHECK_S - 1
        req.write_text(json.dumps(info))
        self.assertEqual(await compact_watch.tick(self._app()),
                         "started example/long-run")

    async def test_submit_manual(self):
        from hearth.supervisor import compact_watch
        self.qdir.mkdir(parents=True, exist_ok=True)
        # bot up → honest refusal
        res = await compact_watch.submit(self._app("running"), "example", "long-run")
        self.assertFalse(res["ok"])
        self.assertIn("stop it first", res["note"])
        # a manual click re-arms a .failed pair and fires when a script exists
        (self.qdir / "example.long-run.failed").write_text("{}")
        self._script()
        res = await compact_watch.submit(self._app(), "example", "long-run")
        self.assertTrue(res["ok"])
        self.assertEqual(res["note"], "started example/long-run")
        self.assertFalse((self.qdir / "example.long-run.failed").exists())
        # active claim (lock held) → refused as already compacting
        from hearth.session import maintenance_lock
        maintenance_lock.hold("example", op="compact", session="long-run")
        try:
            res = await compact_watch.submit(self._app(), "example", "long-run")
            self.assertFalse(res["ok"])
            self.assertIn("already compacting", res["note"])
        finally:
            maintenance_lock.drop("example")

    async def test_submit_queues_without_compactor(self):
        from hearth.supervisor import compact_watch
        res = await compact_watch.submit(self._app(), "example", "long-run")
        self.assertTrue(res["ok"])
        self.assertIn("queued", res["note"])
        self.assertTrue((self.qdir / "example.long-run.request").exists())

if __name__ == "__main__":
    unittest.main()
