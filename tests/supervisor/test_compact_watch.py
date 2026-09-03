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

    def _script(self):
        script = self.root / "ops" / "compact-companion-session.sh"
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
