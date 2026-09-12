"""test_start_door.py — characterization of the bot start/stop door.

Pins what /admin/bot/start and /admin/bot/stop (routes/lifecycle.py's
_bot_start / _bot_stop) DO today, not what they should do — including the
current blindness of the start-door guard (it consults only a compaction
maintenance lock, nothing else). A later, deliberate widening of that guard
is expected to flip test_start_door_consults_only_compact_locks_today; every
other test here should keep passing.

Uses a fake bot_child (async start/stop that record kwargs and return what
the test dictates) and patches maintenance_lock in the lifecycle module's own
namespace — no real child process, no real lock file, no real config tree.

Run:  .venv/bin/python -m unittest tests.test_start_door
"""

from __future__ import annotations

import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth.supervisor.routes import lifecycle


class _FakeBotChild:
    """Records start/stop calls; returns whatever the test set up."""

    def __init__(self):
        self.start_calls: list[dict] = []
        self.stop_calls: list[dict] = []
        self.start_result: dict = {"ok": True, "pid": 1}
        self.stop_result: dict = {"ok": True}

    async def start(self, mode="new", name=None, memory=None):
        self.start_calls.append({"mode": mode, "name": name, "memory": memory})
        return self.start_result

    async def stop(self, hold=False, name=None):
        self.stop_calls.append({"hold": hold, "name": name})
        return self.stop_result


class StartDoor(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        app["bot_child"] = self.child
        app.router.add_post("/admin/bot/start", lifecycle._bot_start)
        app.router.add_post("/admin/bot/stop", lifecycle._bot_stop)
        return app

    async def asyncSetUp(self):
        self.child = _FakeBotChild()
        await super().asyncSetUp()

    async def test_start_door_refuses_while_a_compaction_lock_is_held(self):
        held = [{"character": "testchar", "op": "compact", "session": "s-1",
                 "started": "2026-09-01T00:00:00"}]
        with mock.patch.object(lifecycle.maintenance_lock, "held_locks",
                               return_value=held) as held_locks:
            resp = await self.client.post("/admin/bot/start", json={})
        self.assertEqual(resp.status, 409)
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertIn("testchar", data["error"])
        self.assertEqual(data["maintenance"], held)
        held_locks.assert_called_once_with(op="compact")
        self.assertEqual(self.child.start_calls, [])

    async def test_start_door_consults_only_compact_locks_today(self):
        """The one row that exists to be broken on purpose later: today the
        start door asks maintenance_lock.held_locks exactly once, with
        op="compact", and nothing else — no port probe, no other lock op —
        before handing off to bot_child.start."""
        with mock.patch.object(lifecycle.maintenance_lock, "held_locks",
                               return_value=[]) as held_locks:
            resp = await self.client.post("/admin/bot/start", json={})
        self.assertEqual(resp.status, 200)
        held_locks.assert_called_once_with(op="compact")
        self.assertEqual(self.child.start_calls, [{"mode": "new", "name": None, "memory": None}])

    async def test_start_door_defaults_on_an_empty_body(self):
        with mock.patch.object(lifecycle.maintenance_lock, "held_locks", return_value=[]):
            resp = await self.client.post("/admin/bot/start", json={})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.child.start_calls, [{"mode": "new", "name": None, "memory": None}])

    async def test_start_door_defaults_on_a_non_json_body(self):
        with mock.patch.object(lifecycle.maintenance_lock, "held_locks", return_value=[]):
            resp = await self.client.post("/admin/bot/start", data=b"not json",
                                          headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.child.start_calls, [{"mode": "new", "name": None, "memory": None}])

    async def test_start_door_passes_the_three_fields_through_as_strings(self):
        with mock.patch.object(lifecycle.maintenance_lock, "held_locks", return_value=[]):
            resp = await self.client.post(
                "/admin/bot/start",
                json={"mode": "resume", "name": "x", "memory": "recall-only"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.child.start_calls,
                         [{"mode": "resume", "name": "x", "memory": "recall-only"}])

    async def test_start_door_status_follows_the_childs_ok_true(self):
        self.child.start_result = {"ok": True, "pid": 99}
        with mock.patch.object(lifecycle.maintenance_lock, "held_locks", return_value=[]):
            resp = await self.client.post("/admin/bot/start", json={})
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.json(), {"ok": True, "pid": 99})

    async def test_start_door_status_follows_the_childs_ok_false(self):
        self.child.start_result = {"ok": False, "error": "the companion is starting — wait a moment"}
        with mock.patch.object(lifecycle.maintenance_lock, "held_locks", return_value=[]):
            resp = await self.client.post("/admin/bot/start", json={})
        self.assertEqual(resp.status, 409)
        self.assertEqual(await resp.json(), self.child.start_result)

    async def test_stop_door_passes_hold_and_name_through_and_is_200_on_ok(self):
        self.child.stop_result = {"ok": True, "escalated": False, "held": True}
        resp = await self.client.post("/admin/bot/stop", json={"hold": True, "name": "keepme"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.child.stop_calls, [{"hold": True, "name": "keepme"}])
        self.assertEqual(await resp.json(), self.child.stop_result)

    async def test_stop_door_coerces_hold_and_defaults_name_to_none(self):
        self.child.stop_result = {"ok": True, "escalated": False, "held": False}
        resp = await self.client.post("/admin/bot/stop", json={})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.child.stop_calls, [{"hold": False, "name": None}])

    async def test_stop_door_is_500_on_ok_false(self):
        self.child.stop_result = {"ok": False, "error": "the companion did not stop — see logs/bot.log"}
        resp = await self.client.post("/admin/bot/stop", json={"hold": False})
        self.assertEqual(resp.status, 500)
        self.assertEqual(await resp.json(), self.child.stop_result)


if __name__ == "__main__":
    unittest.main()
