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
import json
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
            "watch": {"myservice": {"url": "http://127.0.0.1:1/"}},
            "compact_watch": False,  # unit tests never scan the real queue
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

    async def test_start_memory_rider(self):
        resp = await self.client.post("/admin/bot/start", headers=self.BEARER,
                                      json={"memory": "bogus"})
        self.assertEqual(resp.status, 409)
        self.assertIn("memory mode", (await resp.json())["error"])
        resp = await self.client.post("/admin/bot/start", headers=self.BEARER,
                                      json={"memory": "off"})
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual((await resp.json())["memory"], "off")
        await self.client.post("/admin/bot/stop", headers=self.BEARER, json={})

    async def test_sessions_listing_metadata_only(self):
        from unittest import mock

        from hearth.config import config_loader
        from hearth.session import session_store

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            char_dir = root / "characters" / "zz-sup-test"
            char_dir.mkdir(parents=True)
            (char_dir / "persona.md").write_text("test persona marker")
            store = session_store.SessionStore(
                session_id="session-x", model="m1", voice="v1",
                prompt_sha256="d", sessions_dir=char_dir / "sessions",
                character="zz-sup-test", memory_mode="recall-only")
            store.snapshot([{"role": "user", "content": "SENSITIVE-CONTENT"}])
            with mock.patch.object(config_loader, "_DATA", root):
                resp = await self.client.get("/admin/sessions?character=zz-sup-test",
                                             headers=self.BEARER)
                self.assertEqual(resp.status, 200, await resp.text())
                data = await resp.json()
                self.assertEqual(data["character"], "zz-sup-test")
                self.assertEqual(len(data["sessions"]), 1)
                meta = data["sessions"][0]
                self.assertEqual(meta["session_id"], "session-x")
                self.assertEqual(meta["turns"], 1)
                self.assertEqual(meta["memory_mode"], "recall-only")
                self.assertNotIn("SENSITIVE",
                                 json.dumps(data), "listing must never carry content")
                self.assertNotIn("path", meta, "file paths are not exposed")
                resp = await self.client.get("/admin/sessions?character=zz-nope",
                                             headers=self.BEARER)
                self.assertEqual(resp.status, 404)

    async def test_offline_root_page(self):
        resp = await self.client.get("/", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("offline", text.lower())
        self.assertIn("/admin/bot/start", text)
        self.assertIn("/admin/launch", text, "offline page points at the launch surface")

    async def test_launch_page_is_static_chrome(self):
        # Reachable WITHOUT the bearer (the one static-chrome exemption) …
        resp = await self.client.get("/admin/launch")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/html")
        text = await resp.text()
        self.assertIn("Hearth", text)
        self.assertIn("/admin/state", text)  # it drives the authed API
        # … and therefore must carry ZERO state: no bearer, no names.
        self.assertNotIn("test-bearer", text, "the shell must never embed the token")
        # Also fine with the bearer (same page either way).
        resp = await self.client.get("/admin/launch", headers=self.BEARER)
        self.assertEqual(resp.status, 200)

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
        self.assertIn("myservice", data["externals"])
        self.assertIs(data["externals"]["myservice"], False)  # dead test port
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


class _FakeCurationBackend:
    name = "fakehs"

    def __init__(self) -> None:
        self.forgot: list[tuple[str, str]] = []
        self.raise_on_forget = False
        self.result: bool | None = True

    def forget(self, companion, session_id):  # noqa: ANN001
        if self.raise_on_forget:
            raise RuntimeError("backend down")
        self.forgot.append((companion, session_id))
        return self.result


class _FakeCountingBackend(_FakeCurationBackend):
    """A curation backend that also exposes the optional fact_count capability."""

    def __init__(self) -> None:
        super().__init__()
        self.counted: list[str] = []
        self.count_result: dict = {"facts": 7, "capped": False}
        self.raise_on_count = False

    def fact_count(self, companion):  # noqa: ANN001
        if self.raise_on_count:
            raise RuntimeError("backend down")
        self.counted.append(companion)
        return self.count_result


class _FakeGlue:
    """The two ServeMemory methods /admin/memory uses, nothing more."""

    def __init__(self, backend) -> None:  # noqa: ANN001 — None = companion "none"
        self._backend = backend

    def backend_name_for(self, companion):  # noqa: ANN001
        return self._backend.name if self._backend is not None else "none"

    def curation_backend(self, companion):  # noqa: ANN001
        return self._backend


class CurationRoutes(AioHTTPTestCase):
    """/admin/memory: bearer-gated digest views + preview-then-confirm forget,
    CLI-parity ordering (backend facts first — a failed index update keeps the
    record), against a scratch DATA tree and a fake facade memory glue."""

    BEARER = {"Authorization": "Bearer test-bearer"}
    CHAR = "zz-cur-test"

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none",
            session=None,
            memory=None,  # per-test: a _FakeGlue or None
        )
        mount = supervisor.build_mount({"enabled": True,
                                        "panel_url": "http://127.0.0.1:1",
                                        "compact_watch": False})
        mount(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Deterministic: never adopt a real desk bot into a test.
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)
        # Scratch DATA tree with one known character + two ended-session records.
        from unittest import mock

        from hearth.config import config_loader
        from hearth.memory import records as records_mod
        from hearth.memory.backend import SessionRecord

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        char_dir = root / "characters" / self.CHAR
        char_dir.mkdir(parents=True)
        (char_dir / "persona.md").write_text("test persona marker")
        self.records_dir = char_dir / "memory" / "records"
        for sid, ended in (("sess-a", "2026-08-30T09:00:00"),
                           ("sess-b", "2026-08-31T09:00:00")):
            records_mod.write_record(SessionRecord(
                companion=self.CHAR, session_id=sid,
                started="2026-08-30T08:00:00", ended=ended, name="",
                messages=[{"role": "user", "content": f"SECRET-USER-LINE {sid}"},
                          {"role": "assistant", "content": f"a reply in {sid}"}],
            ), self.records_dir)
        self._patch = mock.patch.object(config_loader, "_DATA", root)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def test_bearer_required(self):
        self.assertEqual((await self.client.get("/admin/memory")).status, 401)
        self.assertEqual(
            (await self.client.get("/admin/memory/records?character=x")).status, 401)
        self.assertEqual(
            (await self.client.get("/admin/memory/facts?character=x")).status, 401)
        self.assertEqual(
            (await self.client.post("/admin/memory/forget", json={})).status, 401)

    async def test_pane_shell_is_unauthed_static_chrome(self):
        resp = await self.client.get("/admin/memory/ui")  # no bearer
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("memory", text.lower())
        self.assertNotIn("test-bearer", text)
        self.assertNotIn(self.CHAR, text)  # chrome carries no names

    async def test_facts_lazy_gauge(self):
        backend = _FakeCountingBackend()
        self.app["deps"].memory = _FakeGlue(backend)
        url = f"/admin/memory/facts?character={self.CHAR}"
        resp = await self.client.get(url, headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual(data["facts"], 7)
        self.assertFalse(data["capped"])
        self.assertEqual(data["backend"], "fakehs")
        self.assertEqual(backend.counted, [self.CHAR])
        # Backend without the capability (the floor) → honest null + note.
        self.app["deps"].memory = _FakeGlue(_FakeCurationBackend())
        data = await (await self.client.get(url, headers=self.BEARER)).json()
        self.assertIsNone(data["facts"])
        self.assertIn("no fact index", data["note"])
        # Companion mapped "none" → null, backend named honestly.
        self.app["deps"].memory = _FakeGlue(None)
        data = await (await self.client.get(url, headers=self.BEARER)).json()
        self.assertIsNone(data["facts"])
        self.assertEqual(data["backend"], "none")
        # Lane down → null + the lane note; the view still answers 200.
        self.app["deps"].memory = None
        data = await (await self.client.get(url, headers=self.BEARER)).json()
        self.assertIsNone(data["facts"])
        self.assertIn("memory lane", data["note"])
        # Unknown character → 404; backend failure → 502 with the type name.
        resp = await self.client.get("/admin/memory/facts?character=zz-nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)
        failing = _FakeCountingBackend()
        failing.raise_on_count = True
        self.app["deps"].memory = _FakeGlue(failing)
        resp = await self.client.get(url, headers=self.BEARER)
        self.assertEqual(resp.status, 502)
        self.assertIn("RuntimeError", (await resp.json())["error"])

    async def test_overview_counts_and_lane_flag(self):
        backend = _FakeCurationBackend()
        self.app["deps"].memory = _FakeGlue(backend)
        resp = await self.client.get("/admin/memory", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertTrue(data["memory_lane"])
        row = next(c for c in data["companions"] if c["character"] == self.CHAR)
        self.assertEqual(row["records"], 2)
        self.assertEqual(row["backend"], "fakehs")
        # Views stay useful with the lane down — backend map honestly absent.
        self.app["deps"].memory = None
        data = await (await self.client.get("/admin/memory",
                                            headers=self.BEARER)).json()
        self.assertFalse(data["memory_lane"])
        row = next(c for c in data["companions"] if c["character"] == self.CHAR)
        self.assertIsNone(row["backend"])

    async def test_records_view_digest_not_transcript(self):
        resp = await self.client.get(f"/admin/memory/records?character={self.CHAR}",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual([r["session_id"] for r in data["records"]],
                         ["sess-b", "sess-a"])  # newest first
        for r in data["records"]:
            self.assertTrue(r["digest"])
            self.assertEqual(r["user_turns"], 1)
            self.assertNotIn("messages", r)  # the digest, never the transcript
        resp = await self.client.get("/admin/memory/records?character=zz-nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)

    async def test_forget_preview_then_confirm(self):
        backend = _FakeCurationBackend()
        self.app["deps"].memory = _FakeGlue(backend)
        body = {"character": self.CHAR, "session": "sess-a"}
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json=body)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertFalse(data["forgotten"])
        self.assertIn('"yes": true', data["confirm"])
        self.assertEqual(data["preview"]["session_id"], "sess-a")
        self.assertTrue((self.records_dir / "sess-a.json").is_file())  # untouched
        self.assertEqual(backend.forgot, [])
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json={**body, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertTrue(data["forgotten"])
        self.assertEqual(data["index"], "excised")
        self.assertEqual(backend.forgot, [(self.CHAR, "sess-a")])
        self.assertFalse((self.records_dir / "sess-a.json").exists())
        self.assertTrue((self.records_dir / "sess-b.json").is_file())

    async def test_forget_backend_failure_keeps_record(self):
        backend = _FakeCurationBackend()
        backend.raise_on_forget = True
        self.app["deps"].memory = _FakeGlue(backend)
        resp = await self.client.post(
            "/admin/memory/forget", headers=self.BEARER,
            json={"character": self.CHAR, "session": "sess-a", "yes": True})
        self.assertEqual(resp.status, 502)
        self.assertIn("record kept", (await resp.json())["error"])
        self.assertTrue((self.records_dir / "sess-a.json").is_file())

    async def test_forget_pre_keyed_leftovers_get_the_clean_hint(self):
        backend = _FakeCurationBackend()
        backend.result = False  # facts stored before keyed retain
        self.app["deps"].memory = _FakeGlue(backend)
        resp = await self.client.post(
            "/admin/memory/forget", headers=self.BEARER,
            json={"character": self.CHAR, "session": "sess-a", "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertTrue(data["forgotten"])
        self.assertEqual(data["index"], "leftover-facts")
        self.assertIn("rebuild --clean", data["hint"])
        self.assertFalse((self.records_dir / "sess-a.json").exists())

    async def test_forget_none_backend_and_lane_down(self):
        # Companion mapped "none": record deleted, no index existed.
        self.app["deps"].memory = _FakeGlue(None)
        resp = await self.client.post(
            "/admin/memory/forget", headers=self.BEARER,
            json={"character": self.CHAR, "session": "sess-b", "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(data["index"], "none")
        self.assertFalse((self.records_dir / "sess-b.json").exists())
        # Lane down: preview still answers; the confirm refuses, names the CLI.
        self.app["deps"].memory = None
        body = {"character": self.CHAR, "session": "sess-a"}
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json=body)
        self.assertEqual(resp.status, 200)
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json={**body, "yes": True})
        self.assertEqual(resp.status, 409)
        self.assertIn("hearth.memory forget", (await resp.json())["error"])
        self.assertTrue((self.records_dir / "sess-a.json").is_file())

    async def test_forget_validation(self):
        self.app["deps"].memory = _FakeGlue(_FakeCurationBackend())
        cases = (
            ({"character": "zz-nope", "session": "sess-a"}, 404),
            ({"character": self.CHAR, "session": "../evil"}, 400),
            ({"character": self.CHAR, "session": "no-such"}, 404),
            (None, 400),  # non-JSON body
        )
        for body, want in cases:
            if body is None:
                resp = await self.client.post("/admin/memory/forget",
                                              headers=self.BEARER, data=b"not json")
            else:
                resp = await self.client.post("/admin/memory/forget",
                                              headers=self.BEARER, json=body)
            self.assertEqual(resp.status, want, str(body))
        # Nothing was deleted by any refused call.
        self.assertTrue((self.records_dir / "sess-a.json").is_file())
        self.assertTrue((self.records_dir / "sess-b.json").is_file())


def _test_wav(path: Path, seconds: float = 4.0, rate: int = 24_000) -> None:
    import wave as _wave

    with _wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


class RosterRoutes(AioHTTPTestCase):
    """/admin/roster: exempt static shell, authed state, and the create-only
    preview-then-confirm onboarding transaction against a scratch DATA tree."""

    BEARER = {"Authorization": "Bearer test-bearer"}
    PERSONA = "## IDENTITY\n\nA test companion.\n\n## SOUL\n\nCalm and brief.\n"

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none", session=None, memory=None)
        supervisor.build_mount({"enabled": True,
                                "panel_url": "http://127.0.0.1:1",
                                "compact_watch": False})(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)
        from unittest import mock

        from hearth.config import config_loader

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "memory.toml").write_text(
            "# operator comment that must survive\n"
            "[memory]\nenabled = true\nbackend = \"floor\"\n\n"
            "[memory.companions]\n# a comment inside the table\n",
            encoding="utf-8")
        self._patch = mock.patch.object(config_loader, "_DATA", self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # MEMORY_TOML is import-bound (unlike the call-time _DATA lookups), so
        # point it at the scratch copy too — every consumer reads one path.
        self._mem_patch = mock.patch.object(
            config_loader, "MEMORY_TOML", self.root / "config" / "memory.toml")
        self._mem_patch.start()
        self.addCleanup(self._mem_patch.stop)
        self.wav = self.root / "clip.wav"
        _test_wav(self.wav)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    def _form(self, **over):
        fields = {"name": "zz-roster-test", "persona": self.PERSONA,
                  "voice_tag": "default", "license": "personal-use-only",
                  "source": "recorded by me for the test", "memory_tier": ""}
        fields.update(over)
        fd = aiohttp.FormData()
        for k, v in fields.items():
            if v is not None:
                fd.add_field(k, v)
        if over.get("_sample", True):
            fd.add_field("sample", self.wav.read_bytes(),
                         filename="clip.wav", content_type="audio/wav")
        fields.pop("_sample", None)
        return fd

    async def test_shell_is_unauthed_static_chrome(self):
        resp = await self.client.get("/admin/roster")  # no bearer
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("roster", text.lower())
        self.assertNotIn("test-bearer", text)
        # data + verbs stay behind the door
        self.assertEqual((await self.client.get("/admin/roster/state")).status, 401)
        self.assertEqual((await self.client.post("/admin/roster/onboard")).status, 401)

    async def test_state_lists_roster(self):
        resp = await self.client.get("/admin/roster/state", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        names = [c["name"] for c in data["characters"]]
        self.assertIn("example", names)  # the shipped root is enumerated too
        self.assertTrue(data["memory_enabled"])
        ex = next(c for c in data["characters"] if c["name"] == "example")
        self.assertEqual(ex["memory_backend"], "floor")

    async def test_preview_then_create_full_transaction(self):
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(memory_tier="hindsight"))
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["created"])
        self.assertEqual(data["clip"]["channels"], 1)
        self.assertFalse((self.root / "characters" / "zz-roster-test").exists())

        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(memory_tier="hindsight",
                                                      yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["created"])
        self.assertIn("verified", data["loader"])
        self.assertIn("/admin/launch", data["next"])
        cdir = self.root / "characters" / "zz-roster-test"
        for rel in ("persona.md", "VOICE-SOURCE.md",
                    "voices/default/voice.toml", "voices/default/sample.wav"):
            self.assertTrue((cdir / rel).is_file(), rel)
        # The generated descriptor passes the registry's strict check.
        import tomllib

        from hearth.config import settings_registry as reg
        parsed = tomllib.loads((cdir / "voices/default/voice.toml").read_text())
        errors, _warnings = reg.strict_check("voice", parsed)
        self.assertEqual(errors, [])
        self.assertEqual(parsed["license"], "personal-use-only")
        # Tier entry landed under [memory.companions], comments preserved.
        mem_text = (self.root / "config" / "memory.toml").read_text()
        self.assertIn('zz-roster-test = "hindsight"', mem_text)
        self.assertIn("operator comment that must survive", mem_text)
        self.assertIn("a comment inside the table", mem_text)
        self.assertIn("next bot/facade start", data["memory"])
        # Create-only: the same name is now refused.
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(yes="true"))
        self.assertEqual(resp.status, 400)
        self.assertIn("create-only", json.dumps(await resp.json()))

    async def test_validation_matrix(self):
        cases = (
            ({"name": "bad/name"}, "invalid character name"),
            ({"name": "example"}, "create-only"),        # shipped root protected
            ({"source": ""}, "source attestation"),
            ({"persona": "## IDENTITY\n\nonly half\n"}, "SOUL"),
            ({"memory_tier": "bogus"}, "unknown memory tier"),
        )
        for over, want in cases:
            resp = await self.client.post("/admin/roster/onboard",
                                          headers=self.BEARER,
                                          data=self._form(**over))
            self.assertEqual(resp.status, 400, want)
            self.assertIn(want, json.dumps(await resp.json()))
        self.assertFalse((self.root / "characters").exists())  # nothing written

    async def test_garbage_clip_refused_honestly(self):
        self.wav.write_bytes(b"this is not audio at all")
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(yes="true"))
        self.assertEqual(resp.status, 422)
        # Rolled back: the new character dir is gone (the bare characters/
        # shell from scaffolding may remain — it carries nothing).
        self.assertFalse((self.root / "characters" / "zz-roster-test").exists())

    async def test_short_clip_refused(self):
        _test_wav(self.wav, seconds=1.0)
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form())
        self.assertEqual(resp.status, 422)
        self.assertIn("clone reference wants", json.dumps(await resp.json()))

    async def test_memory_toml_absent_still_onboards(self):
        (self.root / "config" / "memory.toml").unlink()
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(name="zz-roster-nomem",
                                                      memory_tier="floor",
                                                      yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["created"])
        self.assertIn("memory.toml absent", data["memory"])
        self.assertTrue((self.root / "characters" / "zz-roster-nomem"
                         / "persona.md").is_file())

    # ── the editing half: persona editor ─────────────────────────────────────

    NEW_PERSONA = ("## IDENTITY\n\nAn edited test companion.\n\n"
                   "## SOUL\n\nBrighter now.\n")

    async def _create(self, name="zz-roster-test"):
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(name=name, yes="true"))
        self.assertEqual(resp.status, 200, await resp.text())

    async def test_persona_editor_auth_and_validation(self):
        self.assertEqual((await self.client.get(
            "/admin/roster/persona?character=x")).status, 401)
        self.assertEqual((await self.client.post(
            "/admin/roster/persona", json={})).status, 401)
        resp = await self.client.get("/admin/roster/persona?character=zz-nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "example", "text": "## IDENTITY\n\nhalf only\n"})
        self.assertEqual(resp.status, 400)
        self.assertIn("SOUL", json.dumps(await resp.json()))
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "example", "persona": "../evil",
                  "text": self.NEW_PERSONA})
        self.assertEqual(resp.status, 400)

    async def test_persona_edit_data_character_with_backup(self):
        await self._create()
        target = self.root / "characters" / "zz-roster-test" / "persona.md"
        # Read shows the created text; preview writes nothing.
        resp = await self.client.get(
            "/admin/roster/persona?character=zz-roster-test", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(data["text"], self.PERSONA)
        self.assertEqual(data["root"], "data")
        body = {"character": "zz-roster-test", "text": self.NEW_PERSONA}
        resp = await self.client.post("/admin/roster/persona",
                                      headers=self.BEARER, json=body)
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertFalse(data["written"])
        self.assertEqual(target.read_text(), self.PERSONA)  # untouched
        # Confirm: written atomically, one .prev backup, effect time stated.
        resp = await self.client.post("/admin/roster/persona", headers=self.BEARER,
                                      json={**body, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["written"])
        self.assertEqual(target.read_text(), self.NEW_PERSONA)
        prev = target.with_name("persona.md.prev")
        self.assertEqual(prev.read_text(), self.PERSONA)
        self.assertIn("characters/zz-roster-test/persona.md.prev", data["backup"])
        self.assertIn("live-switch", data["effect"])

    async def test_persona_shipped_character_copies_on_write(self):
        # "example" resolves to the shipped root; editing must create a DATA
        # overlay and NEVER touch the shipped file.
        from hearth.config import config_loader

        shipped = config_loader._ROOT / "characters" / "example" / "persona.md"
        shipped_text = shipped.read_text()
        resp = await self.client.get(
            "/admin/roster/persona?character=example", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual(data["root"], "shipped")
        self.assertIn("DATA", data["editable_note"] or "")
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "example", "text": self.NEW_PERSONA, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["written"])
        self.assertIsNone(data["backup"])  # fresh overlay — nothing to back up
        overlay = self.root / "characters" / "example" / "persona.md"
        self.assertEqual(overlay.read_text(), self.NEW_PERSONA)
        self.assertEqual(shipped.read_text(), shipped_text)  # sacred
        # The overlay now shadows: a fresh GET reads it back as root=data.
        resp = await self.client.get(
            "/admin/roster/persona?character=example", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual((data["root"], data["text"]), ("data", self.NEW_PERSONA))

    async def test_persona_new_variant_created_and_listed(self):
        await self._create()
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "zz-roster-test", "persona": "bright",
                  "text": self.NEW_PERSONA, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertIn("variant", data["action"])
        vfile = (self.root / "characters" / "zz-roster-test"
                 / "persona.bright.md")
        self.assertEqual(vfile.read_text(), self.NEW_PERSONA)
        # The switch enumeration picks the variant up at call time.
        resp = await self.client.get("/admin/roster/state", headers=self.BEARER)
        entry = next(c for c in (await resp.json())["characters"]
                     if c["name"] == "zz-roster-test")
        self.assertIn("bright", entry["personas"])

    # ── the editing half: add-a-voice ────────────────────────────────────────

    def _voice_form(self, **over):
        fields = {"character": "zz-roster-test", "voice_tag": "second",
                  "license": "personal-use-only",
                  "source": "recorded by me for the test"}
        fields.update(over)
        fd = aiohttp.FormData()
        for k, v in fields.items():
            if v is not None:
                fd.add_field(k, v)
        if over.get("_sample", True):
            fd.add_field("sample", self.wav.read_bytes(),
                         filename="clip.wav", content_type="audio/wav")
        return fd

    async def test_add_voice_validation(self):
        self.assertEqual((await self.client.post("/admin/roster/voice")).status, 401)
        await self._create()
        cases = (
            ({"character": "zz-nope"}, "unknown character"),
            ({"voice_tag": "bad/tag"}, "invalid voice tag"),
            ({"voice_tag": "default"}, "already exists"),  # per-tag create-only
            ({"source": ""}, "source attestation"),
        )
        for over, want in cases:
            resp = await self.client.post("/admin/roster/voice",
                                          headers=self.BEARER,
                                          data=self._voice_form(**over))
            self.assertEqual(resp.status, 400, want)
            self.assertIn(want, json.dumps(await resp.json()))

    async def test_add_voice_preview_then_create_appends_provenance(self):
        await self._create()
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form())
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertFalse(data["created"])
        vdir = (self.root / "characters" / "zz-roster-test" / "voices" / "second")
        self.assertFalse(vdir.exists())  # preview persisted nothing
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form(yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["created"])
        self.assertIn("verified", data["loader"])
        self.assertIn("switch pickers", data["next"])
        self.assertTrue((vdir / "voice.toml").is_file())
        self.assertTrue((vdir / "sample.wav").is_file())
        # Provenance appended to the character's ONE record, both tags present.
        vs = (self.root / "characters" / "zz-roster-test" / "VOICE-SOURCE.md")
        text = vs.read_text()
        self.assertIn("default", text)
        self.assertIn("## second — added", text)
        # And the enumeration sees the new tag immediately.
        resp = await self.client.get("/admin/roster/state", headers=self.BEARER)
        entry = next(c for c in (await resp.json())["characters"]
                     if c["name"] == "zz-roster-test")
        self.assertEqual(sorted(entry["voices"]), ["default", "second"])
        # Create-only per tag: a repeat is refused with the bundle intact.
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form(yes="true"))
        self.assertEqual(resp.status, 400)
        self.assertTrue((vdir / "voice.toml").is_file())

    async def test_add_voice_to_shipped_character_lands_in_data(self):
        # example's persona lives in the shipped root; the new bundle must land
        # under DATA (voice_dir's per-voice lookup), shipped tree untouched.
        resp = await self.client.post(
            "/admin/roster/voice", headers=self.BEARER,
            data=self._voice_form(character="example", voice_tag="zz-extra",
                                  yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        vdir = self.root / "characters" / "example" / "voices" / "zz-extra"
        self.assertTrue((vdir / "voice.toml").is_file())
        # A DATA-side provenance record was started for the new clip.
        self.assertTrue((self.root / "characters" / "example"
                         / "VOICE-SOURCE.md").is_file())
        from hearth.config import config_loader
        self.assertFalse((config_loader._ROOT / "characters" / "example"
                          / "voices" / "zz-extra").exists())

    async def test_add_voice_garbage_clip_rolls_back_voice_dir_only(self):
        await self._create()
        self.wav.write_bytes(b"this is not audio at all")
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form(yes="true"))
        self.assertEqual(resp.status, 422)
        cdir = self.root / "characters" / "zz-roster-test"
        self.assertFalse((cdir / "voices" / "second").exists())  # rolled back
        self.assertTrue((cdir / "persona.md").is_file())  # character untouched
        self.assertTrue((cdir / "voices" / "default" / "voice.toml").is_file())


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


class CompactRoute(AioHTTPTestCase):
    """POST /admin/compact — the :65001 manual-initiation knob."""

    BEARER = {"Authorization": "Bearer test-bearer"}

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer", cfg={}, lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none", session=None)
        supervisor.build_mount({"enabled": True,
                                "panel_url": "http://127.0.0.1:1",
                                "compact_watch": False})(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from unittest import mock
        from hearth.config import config_loader
        from hearth.session import session_store
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for p in (mock.patch.object(config_loader, "DATA_DIR", self.root),
                  mock.patch.object(routes_mod.switch_mod, "choices",
                                    lambda: {"characters": [{"name": "example"}]}),
                  mock.patch.object(session_store, "companion_sessions_dir",
                                    lambda c=None: self.root / "sessions")):
            p.start()
            self.addCleanup(p.stop)
        (self.root / "sessions").mkdir(parents=True)
        (self.root / "sessions" / "long-run.json").write_text("{}")
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def test_validation(self):
        r = await self.client.post("/admin/compact", headers=self.BEARER,
                                   json={"character": "nobody", "session": "long-run"})
        self.assertEqual(r.status, 404)
        r = await self.client.post("/admin/compact", headers=self.BEARER,
                                   json={"character": "example", "session": "gone"})
        self.assertEqual(r.status, 404)
        r = await self.client.post("/admin/compact", headers=self.BEARER, json={})
        self.assertEqual(r.status, 400)
        r = await self.client.post("/admin/compact")
        self.assertEqual(r.status, 401)  # bearer door

    async def test_ok_queues(self):
        r = await self.client.post("/admin/compact", headers=self.BEARER,
                                   json={"character": "example",
                                         "session": "long-run.json"})
        data = await r.json()
        self.assertEqual(r.status, 200, data)
        self.assertTrue(data["ok"])
        self.assertIn("queued", data["note"])  # no compactor installed here
        qfile = self.root / "ops" / "compact-queue" / "example.long-run.request"
        self.assertTrue(qfile.exists())
        self.assertEqual(json.loads(qfile.read_text())["source"], "manual")


class MaintenanceStartGuard(AioHTTPTestCase):
    """/admin/bot/start refuses 409 while a compaction lock is held, and
    /admin/state lists held maintenance locks."""

    BEARER = {"Authorization": "Bearer test-bearer"}

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer", cfg={}, lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none", session=None)
        supervisor.build_mount({"enabled": True,
                                "panel_url": "http://127.0.0.1:1",
                                "compact_watch": False})(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from unittest import mock
        from hearth.config import config_loader
        from hearth.session import maintenance_lock
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.object(config_loader, "DATA_DIR",
                                  Path(self._tmp.name))
        patch.start()
        self.addCleanup(patch.stop)
        maintenance_lock._HELD.clear()
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)

    async def asyncTearDown(self):
        from hearth.session import maintenance_lock
        for c in list(maintenance_lock._HELD):
            maintenance_lock.drop(c)
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def test_start_409_while_compacting_then_ok(self):
        from hearth.session import maintenance_lock
        maintenance_lock.hold("example", op="compact", session="long-run")
        resp = await self.client.post("/admin/bot/start", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual(resp.status, 409)
        self.assertIn("compaction of long-run", data["error"])
        self.assertIn("try again in a few minutes", data["error"])
        # state surfaces it too (names only)
        st = await (await self.client.get("/admin/state",
                                          headers=self.BEARER)).json()
        self.assertEqual(st["maintenance"][0]["character"], "example")
        self.assertEqual(st["maintenance"][0]["op"], "compact")
        maintenance_lock.drop("example")
        resp = await self.client.post("/admin/bot/start", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())

    async def test_session_lock_does_not_block_start(self):
        from hearth.session import maintenance_lock
        # An op=session lock (an adopted bot's own) must not 409 the door —
        # the child's double-start refusal and the bot's own acquire govern.
        maintenance_lock.hold("example", op="session")
        resp = await self.client.post("/admin/bot/start", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())


class SettingsRoutes(AioHTTPTestCase):
    """/admin/settings: the generated settings surface (schema-driven step 2)
    against a fully synthetic install — scratch DATA *and* engine roots, so
    discovery never sweeps the real trees and no live file content can reach
    a payload assertion."""

    BEARER = {"Authorization": "Bearer test-bearer"}
    MEMORY_TOML = (
        "# operator header comment that must survive\n"
        "[memory]\n"
        "enabled = true\n"
        'backend = "floor"\n'
        "recall_limit = 6  # recalled items\n"
        "\n"
        "[memory.hindsight]\n"
        'llm_api_key = "zz-secret-value"\n'
        "\n"
        "[memory.hindsight.env]\n"
        'HS_FLAG = "zz-env-secret"\n'
        "\n"
        "[memory.companions]\n"
        "# a comment inside the table\n"
        'zz-a = "floor"\n'
    )

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none", session=None, memory=None)
        supervisor.build_mount({"enabled": True,
                                "panel_url": "http://127.0.0.1:1",
                                "compact_watch": False})(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)
        from unittest import mock

        from hearth.config import config_loader

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.data = base / "data"
        self.eng = base / "engine"
        (self.data / "config" / "models" / "zz-m").mkdir(parents=True)
        (self.eng / "config").mkdir(parents=True)
        (self.data / "config" / "memory.toml").write_text(
            self.MEMORY_TOML, encoding="utf-8")
        (self.data / "config" / "active.toml").write_text(
            'character = "zz-a"\nmodel = "zz-m"\nvoice = "zz-v"\n',
            encoding="utf-8")
        (self.data / "config" / "overrides.toml").write_text(
            "[tts]\ntemperature = 0.8\n", encoding="utf-8")
        (self.data / "config" / "models" / "zz-m" / "model.toml").write_text(
            'id = "zz-model"\ntemperature = 0.7\nreasoning_effort = "none"\n',
            encoding="utf-8")
        # A SHIPPED file with no data-root copy — the copy-on-write target.
        (self.eng / "config" / "vad.toml").write_text(
            "# shipped calibration\n[live]\nconfidence = 0.7\n",
            encoding="utf-8")
        cfg = self.data / "config"
        for name, value in (("_DATA", self.data), ("DATA_DIR", self.data),
                            ("_ROOT", self.eng), ("CONFIG_DIR", cfg),
                            ("ACTIVE_TOML", cfg / "active.toml"),
                            ("MEMORY_TOML", cfg / "memory.toml"),
                            ("SERVE_TOML", cfg / "serve.toml"),
                            ("OPENCLAW_TOML", cfg / "openclaw.toml")):
            p = mock.patch.object(config_loader, name, value)
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def _set(self, file, key, value, yes=False):
        body = {"file": file, "key": key, "value": value}
        if yes:
            body["yes"] = True
        return await self.client.post("/admin/settings/set", json=body,
                                      headers=self.BEARER)

    async def test_shell_unauthed_and_routes_authed(self):
        resp = await self.client.get("/admin/settings/ui")  # no bearer
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertNotIn("test-bearer", text)
        self.assertNotIn("zz-", text)  # pure chrome — no install facts baked in
        for path in ("/admin/settings", "/admin/settings/schema",
                     "/admin/settings/file?file=x"):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 401, path)
        resp = await self.client.post("/admin/settings/set", json={})
        self.assertEqual(resp.status, 401)

    async def test_overview_and_schema(self):
        resp = await self.client.get("/admin/settings", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        kinds = {k["kind"]: k for k in (await resp.json())["kinds"]}
        self.assertEqual(kinds.keys(), sr.REGISTRY.keys())  # total coverage
        mem = kinds["memory"]
        self.assertTrue(mem["writable"])
        self.assertEqual(mem["files"][0]["file"], "DATA/config/memory.toml")
        self.assertIn(mem["files"][0]["verdict"], ("ok", "warn"))
        self.assertFalse(kinds["active"]["writable"])
        self.assertIn("/admin/switch", kinds["active"]["pointer"])
        self.assertIn("65000", kinds["overrides"]["pointer"])
        self.assertEqual(kinds["serve"]["files"], [])  # absent → no instances

        resp = await self.client.get("/admin/settings/schema", headers=self.BEARER)
        schema = (await resp.json())["schema"]
        props = schema["memory"]["schema"]["$defs"]["_MemHindsight"]["properties"]
        self.assertTrue(props["llm_api_key"]["x-hearth"]["secret"])
        self.assertEqual(
            schema["memory"]["schema"]["properties"]["enabled"]["x-hearth"]["effect"],
            "bot+facade")

    async def test_file_values_redact_secrets(self):
        resp = await self.client.get(
            "/admin/settings/file?file=DATA/config/memory.toml",
            headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        raw = await resp.text()
        self.assertNotIn("zz-secret-value", raw)   # the whole payload is clean
        self.assertNotIn("zz-env-secret", raw)
        d = json.loads(raw)
        self.assertTrue(d["values"]["enabled"])
        self.assertEqual(d["values"]["hindsight"]["llm_api_key"], "•••")
        self.assertEqual(d["values"]["hindsight"]["env"],
                         {"HS_FLAG": "•••"})  # keys stay visible
        resp = await self.client.get("/admin/settings/file?file=nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)

    async def test_set_preview_then_confirm_preserves_comments(self):
        import tomllib

        path = self.data / "config" / "memory.toml"
        label = "DATA/config/memory.toml"
        before = path.read_text(encoding="utf-8")
        resp = await self._set(label, "recall_limit", 4)
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertFalse(d["written"])
        self.assertEqual((d["old"], d["new"]), (6, 4))
        self.assertEqual(d["effect"]["effect"], "bot+facade")
        self.assertEqual(before, path.read_text(encoding="utf-8"))  # preview wrote nothing

        resp = await self._set(label, "recall_limit", 4, yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertTrue(d["written"])
        text = path.read_text(encoding="utf-8")
        self.assertIn("# operator header comment that must survive", text)
        self.assertIn("# recalled items", text)      # the trailing comment too
        parsed = tomllib.loads(text)["memory"]
        self.assertEqual(parsed["recall_limit"], 4)
        self.assertEqual(parsed["backend"], "floor")  # neighbors untouched
        prev = path.with_name(path.name + ".prev")
        self.assertEqual(prev.read_text(encoding="utf-8"), before)

    async def test_set_new_key_and_map_entry(self):
        import tomllib

        label = "DATA/config/memory.toml"
        # A key whose sub-table has no section header yet: appended fresh.
        resp = await self._set(label, "per_turn.enabled", True, yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        # A new entry in an existing map table: inserted under its header.
        resp = await self._set(label, "companions.zz-new", "hindsight", yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        text = (self.data / "config" / "memory.toml").read_text(encoding="utf-8")
        self.assertIn("# a comment inside the table", text)
        parsed = tomllib.loads(text)["memory"]
        self.assertTrue(parsed["per_turn"]["enabled"])
        self.assertEqual(parsed["companions"],
                         {"zz-a": "floor", "zz-new": "hindsight"})

    async def test_set_refusals_are_honest_pointers(self):
        label = "DATA/config/memory.toml"
        resp = await self._set("DATA/config/active.toml", "character", "zz-b")
        self.assertEqual(resp.status, 409)
        self.assertIn("/admin/switch", (await resp.json())["error"])
        resp = await self._set("DATA/config/overrides.toml", "tts.temperature", 0.9)
        self.assertEqual(resp.status, 409)
        self.assertIn("panel", (await resp.json())["error"])
        resp = await self._set(label, "hindsight.llm_api_key", "x")
        self.assertEqual(resp.status, 409)
        self.assertIn("secret", (await resp.json())["error"])
        resp = await self._set(label, "hindsight", "x")     # a table, not a key
        self.assertEqual(resp.status, 400)
        resp = await self._set(label, "companions", "x")    # a map needs an entry
        self.assertEqual(resp.status, 400)
        resp = await self._set(label, "no_such_key", "x")
        self.assertEqual(resp.status, 404)
        resp = await self._set(label, "recall_limit", "hi")  # type refused
        self.assertEqual(resp.status, 422)
        resp = await self._set("DATA/config/models/zz-m/model.toml",
                               "temperature", 9.9)           # range refused
        self.assertEqual(resp.status, 422)
        self.assertTrue((await resp.json())["detail"])
        resp = await self.client.post("/admin/settings/set",
                                      json={"file": label, "key": "enabled"},
                                      headers=self.BEARER)
        self.assertEqual(resp.status, 400)                   # value required

    async def test_shipped_file_copies_on_write_into_data(self):
        import tomllib

        shipped = self.eng / "config" / "vad.toml"
        before = shipped.read_text(encoding="utf-8")
        resp = await self._set("ROOT/config/vad.toml", "live.confidence", 0.5,
                               yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertIn("data root", d["target"])
        self.assertEqual(before, shipped.read_text(encoding="utf-8"))  # untouched
        copy = self.data / "config" / "vad.toml"
        self.assertIn("# shipped calibration", copy.read_text(encoding="utf-8"))
        self.assertEqual(
            tomllib.loads(copy.read_text(encoding="utf-8"))["live"]["confidence"],
            0.5)
        # The shipped label now answers with a pointer at the shadowing copy.
        resp = await self._set("ROOT/config/vad.toml", "live.confidence", 0.6,
                               yes=True)
        self.assertEqual(resp.status, 409)
        self.assertIn("shadows", (await resp.json())["error"])


if __name__ == "__main__":
    unittest.main()
