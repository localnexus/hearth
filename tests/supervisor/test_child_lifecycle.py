"""Supervisor — the bot child's lifecycle.

start / stop / adopt / reconcile against real short-lived processes: a graceful
child that honors SIGINT, a stubborn one that ignores both signals, and the
re-anchor to process truth that the status poll depends on.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
from hearth.session import session_store
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


# Dies gracefully, but not instantly: the close tail in miniature, so stop()'s
# ladder and the reaper task genuinely race on the same death.
SLOW_GRACEFUL = (
    "import signal, sys, time\n"
    "def bye(*a):\n"
    "    time.sleep(0.5)\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGINT, bye)\n"
    "while True: time.sleep(0.1)\n"
)


_NOMATCH = "zz-hearth-test-nomatch-zz"

# Prints two env words the facade hands over per start, then waits to be stopped.
REPORTS_ENV = (
    "import os, signal, sys, time\n"
    "print('WORDS', os.environ.get('ZZ_PER_START', '-'), os.environ.get('ZZ_BOOT', '-'), flush=True)\n"
    "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
    "while True: time.sleep(0.1)\n"
)


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

    async def test_deliberate_stop_is_not_logged_as_a_self_exit(self):
        """The reaper wakes on the same death as stop()'s ladder and usually
        wins the race. Until 2026-09-09 it logged "exited on its own" there, so
        the facade log read as though the Stop button had never been pressed —
        which is exactly how a slow stop got diagnosed as a dead button."""
        from loguru import logger

        lines: list = []
        sink = logger.add(lines.append, level="DEBUG")
        c = _fake(SLOW_GRACEFUL)
        try:
            self.assertTrue((await c.start())["ok"])
            await asyncio.sleep(0.4)  # let the child install its handler
            res = await c.stop()
            self.assertTrue(res["ok"], res)
        finally:
            logger.remove(sink)
        self.assertNotIn("exited on its own", "".join(lines),
                         "a stop() is not a spontaneous exit")
        # stop() still owns the bookkeeping it stepped in front of
        self.assertEqual(c.state, "down")
        self.assertIsNone(c.pid)
        self.assertEqual(c.last_exit["code"], 0)
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

    async def test_muted_is_forwarded_as_a_bare_flag(self):
        async def _start_and_capture_argv(**start_kwargs):
            c = _fake(GRACEFUL)
            captured = []

            class _FakeProbeProc:
                async def communicate(self):
                    return b"", b""  # pgrep: no match

            class _FakeSpawnProc:
                pid = 999999
                returncode = None

                async def wait(self):
                    await asyncio.sleep(3600)

            async def _record(*args, **kwargs):
                if args and args[0] == "pgrep":
                    return _FakeProbeProc()
                captured.append(args)
                return _FakeSpawnProc()

            with mock.patch("hearth.supervisor.child.asyncio.create_subprocess_exec",
                            side_effect=_record):
                res = await c.start(**start_kwargs)
            c.close()
            return res, captured[0]

        res, argv = await _start_and_capture_argv(muted=True)
        self.assertTrue(res["ok"], res)
        self.assertIs(res["muted"], True)
        self.assertEqual(argv.count("--muted"), 1)

        res, argv = await _start_and_capture_argv()
        self.assertTrue(res["ok"], res)
        self.assertNotIn("muted", res)
        self.assertNotIn("--muted", argv)

    async def test_stop_when_nothing_runs(self):
        c = _fake(GRACEFUL)
        res = await c.stop()
        self.assertTrue(res["ok"])
        self.assertIn("nothing to stop", res["note"])
        c.close()

    async def test_switches_forwarded_as_flags(self):
        async def _start_and_capture_argv(**start_kwargs):
            c = _fake(GRACEFUL)
            captured = []

            class _FakeProbeProc:
                async def communicate(self):
                    return b"", b""  # pgrep: no match

            class _FakeSpawnProc:
                pid = 999999
                returncode = None

                async def wait(self):
                    await asyncio.sleep(3600)

            async def _record(*args, **kwargs):
                if args and args[0] == "pgrep":
                    return _FakeProbeProc()
                captured.append(args)
                return _FakeSpawnProc()

            with mock.patch("hearth.supervisor.child.asyncio.create_subprocess_exec",
                            side_effect=_record):
                res = await c.start(**start_kwargs)
            c.close()
            return res, captured[0]

        res, argv = await _start_and_capture_argv(recall=False, retain=True, keep_name="x")
        self.assertTrue(res["ok"], res)
        self.assertIs(res["recall"], False)
        self.assertIs(res["retain"], True)
        self.assertEqual(argv.count("--no-recall"), 1)
        self.assertEqual(argv.count("--keep"), 1)
        self.assertEqual(argv[argv.index("--keep-name") + 1], "x")

        res, argv = await _start_and_capture_argv(retain=True)
        self.assertTrue(res["ok"], res)
        self.assertNotIn("--no-recall", argv)
        self.assertIn("--keep", argv)

        c2 = _fake(GRACEFUL)
        self.assertTrue((await c2.start())["ok"])
        await asyncio.sleep(0.4)
        with mock.patch.object(session_store, "write_retain_request") as wrr:
            res = await c2.stop(retain=False)
        self.assertTrue(res["ok"], res)
        wrr.assert_called_once_with(False, None)
        c2.close()

    async def test_status_reports_the_last_starts_switches(self):
        c = _fake(GRACEFUL)
        self.assertEqual(c.status()["switches"],
                         {"recall": None, "retain": None, "keep_name": None,
                          "route": None})
        res = await c.start(recall=False, retain=True, route="remote:pixel")
        self.assertTrue(res["ok"], res)
        self.assertEqual(c.status()["switches"],
                         {"recall": False, "retain": True, "keep_name": None,
                          "route": "remote:pixel"})
        await c.stop()
        c.close()

    async def test_status_carries_the_start_time_keep_name(self):
        """The name chosen at start rides /admin/state, so the Stop card can
        show it before the first turn puts a working file on the shelf."""
        c = _fake(GRACEFUL)
        res = await c.start(retain=True, keep_name="x")
        self.assertTrue(res["ok"], res)
        self.assertEqual(c.status()["switches"]["keep_name"], "x")
        await c.stop()
        c.close()

        c2 = _fake(GRACEFUL)
        res = await c2.start(retain=True)
        self.assertTrue(res["ok"], res)
        self.assertIsNone(c2.status()["switches"]["keep_name"])
        await c2.stop()
        c2.close()

class PerStartEnv(unittest.IsolatedAsyncioTestCase):
    """The facade hands a settings file's words to EVERY start, read fresh each
    time, on top of the boot-time overlay — and a reader that fails never
    refuses a start."""

    async def _start_and_read(self, child) -> str:
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "bot.log"
            child._log_path = log
            r = await child.start()
            self.assertTrue(r["ok"], r)
            try:
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    if log.exists() and "WORDS" in log.read_text():
                        break
                return log.read_text()
            finally:
                await child.stop()
                child.close()

    async def test_the_words_are_read_at_each_start_and_win_over_the_overlay(self):
        calls = []

        def reader():
            calls.append(1)
            return {"ZZ_PER_START": f"v{len(calls)}", "ZZ_BOOT": "from-file"}

        child = _fake(REPORTS_ENV, env_overlay={"ZZ_BOOT": "from-boot"}, env_per_start=reader)
        self.assertIn("WORDS v1 from-file", await self._start_and_read(child))
        child = _fake(REPORTS_ENV, env_overlay={"ZZ_BOOT": "from-boot"}, env_per_start=reader)
        self.assertIn("WORDS v2 from-file", await self._start_and_read(child))

    async def test_a_reader_that_fails_does_not_refuse_the_start(self):
        def broken():
            raise ValueError("bad file")

        child = _fake(REPORTS_ENV, env_overlay={"ZZ_BOOT": "from-boot"}, env_per_start=broken)
        self.assertIn("WORDS - from-boot", await self._start_and_read(child))


if __name__ == "__main__":
    unittest.main()
