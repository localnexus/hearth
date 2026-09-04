"""Auto-compaction — /admin/compact and the start-door guard.

The compact route's answers, and the maintenance lock that holds the start door
shut while a compaction is running.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
from hearth import supervisor
from hearth.supervisor import routes as routes_mod
from hearth.supervisor.child import BotChild


GRACEFUL = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
    "while True: time.sleep(0.1)\n"
)


def _fake(src: str, **kw) -> BotChild:
    kw.setdefault("pattern", _NOMATCH)
    kw.setdefault("stop_grace_s", 5.0)
    kw.setdefault("term_grace_s", 1.0)
    return BotChild(argv=[_PY, "-c", src], **kw)


_NOMATCH = "zz-hearth-test-nomatch-zz"


_PY = sys.executable


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
        from hearth.supervisor import switch as switch_mod
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for p in (mock.patch.object(config_loader, "DATA_DIR", self.root),
                  mock.patch.object(switch_mod, "choices",
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

if __name__ == "__main__":
    unittest.main()
