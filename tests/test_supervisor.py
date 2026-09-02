"""test_supervisor.py — the daemon face (the supervisor core).

Proves, on real subprocesses and a real aiohttp app:
  1. CHILD     — BotChild spawn/stop honors the escalation ladder (SIGINT
                 graceful → exit 0 recorded; a signal-ignoring child is
                 escalated to SIGKILL), refuses double starts, and ADOPTS an
                 already-running process instead of colliding with it.
  2. ROUTES    — /admin/* rides the facade bearer middleware (401 without);
                 /admin/state reports process truth; start/stop round-trips;
                 the catch-all proxy answers an honest offline page/503 when
                 the bot is down.
  3. PARITY    — registry [serve.supervisor] defaults equal the supervisor
                 module constants; the nested block validates (unknown keys
                 warn, never crash).
  4. ACTUATORS — declared commands run bounded (ok / non-zero / timeout-kill),
                 log to 0600 files in a 0700 dir, refuse concurrent runs and
                 unknown names; declared watch names join /admin/state's
                 externals.

No test here spawns the real bot — every child is a stdlib fake with an
injected argv/pattern, so the suite never touches the mic, models, or a
live install.

Run:  .venv/bin/python -m unittest tests.test_supervisor
"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth import supervisor
from hearth.config import settings_registry as sr
from hearth.supervisor import actuators as actuators_mod
from hearth.supervisor import child as child_mod
from hearth.supervisor import routes as routes_mod
from hearth.supervisor.child import BotChild

_PY = sys.executable
_NOMATCH = "zz-hearth-test-nomatch-zz"

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


def _fake(src: str, **kw) -> BotChild:
    kw.setdefault("pattern", _NOMATCH)
    kw.setdefault("stop_grace_s", 5.0)
    kw.setdefault("term_grace_s", 1.0)
    return BotChild(argv=[_PY, "-c", src], **kw)


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

    async def test_stop_when_nothing_runs(self):
        c = _fake(GRACEFUL)
        res = await c.stop()
        self.assertTrue(res["ok"])
        self.assertIn("nothing to stop", res["note"])
        c.close()


class AdminRoutes(AioHTTPTestCase):
    BEARER = {"Authorization": "Bearer test-bearer"}

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none",
            session=None,
        )
        mount = supervisor.build_mount({
            "enabled": True, "panel_url": "http://127.0.0.1:1",
            "watch": {"streamcore": {"url": "http://127.0.0.1:1/"}},
        })
        mount(app)

        async def _open(app_):
            app_["deps"].session = aiohttp.ClientSession()

        async def _close(app_):
            await app_["deps"].session.close()

        app.on_startup.append(_open)
        app.on_cleanup.append(_close)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Deterministic: never adopt a real desk bot into a test.
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)
        # Actuator logs land in a scratch dir, never the real DATA tree.
        self._acts_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._acts_tmp.cleanup)
        self.app["actuators"] = actuators_mod.ActuatorSet(
            {"echo-ok": {"command": [_PY, "-c", "print('actuated')"],
                         "note": "test echo"}},
            log_dir=Path(self._acts_tmp.name) / "actuators")

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def test_bearer_required(self):
        for path in ("/admin/state", "/say"):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 401, path)

    async def test_state_shape(self):
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["supervisor"])
        self.assertEqual(data["bot"]["state"], "down")
        self.assertIn("llm", data["externals"])
        self.assertIn("audio", data["externals"])
        self.assertIs(data["panel"]["reachable"], False)  # dead test port

    async def test_start_stop_roundtrip(self):
        resp = await self.client.post("/admin/bot/start", headers=self.BEARER, json={})
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["mode"], "new")
        resp = await self.client.post("/admin/bot/start", headers=self.BEARER, json={})
        self.assertEqual(resp.status, 409)
        resp = await self.client.post("/admin/bot/stop", headers=self.BEARER, json={})
        self.assertEqual(resp.status, 200)
        self.assertTrue((await resp.json())["ok"])
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual((await resp.json())["bot"]["state"], "down")

    async def test_offline_root_page(self):
        resp = await self.client.get("/", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("offline", text.lower())
        self.assertIn("/admin/bot/start", text)

    async def test_actuator_list_run_unknown(self):
        resp = await self.client.get("/admin/actuators", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        data = (await resp.json())["actuators"]
        self.assertEqual(data["echo-ok"]["note"], "test echo")
        self.assertFalse(data["echo-ok"]["running"])
        self.assertIsNone(data["echo-ok"]["last"])
        resp = await self.client.post("/admin/actuators/echo-ok/run", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        rec = await resp.json()
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["exit"], 0)
        resp = await self.client.post("/admin/actuators/nope/run", headers=self.BEARER)
        self.assertEqual(resp.status, 404)

    async def test_state_carries_declared_watches_and_actuator_names(self):
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        data = await resp.json()
        self.assertIn("streamcore", data["externals"])
        self.assertIs(data["externals"]["streamcore"], False)  # dead test port
        self.assertEqual(data["actuators"], ["echo-ok"])

    async def test_offline_other_paths_503(self):
        resp = await self.client.post("/say", headers=self.BEARER, json={"text": "hi"})
        self.assertEqual(resp.status, 503)
        data = await resp.json()
        self.assertEqual(data["bot"]["state"], "down")


class RegistryParity(unittest.TestCase):
    def test_defaults_match_module_constants(self):
        sup = sr._ServeSupervisor
        self.assertEqual(sup.model_fields["stop_grace_s"].default, child_mod.STOP_GRACE_S)
        self.assertEqual(sup.model_fields["term_grace_s"].default, child_mod.TERM_GRACE_S)
        self.assertEqual(sup.model_fields["panel_url"].default, routes_mod.PANEL_URL)
        self.assertFalse(sup.model_fields["enabled"].default)
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
                            "watch": {"streamcore": {"url": "http://127.0.0.1:8080"}},
                            "actuators": {"lm-unload": {
                                "command": ["/x/lms", "unload", "--all"],
                                "note": "cold stop"}}}},
        )
        self.assertEqual([e for e in errors if "supervisor" in e], [], errors)
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

    def test_commandless_block_skipped_never_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            acts = self._set({"bad": {}, "good": {"command": ["/bin/true"]}}, tmp)
            self.assertNotIn("bad", acts)
            self.assertEqual(acts.names(), ["good"])


if __name__ == "__main__":
    unittest.main()
