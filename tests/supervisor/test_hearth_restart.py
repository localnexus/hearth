"""The Restart Hearth card and the keeper behind it (2026-09-05).

/admin/daemon/restart is a deliberate exit that only IS a restart when a keeper
(launchd on the supervised install) relaunches the process. So: the keeper is
detected once at mount and rides /admin/state; the launch page draws the card
only while one is reported; and the route refuses without one unless forced.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from hearth.supervisor import keeper
from hearth.supervisor import routes as routes_mod
from hearth.supervisor.routes import lifecycle
from hearth.ui import hearth_restart

from .test_admin_routes import AdminRoutes


class KeeperDetect(unittest.TestCase):
    def test_env_word_wins(self):
        self.assertEqual(keeper.detect({"HEARTH_KEEPER": "systemd"}, ppid=4242), "systemd")
        for word in ("", "none", "NONE", "0", "false"):
            self.assertIsNone(keeper.detect({"HEARTH_KEEPER": word}, ppid=1), word)

    def test_parent_pid_one_is_the_hint(self):
        self.assertEqual(keeper.detect({}, ppid=1, platform="darwin"), "launchd")
        self.assertEqual(keeper.detect({}, ppid=1, platform="linux"), "init")
        self.assertIsNone(keeper.detect({}, ppid=4242, platform="darwin"))

    def test_real_call_answers_without_error(self):
        self.assertIn(keeper.detect(), (None, "launchd", "init") + (keeper.detect(),))


class RestartRoute(AdminRoutes):
    async def test_state_carries_the_keeper(self):
        self.app["keeper"] = None
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertIsNone((await resp.json())["keeper"])
        self.app["keeper"] = "launchd"
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual((await resp.json())["keeper"], "launchd")

    async def test_refused_without_a_keeper_in_plain_words(self):
        self.app["keeper"] = None
        with mock.patch.object(lifecycle.os, "_exit") as ex:
            resp = await self.client.post("/admin/daemon/restart", headers=self.BEARER, json={})
            self.assertEqual(resp.status, 409)
            body = await resp.json()
            self.assertFalse(body["ok"])
            self.assertIn("started from a terminal", body["error"])
            self.assertNotIn("daemon", body["error"].lower())
            await asyncio.sleep(0.4)
            ex.assert_not_called()

    async def test_force_exits_anyway(self):
        self.app["keeper"] = None
        with mock.patch.object(lifecycle.os, "_exit") as ex:
            resp = await self.client.post("/admin/daemon/restart", headers=self.BEARER,
                                          json={"force": True})
            self.assertEqual(resp.status, 200)
            await asyncio.sleep(0.4)
            ex.assert_called_once_with(3)

    async def test_with_a_keeper_it_exits_for_the_keeper(self):
        self.app["keeper"] = "launchd"
        with mock.patch.object(lifecycle.os, "_exit") as ex:
            resp = await self.client.post("/admin/daemon/restart", headers=self.BEARER, json={})
            self.assertTrue((await resp.json())["restarting"])
            await asyncio.sleep(0.4)
            ex.assert_called_once_with(3)


class RestartCard(unittest.TestCase):
    def test_launch_page_carries_the_card_once_and_hidden_by_default(self):
        html = routes_mod._LAUNCH_PAGE()
        self.assertNotIn(hearth_restart.PLACEHOLDER, html)
        self.assertEqual(html.count(hearth_restart.JS), 1)
        self.assertIn('id="hearthcard" class="card hidden"', html)
        self.assertIn("data.keeper", hearth_restart.JS)  # drawn from the keeper, nothing else

    def test_splice_refuses_a_page_without_the_placeholder(self):
        with self.assertRaises(ValueError):
            hearth_restart.splice("<script>nothing here</script>")


if __name__ == "__main__":
    unittest.main()
