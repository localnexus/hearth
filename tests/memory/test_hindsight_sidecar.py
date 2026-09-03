"""Hindsight backend — the sidecar survives its own child (incident 2026-08-30).

The child's stdout+stderr land in a 0600 logfile in a 0700 dir and the pipe is
drained for life; an oversized log rotates to .1 at spawn; a dead child is
respawned exactly ONCE (a second death raises); close() after a death is quiet.
All against a stub runner — no hindsight install, no network.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))




class _FakeClient:
    """Stands in for hindsight_client.Hindsight — the SDK is not installed here
    (and must never be needed to test the adapter's process plumbing)."""

    def __init__(self, url: str | None) -> None:
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestHindsightSidecar(unittest.TestCase):
    """Sidecar plumbing only — no hindsight install needed: a stub runner stands
    in for the real server (spawn → parse HINDSIGHT_URL → terminate)."""

    def test_spawn_parse_terminate(self):
        from hearth.memory.backend_hindsight import HindsightBackend
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub_runner.py"
            stub.write_text(
                "import time\n"
                "print('startup noise', flush=True)\n"
                "print('HINDSIGHT_URL=http://127.0.0.1:59999', flush=True)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            b = HindsightBackend({"mode": "sidecar", "python": sys.executable,
                                  "runner": str(stub), "llm_model": "m",
                                  "log_file": str(Path(tmp) / "logs" / "sidecar.log")})
            b._start_sidecar()
            proc = b._proc
            try:
                self.assertEqual(b._url, "http://127.0.0.1:59999")
                self.assertIsNone(proc.poll())  # still running until close
                # Own process group (start_new_session): the operator's Ctrl+C
                # must never reach the sidecar (run-observed 2026-08-30 — the
                # terminal's SIGINT killed it before the close-time store).
                self.assertNotEqual(os.getpgid(proc.pid), os.getpgid(os.getpid()))
            finally:
                b.close()
            self.assertIsNotNone(proc.poll())   # terminated by close
            self.assertIsNone(b._proc)

    def test_sidecar_requires_python_path(self):
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "llm_model": "m"})
        with self.assertRaises(ValueError):
            b._start_sidecar()

    def test_call_pins_one_persistent_thread_in_async_context(self):
        """Regression (run-observed 2026-08-30, first in-bot store): the SDK
        caches an aiohttp ClientSession on the first call's event loop, so all
        async-context calls must share ONE persistent worker thread — per-call
        threads leave the session on a dead loop (RuntimeError on call #2)."""
        from hearth.memory.backend_hindsight import HindsightBackend

        b = HindsightBackend({"mode": "sidecar", "llm_model": "m"})
        idents: list[int] = []

        async def scenario():
            idents.append(b._call(threading.get_ident))
            idents.append(b._call(threading.get_ident))

        asyncio.run(scenario())
        self.assertEqual(idents[0], idents[1])            # same worker thread
        self.assertNotEqual(idents[0], threading.get_ident())  # not the caller's
        b.close()  # shuts the pool with no client/proc — must not raise
        # sync context (CLI rebuild) rides the SAME lane — since 2026-09-02
        # there is no direct path in any context (a caller-context dispatch
        # broke the moment the voice prefetch's to_thread calls mixed with the
        # bot's async calls); close() re-creates the pool on the next call
        self.assertNotEqual(b._call(threading.get_ident), threading.get_ident())
        b.close()

    # ── the 2026-08-30 incident: a child that died blind and undrained ───────

    _NOISY = (
        "import sys, time\n"
        "print('startup noise', flush=True)\n"
        "print('HINDSIGHT_URL=http://127.0.0.1:59999', flush=True)\n"
        "print('post-handshake stdout line', flush=True)\n"
        "sys.stderr.write('stderr complaint\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )
    _DIES = "import sys\nsys.exit(3)\n"

    def _stub(self, tmp: Path, name: str, src: str) -> Path:
        path = tmp / f"{name}.py"
        path.write_text(src, encoding="utf-8")
        return path

    def _backend(self, tmp: Path, runner: Path, log: Path):
        from hearth.memory.backend_hindsight import HindsightBackend
        return HindsightBackend({"mode": "sidecar", "python": sys.executable,
                                 "runner": str(runner), "llm_model": "m",
                                 "log_file": str(log)})

    def _wait_for(self, log: Path, needle: str, timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        text = ""
        while time.monotonic() < deadline:
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            if needle in text:
                return text
            time.sleep(0.05)
        return text

    def test_child_stdout_and_stderr_land_in_the_logfile_at_0600(self):
        """Both holes from the incident, in one run: stderr no longer goes to
        DEVNULL, and stdout keeps being drained after the handshake line."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            b._start_sidecar()
            try:
                text = self._wait_for(log, "stderr complaint")
            finally:
                b.close()
            self.assertIn("startup noise", text)              # pre-handshake stdout
            self.assertIn("post-handshake stdout line", text)  # the drain thread
            self.assertIn("stderr complaint", text)            # stderr, not DEVNULL
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)
            self.assertIsNone(b._log)                          # handle released by close()

    def test_oversized_log_rotates_to_dot_one_at_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            log.parent.mkdir(parents=True)
            log.write_text("x" * (5 * 1024 * 1024 + 1), encoding="utf-8")
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            b._start_sidecar()
            b.close()
            rotated = log.with_name(log.name + ".1")
            self.assertTrue(rotated.is_file())
            self.assertGreater(rotated.stat().st_size, 5 * 1024 * 1024)
            self.assertLess(log.stat().st_size, 4096)          # a fresh generation

    def test_dead_sidecar_respawns_once_and_a_second_death_raises(self):
        """The store at session close used to die on ClientConnectorError when
        the child was gone (run-observed). _ensure now notices and respawns —
        once. The SDK is absent here, so _new_client is the seam."""
        from hearth.memory import backend_hindsight as hs
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            clients: list[_FakeClient] = []
            b._new_client = lambda: clients.append(_FakeClient(b._url)) or clients[-1]

            b._ensure()
            first = b._proc
            self.assertEqual(len(clients), 1)
            b._ensure()                       # alive: no respawn, no new client
            self.assertIs(b._proc, first)
            self.assertEqual(len(clients), 1)

            first.kill()
            first.wait()
            with mock.patch.object(hs.logger, "warning") as warn:
                b._ensure()
            observed = [c.args[1] for c in warn.call_args_list
                        if "died (rc=" in str(c.args[0])]
            self.assertEqual(observed, [first.returncode])     # the old rc was named
            self.assertIsNot(b._proc, first)                   # exactly one respawn
            self.assertIsNone(b._proc.poll())
            self.assertEqual(len(clients), 2)
            self.assertTrue(clients[0].closed)                 # stale client retired

            # a sidecar that cannot come back propagates instead of looping
            b._cfg["runner"] = str(self._stub(tmp, "dies", self._DIES))
            b._proc.kill()
            b._proc.wait()
            with self.assertRaises(RuntimeError):
                b._ensure()
            b.close()

    def test_close_after_child_death_skips_terminate_and_resets(self):
        from hearth.memory import backend_hindsight as hs
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            b._start_sidecar()
            proc = b._proc
            proc.kill()
            proc.wait()
            with mock.patch.object(hs.logger, "warning") as warn:
                b.close()                                      # must not raise
            self.assertTrue(any("already exited" in str(c.args[0])
                                for c in warn.call_args_list))
            self.assertIsNone(b._proc)
            self.assertIsNone(b._client)
            self.assertIsNone(b._url)
            self.assertIsNone(b._log)
            self.assertFalse(b._drain.is_alive() if b._drain else False)

class TestOrphanSweep(unittest.TestCase):
    """The orphan sweep (2026-09-03): a sidecar outlives an ungraceful host
    death, because it holds its own process group so only close() ends it.

    Ten survivors (9.4 GB) were found after a day of bot startup crashes. These
    tests pin the sweep's SAFETY rule, not its plumbing: it must only ever
    signal processes whose parent is already gone.
    """

    _RUNNER = "/opt/hearth/src/hearth/memory/sidecar_runner.py"

    def _sweep(self, pgrep_stdout: str, kill=None):
        """Run the sweep against a canned pgrep result; returns (n, killed)."""
        import hearth.memory.backend_hindsight as hs

        killed: list = []

        def _kill(pid, sig):
            killed.append((pid, sig))
            if kill is not None:
                kill(pid)

        completed = mock.Mock(stdout=pgrep_stdout)
        with mock.patch.object(hs.subprocess, "run", return_value=completed) as run, \
                mock.patch.object(hs.os, "kill", side_effect=_kill):
            n = hs._reap_orphaned_sidecars(self._RUNNER)
        self.argv = list(run.call_args.args[0])
        return n, killed

    def test_sweep_is_scoped_to_parentless_processes(self):
        """THE safety property. ``-P 1`` is what makes this safe to run while a
        bot and the facade are both live: their sidecars are parented to them,
        so only an abandoned one is ever matched. Do not 'simplify' this away."""
        self._sweep("")
        self.assertIn("-P", self.argv)
        self.assertEqual(self.argv[self.argv.index("-P") + 1], "1",
                         "the sweep must only ever match processes reparented "
                         "to launchd — without -P 1 it would kill LIVE sidecars")
        self.assertIn(self._RUNNER, self.argv,
                      "matching is on the runner path — another checkout's "
                      "sidecars are that install's business")

    def test_sweep_signals_every_orphan_once(self):
        import signal as sig_mod

        n, killed = self._sweep("111\n222\n333\n")
        self.assertEqual(n, 3)
        self.assertEqual([p for p, _ in killed], [111, 222, 333])
        self.assertEqual({s for _, s in killed}, {sig_mod.SIGTERM},
                         "SIGTERM only — the orphan must get to close its pg0 "
                         "connection; a straggler is the next sweep's problem")

    def test_sweep_never_signals_itself(self):
        n, killed = self._sweep(f"111\n{os.getpid()}\n222\n")
        self.assertEqual(n, 2)
        self.assertNotIn(os.getpid(), [p for p, _ in killed])

    def test_sweep_tolerates_a_pid_that_already_exited(self):
        def _gone(pid):
            if pid == 222:
                raise ProcessLookupError

        n, _ = self._sweep("111\n222\n333\n", kill=_gone)
        self.assertEqual(n, 2, "a pid that died between pgrep and kill is not "
                               "an error, and must not stop the sweep")

    def test_sweep_survives_a_missing_pgrep(self):
        """Best-effort: a sweep that cannot run must never fail a session start."""
        import hearth.memory.backend_hindsight as hs

        with mock.patch.object(hs.subprocess, "run", side_effect=OSError), \
                mock.patch.object(hs.os, "kill") as kill:
            self.assertEqual(hs._reap_orphaned_sidecars(self._RUNNER), 0)
        kill.assert_not_called()

    def test_start_sidecar_sweeps_before_spawning(self):
        """Ordering is the point — sweeping after the spawn would reclaim
        nothing for the session that just paid to start."""
        import hearth.memory.backend_hindsight as hs

        order: list = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner = tmp / "stub.py"
            runner.write_text(
                "import time\n"
                "print('HINDSIGHT_URL=http://127.0.0.1:59999', flush=True)\n"
                "time.sleep(60)\n", encoding="utf-8")
            backend = hs.HindsightBackend(
                {"mode": "sidecar", "python": sys.executable, "runner": str(runner),
                 "llm_model": "m", "log_file": str(tmp / "logs" / "s.log")})
            real_popen = hs.subprocess.Popen

            def _popen(*a, **kw):
                order.append("spawn")
                return real_popen(*a, **kw)

            with mock.patch.object(hs, "_reap_orphaned_sidecars",
                                   side_effect=lambda r: order.append(("sweep", r))), \
                    mock.patch.object(hs.subprocess, "Popen", side_effect=_popen):
                backend._start_sidecar()
            try:
                self.assertEqual([o[0] if isinstance(o, tuple) else o for o in order],
                                 ["sweep", "spawn"])
                self.assertEqual(order[0][1], str(runner),
                                 "the sweep must match OUR runner path")
            finally:
                backend.close()


if __name__ == "__main__":
    unittest.main()
