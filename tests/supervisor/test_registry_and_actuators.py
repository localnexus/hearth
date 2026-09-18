"""Supervisor — settings-registry parity and the actuator engine.

The supervisor's config surface matches the declared schema, and declared
actuators run (or refuse) as configured.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from hearth import supervisor
from hearth.config import settings_registry as sr
from hearth.supervisor import actuators as actuators_mod
from hearth.supervisor import child as child_mod
from hearth.supervisor import routes as routes_mod


_PY = sys.executable


class RegistryParity(unittest.TestCase):
    def test_defaults_match_module_constants(self):
        sup = sr._ServeSupervisor
        self.assertEqual(sup.model_fields["stop_grace_s"].default, child_mod.STOP_GRACE_S)
        self.assertEqual(sup.model_fields["term_grace_s"].default, child_mod.TERM_GRACE_S)
        self.assertEqual(sup.model_fields["panel_url"].default, routes_mod.PANEL_URL)
        self.assertFalse(sup.model_fields["enabled"].default)
        self.assertTrue(sup.model_fields["compact_watch"].default)  # watch ships ON
        self.assertIsNone(sr.ServeTable.model_fields["supervisor"].default)

    def test_supervisor_block_validates(self):
        errors, warnings = sr.strict_check(
            "serve",
            {"enabled": True,
             "supervisor": {"enabled": True, "stop_grace_s": 20.0, "bogus": 1,
                            "env": {"LM_PROVIDER": "lmstudio"}}},
        )
        self.assertTrue(any("supervisor.bogus" in w for w in warnings), warnings)
        self.assertEqual([e for e in errors if "supervisor" in e], [], errors)

    def test_type_violation_fails_loader(self):
        with self.assertRaises(sr.SchemaError):
            sr.loader_check("serve", {"enabled": True, "supervisor": {"enabled": "yes-please"}})

    def test_actuator_defaults_and_validation(self):
        act = sr._SupActuator
        self.assertEqual(act.model_fields["timeout_s"].default,
                         actuators_mod.DEFAULT_TIMEOUT_S)
        errors, warnings = sr.strict_check(
            "serve",
            {"enabled": True,
             "supervisor": {"enabled": True,
                            "watch": {"myservice": {"url": "http://127.0.0.1:8080"}},
                            "actuators": {"lm-unload": {
                                "command": ["/x/lms", "unload", "--all"],
                                "note": "cold stop", "guard": "companion"}}}},
        )
        self.assertEqual([e for e in errors if "supervisor" in e], [], errors)
        self.assertEqual(act.model_fields["guard"].default, "")
        # an empty command is a config error, not a runtime surprise
        errors, _ = sr.strict_check(
            "serve",
            {"enabled": True,
             "supervisor": {"enabled": True,
                            "actuators": {"bad": {"command": []}}}},
        )
        self.assertTrue(any("command" in e for e in errors), errors)


class ActuatorEngine(unittest.IsolatedAsyncioTestCase):
    """The bounded-run engine, on real subprocesses in a scratch tree."""

    def _set(self, acts: dict, tmp: str) -> actuators_mod.ActuatorSet:
        return actuators_mod.ActuatorSet(acts, log_dir=Path(tmp) / "logs")

    async def test_ok_run_logs_at_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({"ok": {"command": [_PY, "-c", "print('actuated-marker')"]}}, tmp)
            rec = await acts.run("ok")
            self.assertTrue(rec["ok"])
            self.assertEqual(rec["exit"], 0)
            self.assertFalse(rec["timed_out"])
            log = Path(rec["log"])
            self.assertIn("actuated-marker", log.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)
            self.assertEqual(acts.status()["ok"]["last"]["exit"], 0)

    async def test_nonzero_exit_reported_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({"no": {"command": [_PY, "-c", "import sys; sys.exit(3)"]}}, tmp)
            rec = await acts.run("no")
            self.assertFalse(rec["ok"])
            self.assertEqual(rec["exit"], 3)

    async def test_timeout_kills_the_command_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({"slow": {"command": [_PY, "-c", "import time; time.sleep(30)"],
                                       "timeout_s": 0.4}}, tmp)
            rec = await acts.run("slow")
            self.assertFalse(rec["ok"])
            self.assertTrue(rec["timed_out"])
            self.assertLess(rec["duration_s"], 10.0)

    async def test_busy_refused_and_unknown_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({"hold": {"command": [_PY, "-c", "import time; time.sleep(1.5)"],
                                       "timeout_s": 10.0}}, tmp)
            task = asyncio.ensure_future(acts.run("hold"))
            await asyncio.sleep(0.3)
            with self.assertRaises(actuators_mod.ActuatorBusy):
                await acts.run("hold")
            rec = await task
            self.assertTrue(rec["ok"])
            with self.assertRaises(KeyError):
                await acts.run("nope")

    def test_guard_is_carried_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({"cold": {"command": ["/bin/true"], "guard": "companion"},
                              "any": {"command": ["/bin/true"]}}, tmp)
            self.assertEqual(acts.guard("cold"), "companion")
            self.assertEqual(acts.guard("any"), "")
            self.assertEqual(acts.status()["cold"]["guard"], "companion")
            self.assertEqual(acts.status()["any"]["guard"], "")

    async def test_stepped_run_presses_each_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            mark = Path(tmp) / "order"
            acts = self._set({
                "a": {"command": [_PY, "-c",
                                  f"open({str(mark)!r}, 'a').write('a')"]},
                "b": {"command": [_PY, "-c",
                                  f"open({str(mark)!r}, 'a').write('b')"]},
                "both": {"steps": ["a", "b"]},
            }, tmp)
            rec = await acts.run("both")
            self.assertTrue(rec["ok"])
            self.assertIsNone(rec["failed_step"])
            self.assertEqual([s["actuator"] for s in rec["steps"]], ["a", "b"])
            self.assertEqual(mark.read_text(), "ab")
            log = Path(rec["log"]).read_text(encoding="utf-8")
            self.assertIn("step a: exit 0", log)
            self.assertIn("step b: exit 0", log)
            self.assertEqual(acts.status()["a"]["last"]["exit"], 0)   # each step is a run

    async def test_stepped_run_stops_at_the_first_bad_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            mark = Path(tmp) / "reached"
            acts = self._set({
                "no": {"command": [_PY, "-c", "import sys; sys.exit(3)"]},
                "b": {"command": [_PY, "-c", f"open({str(mark)!r}, 'w').write('x')"]},
                "seq": {"steps": ["no", "b"]},
                "lenient": {"steps": [{"actuator": "no", "accept": [0, 3]}, "b"]},
            }, tmp)
            rec = await acts.run("seq")
            self.assertFalse(rec["ok"])
            self.assertEqual(rec["failed_step"], "no")
            self.assertEqual(rec["exit"], 3)
            self.assertFalse(mark.exists())
            rec = await acts.run("lenient")         # exit 3 accepted → goes on
            self.assertTrue(rec["ok"])
            self.assertTrue(mark.exists())

    async def test_stepped_run_retries_until_the_step_takes(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = Path(tmp) / "second-time"
            body = (f"import os, sys; p={str(flag)!r}; "
                    "sys.exit(0) if os.path.exists(p) else (open(p, 'w').close(), sys.exit(5))")
            acts = self._set({
                "flaky": {"command": [_PY, "-c", body]},
                "keep": {"steps": [{"actuator": "flaky", "retry_s": 10.0}]},
                "once": {"steps": [{"actuator": "flaky"}]},
            }, tmp)
            rec = await acts.run("keep")
            self.assertTrue(rec["ok"])              # failed once, then took
            self.assertIn("pressing again", Path(rec["log"]).read_text(encoding="utf-8"))
            flag.unlink()
            rec = await acts.run("once")
            self.assertFalse(rec["ok"])             # no retry_s → one press

    async def test_stepped_run_waits_for_the_probe_port_to_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            acts = self._set({
                "stop": {"command": [_PY, "-c", "pass"], "probe_url": f"http://127.0.0.1:{port}/health"},
                "go": {"command": [_PY, "-c", "pass"]},
                "bounce": {"steps": [{"actuator": "stop", "until": "probe-down", "wait_s": 5.0},
                                     "go"]},
                "hurry": {"steps": [{"actuator": "stop", "until": "probe-down", "wait_s": 0.6},
                                    "go"]},
            }, tmp)
            try:
                rec = await acts.run("hurry")       # the port stays open → fails there
                self.assertFalse(rec["ok"])
                self.assertEqual(rec["failed_step"], "stop")
                self.assertEqual([s["actuator"] for s in rec["steps"]], ["stop"])

                async def close_soon():
                    await asyncio.sleep(0.8)
                    server.close()
                    await server.wait_closed()
                closer = asyncio.ensure_future(close_soon())
                rec = await acts.run("bounce")      # waits, sees it leave, goes on
                await closer
                self.assertTrue(rec["ok"])
                self.assertEqual([s["actuator"] for s in rec["steps"]], ["stop", "go"])
                self.assertGreaterEqual(rec["duration_s"], 0.5)
            finally:
                server.close()

    async def test_stepped_run_refuses_a_busy_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({
                "hold": {"command": [_PY, "-c", "import time; time.sleep(1.2)"], "timeout_s": 10.0},
                "seq": {"steps": ["hold"]},
            }, tmp)
            task = asyncio.ensure_future(acts.run("hold"))
            await asyncio.sleep(0.2)
            rec = await acts.run("seq")
            self.assertFalse(rec["ok"])
            self.assertEqual(rec["failed_step"], "hold")
            self.assertTrue((await task)["ok"])

    def test_commandless_block_skipped_never_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({"bad": {}, "good": {"command": ["/bin/true"]}}, tmp)
            self.assertNotIn("bad", acts)
            self.assertEqual(acts.names(), ["good"])

if __name__ == "__main__":
    unittest.main()
