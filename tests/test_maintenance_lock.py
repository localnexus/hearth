"""maintenance_lock + compact_trigger — the auto-compaction primitives.

Run directly: .venv/bin/python tests/test_maintenance_lock.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from hearth.config import config_loader  # noqa: E402
from hearth.session import compact_trigger, maintenance_lock  # noqa: E402


class LockBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = mock.patch.object(config_loader, "DATA_DIR", self.root)
        self._patch.start()
        maintenance_lock._HELD.clear()

    def tearDown(self):
        for char in list(maintenance_lock._HELD):
            maintenance_lock.drop(char)
        self._patch.stop()
        self._tmp.cleanup()


class LockCore(LockBase):
    def test_acquire_probe_release(self):
        self.assertIsNone(maintenance_lock.probe("example"))  # free (no file yet)
        h = maintenance_lock.acquire("example", op="compact", session="s-01")
        self.assertIsNotNone(h)
        info = maintenance_lock.probe("example")
        self.assertEqual(info["op"], "compact")
        self.assertEqual(info["session"], "s-01")
        self.assertEqual(info["pid"], os.getpid())
        h.release()
        self.assertIsNone(maintenance_lock.probe("example"))  # kernel truth, stale JSON ignored

    def test_same_process_second_fd_conflicts(self):
        # flock treats a second fd as a rival even in one process — the
        # registry's idempotency depends on this being true on this platform.
        h = maintenance_lock.acquire("example", op="session")
        self.assertIsNotNone(h)
        self.assertIsNone(maintenance_lock.acquire("example", op="compact"))
        h.release()

    def test_registry_hold_idempotent_and_drop(self):
        self.assertTrue(maintenance_lock.hold("example", op="session"))
        self.assertTrue(maintenance_lock.hold("example", op="session"))  # already ours
        self.assertIsNone(maintenance_lock.acquire("example", op="x"))  # rival fd loses
        maintenance_lock.drop("example")
        maintenance_lock.drop("example")  # no-op
        self.assertIsNone(maintenance_lock.probe("example"))

    def test_kill_dash_nine_releases(self):
        # A child acquires and is SIGKILLed — the MUST case: no stale lock.
        code = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from hearth.session import maintenance_lock\n"
            "h = maintenance_lock.acquire('example', op='compact', session='doom')\n"
            "assert h is not None\n"
            "print('locked', flush=True)\n"
            "import time; time.sleep(30)\n"
        )
        env = {**os.environ, "HEARTH_DATA": str(self.root)}
        proc = subprocess.Popen([sys.executable, "-c", code, str(_SRC)],
                                env=env, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            info = maintenance_lock.probe("example")
            self.assertEqual(info["session"], "doom")  # held cross-process
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            deadline = time.time() + 5
            while maintenance_lock.probe("example") is not None:
                self.assertLess(time.time(), deadline, "lock survived SIGKILL")
                time.sleep(0.05)
        finally:
            proc.kill()
        h = maintenance_lock.acquire("example", op="compact")
        self.assertIsNotNone(h)
        h.release()

    def test_held_locks_and_describe(self):
        maintenance_lock.hold("example", op="compact", session="big-one")
        maintenance_lock.hold("other", op="session")
        all_held = maintenance_lock.held_locks()
        self.assertEqual([x["character"] for x in all_held], ["example", "other"])
        compacts = maintenance_lock.held_locks(op="compact")
        self.assertEqual(len(compacts), 1)
        line = maintenance_lock.describe(compacts[0])
        self.assertIn("compaction of big-one", line)

    def test_invalid_character_refused(self):
        with self.assertRaises(ValueError):
            maintenance_lock.lock_path("../oops")

    def test_cli_run_holds_and_propagates_rc(self):
        env = {**os.environ, "HEARTH_DATA": str(self.root),
               "PYTHONPATH": str(_SRC)}
        # rc propagation through exec
        rc = subprocess.run(
            [sys.executable, "-m", "hearth.session.maintenance_lock",
             "run", "example", "--op", "compact", "--", "/bin/sh", "-c", "exit 7"],
            env=env).returncode
        self.assertEqual(rc, 7)
        # while the wrapped command runs, the lock is observably held
        probe_cmd = (f"{sys.executable} -m hearth.session.maintenance_lock "
                     f"probe example > $OUT 2>&1; echo $? >> $OUT")
        out = self.root / "probe.out"
        rc = subprocess.run(
            [sys.executable, "-m", "hearth.session.maintenance_lock",
             "run", "example", "--op", "compact", "--session", "s-9",
             "--", "/bin/sh", "-c", probe_cmd],
            env={**env, "OUT": str(out)}).returncode
        self.assertEqual(rc, 0)
        lines = out.read_text().strip().splitlines()
        self.assertIn("held — compaction of s-9", lines[0])
        self.assertEqual(lines[-1], "3")
        self.assertIsNone(maintenance_lock.probe("example"))  # released at exit

    def test_cli_run_refused_when_held(self):
        maintenance_lock.hold("example", op="session")
        env = {**os.environ, "HEARTH_DATA": str(self.root),
               "PYTHONPATH": str(_SRC)}
        res = subprocess.run(
            [sys.executable, "-m", "hearth.session.maintenance_lock",
             "run", "example", "--", "/bin/sh", "-c", "echo never"],
            env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 3)
        self.assertIn("held — session", res.stdout)
        self.assertNotIn("never", res.stdout)


class TriggerTests(LockBase):
    def _store(self, size: int, *, held=True, character="example", name="long-run"):
        path = self.root / f"{name}.json"
        path.write_bytes(b"x" * size)
        return SimpleNamespace(path=path, held=held, character=character)

    def qdir(self):
        return compact_trigger.queue_dir()

    def test_small_or_unheld_or_anon_no_request(self):
        self.assertIsNone(compact_trigger.maybe_request(self._store(1000)))
        self.assertIsNone(compact_trigger.maybe_request(
            self._store(300_000, held=False)))
        self.assertIsNone(compact_trigger.maybe_request(
            self._store(300_000, character=None)))
        self.assertIsNone(compact_trigger.maybe_request(None))
        self.assertFalse(self.qdir().exists())

    def test_big_session_writes_request(self):
        note = compact_trigger.maybe_request(self._store(200_000))
        self.assertIn("auto-compaction requested", note)
        req = self.qdir() / "example.long-run.request"
        data = json.loads(req.read_text())
        self.assertEqual(data["character"], "example")
        self.assertEqual(data["session"], "long-run")
        self.assertEqual(data["est_tokens"], 50_000)
        self.assertEqual(data["source"], "bytes/4")

    def test_live_tokens_can_trip_a_small_file(self):
        note = compact_trigger.maybe_request(
            self._store(10_000), live_tokens=45_000)
        self.assertIn("auto-compaction requested", note)
        data = json.loads((self.qdir() / "example.long-run.request").read_text())
        self.assertEqual(data["est_tokens"], 45_000)
        self.assertEqual(data["source"], "prompt-meter")

    def test_failed_breadcrumb_blocks_rerequest(self):
        self.qdir().mkdir(parents=True)
        (self.qdir() / "example.long-run.failed").write_text("{}")
        note = compact_trigger.maybe_request(self._store(200_000))
        self.assertIn("prior attempt failed", note)
        self.assertFalse((self.qdir() / "example.long-run.request").exists())

    def test_running_claim_is_left_alone(self):
        self.qdir().mkdir(parents=True)
        (self.qdir() / "example.long-run.running").write_text("{}")
        self.assertIsNone(compact_trigger.maybe_request(self._store(200_000)))

    def test_never_raises(self):
        broken = SimpleNamespace(path=self.root / "gone.json", held=True,
                                 character="example")
        self.assertIsNone(compact_trigger.maybe_request(broken))  # missing file
        weird = SimpleNamespace(path=object(), held=True, character="example")
        note = compact_trigger.maybe_request(weird)
        self.assertTrue(note is None or "failed" in note)


if __name__ == "__main__":
    unittest.main(verbosity=1)
