"""Supervisor — the /admin routes.

State, start/stop, and the switch action behind the facade's bearer middleware:
what each route answers, what it refuses, and which shells are unauthed.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
from hearth import supervisor
from hearth.supervisor import actuators as actuators_mod
from hearth.supervisor.child import BotChild


GRACEFUL = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
    "while True: time.sleep(0.1)\n"
)


_PY = sys.executable


def _fake(src: str, **kw) -> BotChild:
    kw.setdefault("pattern", _NOMATCH)
    kw.setdefault("stop_grace_s", 5.0)
    kw.setdefault("term_grace_s", 1.0)
    return BotChild(argv=[_PY, "-c", src], **kw)


_NOMATCH = "zz-hearth-test-nomatch-zz"


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

if __name__ == "__main__":
    unittest.main()
