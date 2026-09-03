"""Supervisor — the bot child's lifecycle.

start / stop / adopt / reconcile against real short-lived processes: a graceful
child that honors SIGINT, a stubborn one that ignores both signals, and the
re-anchor to process truth that the status poll depends on.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import unittest
from hearth.supervisor.child import BotChild


GRACEFUL = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
    "while True: time.sleep(0.1)\n"
)


STUBBORN = (
    "import signal, time\n"
    "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "while True: time.sleep(0.1)\n"
)


_PY = sys.executable


def _fake(src: str, **kw) -> BotChild:
    kw.setdefault("pattern", _NOMATCH)
    kw.setdefault("stop_grace_s", 5.0)
    kw.setdefault("term_grace_s", 1.0)
    return BotChild(argv=[_PY, "-c", src], **kw)


_NOMATCH = "zz-hearth-test-nomatch-zz"


class ChildLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_graceful_start_stop(self):
        c = _fake(GRACEFUL)
        res = await c.start()
        self.assertTrue(res["ok"], res)
        await asyncio.sleep(0.4)  # let the child install its SIGINT handler
        self.assertEqual(c.state, "running")
        self.assertTrue(c.managed)
        self.assertIsInstance(c.pid, int)
        self.assertIsNotNone(c.status()["uptime_s"])
        res = await c.stop()
        self.assertTrue(res["ok"], res)
        self.assertFalse(res["escalated"], "SIGINT alone should have sufficed")
        self.assertEqual(c.state, "down")
        self.assertIsNone(c.pid)
        self.assertEqual(c.last_exit["code"], 0)
        c.close()

    async def test_stubborn_child_is_escalated(self):
        c = _fake(STUBBORN, stop_grace_s=0.6, term_grace_s=0.6)
        res = await c.start()
        self.assertTrue(res["ok"], res)
        await asyncio.sleep(0.4)  # let the child install its ignore-handlers
        res = await c.stop()
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["escalated"])
        self.assertEqual(c.state, "down")
        c.close()

    async def test_double_start_refused(self):
        c = _fake(GRACEFUL)
        self.assertTrue((await c.start())["ok"])
        res = await c.start()
        self.assertFalse(res["ok"])
        self.assertIn("running", res["error"])
        await c.stop()
        c.close()

    async def test_bad_mode_refused(self):
        c = _fake(GRACEFUL)
        res = await c.start(mode="bogus")
        self.assertFalse(res["ok"])
        self.assertEqual(c.state, "down")
        c.close()

    async def test_reaper_records_self_exit(self):
        c = _fake("import sys; sys.exit(7)\n")
        self.assertTrue((await c.start())["ok"])
        for _ in range(40):  # the reaper needs loop time
            if c.state == "down":
                break
            await asyncio.sleep(0.1)
        self.assertEqual(c.state, "down")
        self.assertEqual(c.last_exit["code"], 7)
        c.close()

    async def test_adopt_and_stop_external(self):
        mark = f"hearth-adopt-test-{os.getpid()}"
        src = f"mark = '{mark}'\nimport time\nwhile True: time.sleep(0.1)\n"
        ext = subprocess.Popen([_PY, "-c", src])
        self.addCleanup(ext.wait)
        try:
            await asyncio.sleep(0.3)  # let pgrep see it
            c = BotChild(pattern=mark, stop_grace_s=5.0, term_grace_s=1.0)
            self.assertTrue(await c.adopt())
            self.assertEqual(c.pid, ext.pid)
            self.assertFalse(c.managed)
            # a start against a live external adopts and refuses, never duplicates
            c2 = _fake(GRACEFUL, pattern=mark)
            res = await c2.start()
            self.assertFalse(res["ok"])
            self.assertTrue(res.get("adopted"))
            res = await c.stop()
            self.assertTrue(res["ok"], res)
            self.assertIsNone(c.last_exit["code"])  # adopted: code unknowable
            c.close()
            c2.close()
        finally:
            if ext.poll() is None:
                ext.kill()

    async def test_reconcile_tracks_desk_lifecycle(self):
        mark = f"hearth-reconcile-test-{os.getpid()}"
        src = f"mark = '{mark}'\nimport time\nwhile True: time.sleep(0.1)\n"
        c = BotChild(pattern=mark, stop_grace_s=5.0, term_grace_s=1.0)
        # nothing running: reconcile reports not-live, state stays down
        self.assertFalse(await c.reconcile())
        self.assertEqual(c.state, "down")
        # a bot appears at the desk AFTER the daemon came up: the poll adopts it
        ext = subprocess.Popen([_PY, "-c", src])
        self.addCleanup(ext.wait)
        try:
            await asyncio.sleep(0.3)  # let pgrep see it
            self.assertTrue(await c.reconcile())
            self.assertEqual(c.pid, ext.pid)
            self.assertFalse(c.managed)
            # it dies at the desk: the poll notices the dead pid and marks down
            ext.kill()
            ext.wait()
            self.assertFalse(await c.reconcile())
            self.assertEqual(c.state, "down")
            self.assertIsNone(c.pid)
            self.assertIsNone(c.last_exit["code"])  # adopted: code unknowable
            c.close()
        finally:
            if ext.poll() is None:
                ext.kill()

    async def test_reconcile_leaves_managed_alone(self):
        c = _fake(GRACEFUL)
        self.assertTrue((await c.start())["ok"])
        await asyncio.sleep(0.4)  # let the child install its SIGINT handler
        self.assertTrue(await c.reconcile())
        self.assertTrue(c.managed)
        self.assertEqual(c.state, "running")
        await c.stop()
        c.close()

    async def test_memory_mode_validated_and_forwarded(self):
        c = _fake(GRACEFUL)
        res = await c.start(memory="bogus")
        self.assertFalse(res["ok"])
        self.assertIn("memory mode", res["error"])
        self.assertEqual(c.state, "down", "refused before any spawn")
        res = await c.start(memory="recall-only")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["memory"], "recall-only")
        self.assertTrue((await c.stop())["ok"])
        c.close()

    async def test_stop_when_nothing_runs(self):
        c = _fake(GRACEFUL)
        res = await c.stop()
        self.assertTrue(res["ok"])
        self.assertIn("nothing to stop", res["note"])
        c.close()

if __name__ == "__main__":
    unittest.main()
