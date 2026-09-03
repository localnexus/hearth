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
                                        "panel_url": "http://127.0.0.1:1"})
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
            (await self.client.post("/admin/memory/forget", json={})).status, 401)

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
                                "panel_url": "http://127.0.0.1:1"})(app)
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


if __name__ == "__main__":
    unittest.main()
