"""The paired-device registry, through the doors: enrol, list, forget.

Three doors touch the registry and each one has a promise worth pinning:

  POST /admin/pair/claim   a correct code ENROLS, and answers the id the device
                           is now known by. The refusal paths do not change in
                           any way — same body, same status, and no row.
  GET  /admin/devices      the list, behind the access key like every other
                           /admin JSON route; the same list rides /admin/state.
  POST /admin/devices/forget  preview, then a confirmed press. An unknown id is
                           404; the device a live conversation is speaking on
                           is 409, because forgetting it would leave that
                           conversation running on a device nothing can name.

Every case points the registry at a temporary file, so nothing here reads or
writes the operator's data folder.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth import supervisor
from hearth.audio import devices as devices_mod


class _FakeChild:
    """A child that is down until a test says otherwise."""

    pid = None

    def __init__(self):
        self.state = "down"
        self.route = None

    async def reconcile(self):
        return self.state in ("starting", "running")

    async def start(self, **kwargs):
        return {"ok": True, "pid": 7}

    async def stop(self, **kwargs):
        return {"ok": True}

    def close(self):
        pass

    def status(self):
        return {"state": self.state, "pid": 7, "managed": True, "uptime_s": 1.0,
                "last_exit": None,
                "switches": {"recall": None, "retain": None, "keep_name": None,
                             "route": self.route}}


class _DoorCase(AioHTTPTestCase):
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
        supervisor.build_mount({"enabled": True, "panel_url": "http://127.0.0.1:1",
                                "compact_watch": False})(app)

        async def _open(app_):
            app_["deps"].session = aiohttp.ClientSession()

        async def _close(app_):
            await app_["deps"].session.close()

        app.on_startup.append(_open)
        app.on_cleanup.append(_close)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app["bot_child"].close()
        self.child = _FakeChild()
        self.app["bot_child"] = self.child
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.registry = self.dir / "devices.toml"
        patch = mock.patch.object(devices_mod, "devices_toml",
                                  lambda: self.registry)
        patch.start()
        self.addCleanup(patch.stop)
        devices_mod.forget_cache()

    async def mint(self) -> str:
        resp = await self.client.post("/admin/pair", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        return (await resp.json())["code"]

    async def claim(self, **body):
        return await self.client.post("/admin/pair/claim", json=body)


# ── the claim ────────────────────────────────────────────────────────────────

class TheClaimEnrols(_DoorCase):

    async def test_a_correct_code_writes_a_row_and_answers_its_id(self):
        code = await self.mint()
        resp = await self.claim(code=code, label="Pixel")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["token"], "test-bearer")
        self.assertRegex(body["device_id"], r"^pixel-[0-9a-f]{4}$")
        self.assertEqual(body["label"], "Pixel")
        rows = devices_mod.load(self.registry)
        self.assertEqual([d.id for d in rows], [body["device_id"]])
        self.assertEqual(rows[0].paired_at, rows[0].last_seen)

    async def test_a_re_pair_that_says_who_it_is_refreshes_one_row(self):
        code = await self.mint()
        first = await (await self.claim(code=code, label="Pixel")).json()
        code = await self.mint()
        again = await (await self.claim(code=code, label="the hallway Pixel",
                                        device_id=first["device_id"])).json()
        self.assertEqual(again["device_id"], first["device_id"])
        rows = devices_mod.load(self.registry)
        self.assertEqual(len(rows), 1, "a re-pair is not a second row")
        self.assertEqual(rows[0].label, "the hallway Pixel")

    async def test_a_device_paired_before_the_registry_keeps_its_id(self):
        """The migration: the stored name goes up as `device_id` and becomes
        the row's id, so `remote:pixel` keeps working."""
        code = await self.mint()
        body = await (await self.claim(code=code, label="Pixel",
                                       device_id="pixel")).json()
        self.assertEqual(body["device_id"], "pixel")
        self.assertEqual([d.id for d in devices_mod.load(self.registry)], ["pixel"])

    async def test_a_device_id_that_is_not_an_id_is_ignored(self):
        code = await self.mint()
        body = await (await self.claim(code=code, label="Pixel",
                                       device_id="pix el")).json()
        self.assertRegex(body["device_id"], r"^pixel-[0-9a-f]{4}$")

    async def test_no_label_at_all_still_enrols(self):
        code = await self.mint()
        body = await (await self.claim(code=code)).json()
        self.assertEqual(body["label"], "device")
        self.assertRegex(body["device_id"], r"^device-[0-9a-f]{4}$")

    async def test_a_refused_claim_answers_exactly_what_it_always_did(self):
        code = await self.mint()
        wrong = "%06d" % ((int(code) + 1) % 1000000)
        resp = await self.claim(code=wrong, label="Pixel", device_id="pixel")
        self.assertEqual(resp.status, 401)
        self.assertEqual(await resp.json(), {"error": "that code was refused"})
        self.assertFalse(self.registry.exists(),
                         "a refusal must not touch the registry")

    async def test_a_body_that_is_not_an_object_is_just_a_bad_claim(self):
        await self.mint()
        resp = await self.client.post("/admin/pair/claim", json=[1, 2])
        self.assertEqual(resp.status, 401)
        self.assertEqual(await resp.json(), {"error": "that code was refused"})

    async def test_a_registry_that_cannot_be_written_still_hands_over_the_key(self):
        """Pairing must not fail because a file could not be written: the key
        is what the device came for."""
        code = await self.mint()
        logged: list[str] = []
        from loguru import logger

        sink = logger.add(lambda m: logged.append(str(m)))
        try:
            def _boom(*args, **kwargs):
                raise OSError("read-only")

            with mock.patch.object(devices_mod, "save", _boom):
                resp = await self.claim(code=code, label="Pixel")
        finally:
            logger.remove(sink)
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["token"], "test-bearer")
        self.assertIsNone(body["device_id"])
        self.assertTrue(any("device registry write failed (OSError)" in line
                            for line in logged), logged)

    async def test_the_log_line_is_the_id_and_nothing_else(self):
        """The line carries the id, never the label as it was typed. (A minted
        id does contain a SLUG of the label — that is what makes it readable —
        but the person's own words, spacing and capitals never land in a log
        line, and a device that named its own id contributes nothing at all.)"""
        code = await self.mint()
        logged: list[str] = []
        from loguru import logger

        sink = logger.add(lambda m: logged.append(str(m)))
        try:
            body = await (await self.claim(code=code, label="Pixel XL")).json()
        finally:
            logger.remove(sink)
        paired = [line for line in logged if "device paired" in line]
        self.assertEqual(len(paired), 1, logged)
        self.assertTrue(
            paired[0].rstrip().endswith(f"[supervisor] device paired ({body['device_id']})"),
            paired[0])
        self.assertNotIn("Pixel XL", paired[0])


# ── reading the list ─────────────────────────────────────────────────────────

class TheListIsBehindTheDoor(_DoorCase):

    async def test_it_needs_the_access_key(self):
        self.assertEqual((await self.client.get("/admin/devices")).status, 401)

    async def test_it_answers_every_row_with_its_four_fields(self):
        devices_mod.enrol("Pixel", "pixel", path=self.registry)
        devices_mod.enrol("iPad", "ipad", path=self.registry)
        resp = await self.client.get("/admin/devices", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        rows = (await resp.json())["devices"]
        self.assertEqual([r["id"] for r in rows], ["pixel", "ipad"])
        self.assertEqual(sorted(rows[0]),
                         ["id", "label", "last_seen", "paired_at"])

    async def test_no_devices_at_all_is_an_empty_list_not_an_error(self):
        resp = await self.client.get("/admin/devices", headers=self.BEARER)
        self.assertEqual((await resp.json())["devices"], [])

    async def test_the_state_poll_carries_the_same_list(self):
        devices_mod.enrol("Pixel", "pixel", path=self.registry)
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual([r["id"] for r in data["devices"]], ["pixel"])
        self.assertEqual(data["devices"][0]["label"], "Pixel")


class TheDoorTheLaunchPageActuallyPresses(_DoorCase):
    """Start rides POST /admin/switch, not /admin/bot/start. The paired-device
    check has to be on BOTH or it is missing from the one a person uses."""

    async def test_a_device_nobody_paired_is_refused_there_too(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"start": True, "route": "remote:nobody"})
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("no paired device nobody", body["errors"][0])
        self.assertIn("/admin/pair/ui", body["errors"][0])

    async def test_a_route_that_is_not_even_a_route_is_still_refused_first(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"start": True, "route": "remote:pix el"})
        self.assertEqual(resp.status, 400)
        self.assertIn("remote:<device-id>", (await resp.json())["errors"][0])


# ── forget ───────────────────────────────────────────────────────────────────

class Forgetting(_DoorCase):

    async def _two(self):
        devices_mod.enrol("Pixel", "pixel", path=self.registry)
        devices_mod.enrol("iPad", "ipad", path=self.registry)

    async def forget(self, **body):
        return await self.client.post("/admin/devices/forget", json=body,
                                      headers=self.BEARER)

    async def test_it_needs_the_access_key(self):
        resp = await self.client.post("/admin/devices/forget", json={"id": "pixel"})
        self.assertEqual(resp.status, 401)

    async def test_without_a_yes_it_only_says_what_it_would_do(self):
        await self._two()
        resp = await self.forget(id="pixel")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["forgotten"])
        self.assertEqual(body["device"]["label"], "Pixel")
        self.assertTrue(body["archives"].startswith(str(self.registry)))
        self.assertIn(".prev-", body["archives"])
        self.assertIn("Confirm", body["confirm"])
        self.assertEqual(len(devices_mod.load(self.registry)), 2,
                         "a preview changes nothing")

    async def test_a_confirmed_press_archives_and_removes(self):
        await self._two()
        before = self.registry.read_text(encoding="utf-8")
        resp = await self.forget(id="pixel", yes=True)
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["forgotten"])
        self.assertEqual([d.id for d in devices_mod.load(self.registry)], ["ipad"])
        archived = Path(body["archived"])
        self.assertTrue(archived.exists())
        self.assertEqual(archived.read_text(encoding="utf-8"), before)

    async def test_an_id_nobody_paired_is_404(self):
        await self._two()
        resp = await self.forget(id="nobody", yes=True)
        self.assertEqual(resp.status, 404)
        self.assertIn("no paired device nobody", (await resp.json())["error"])

    async def test_a_body_with_no_id_is_400(self):
        resp = await self.forget()
        self.assertEqual(resp.status, 400)

    async def test_the_device_a_live_conversation_is_on_is_409(self):
        await self._two()
        self.child.state = "running"
        self.child.route = "remote:pixel"
        resp = await self.forget(id="pixel", yes=True)
        self.assertEqual(resp.status, 409)
        body = await resp.json()
        self.assertEqual(body["route"], "remote:pixel")
        self.assertIn("Pixel", body["error"])
        self.assertIn("stop it first", body["error"])
        self.assertEqual(len(devices_mod.load(self.registry)), 2)

    async def test_another_device_can_be_forgotten_while_one_is_in_use(self):
        await self._two()
        self.child.state = "running"
        self.child.route = "remote:pixel"
        resp = await self.forget(id="ipad", yes=True)
        self.assertEqual(resp.status, 200)
        self.assertEqual([d.id for d in devices_mod.load(self.registry)], ["pixel"])

    async def test_a_desk_conversation_holds_nothing(self):
        await self._two()
        self.child.state = "running"
        self.child.route = "desk"
        self.assertEqual((await self.forget(id="pixel", yes=True)).status, 200)


if __name__ == "__main__":
    unittest.main()
