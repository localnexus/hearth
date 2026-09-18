"""The route word's journey through the supervisor: the door, the argv, the
state.

Three seams, one word. `POST /admin/bot/start` (and the launch page's own
`POST /admin/switch`) validate it before anything is spawned; `BotChild.start`
turns it into `--audio <route>` and records it beside the other switches so the
Stop card can state it; `/admin/state` carries what the live sitting reports
about it as `bot.route`.

The validation is tested at the DOOR rather than only in the grammar module
because the failure that matters is a bad word reaching a process argument —
and the door is the last place a person's typing can be turned away with a
sentence instead of a dead child.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth.audio import devices as devices_mod
from hearth.supervisor import child as child_mod
from hearth.supervisor.routes import lifecycle, state


def use_a_scratch_registry(case, *device_ids: str) -> Path:
    """Point the device registry at a temporary file and enrol the given ids.

    The start door now refuses a route naming a device nobody paired, so every
    case that starts one has to say which devices exist. It says so here rather
    than by patching the check away, because the check IS half of what these
    cases are about.
    """
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / "devices.toml"
    patch = mock.patch.object(devices_mod, "devices_toml", lambda: path)
    patch.start()
    case.addCleanup(patch.stop)
    devices_mod.forget_cache()
    for device_id in device_ids:
        devices_mod.enrol(device_id, device_id)
    return path


class _FakeChild:
    def __init__(self):
        self.calls = []
        self.state = "down"

    async def start(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "pid": 7, "mode": kwargs.get("mode")}

    async def reconcile(self):
        return self.state in ("starting", "running")

    def status(self):
        return {"state": self.state, "pid": 7, "managed": True, "uptime_s": 1.0,
                "last_exit": None, "switches": {"recall": None, "retain": None,
                                                "keep_name": None, "route": None}}


class TheStartDoorChecksTheRoute(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        app = web.Application()
        app["bot_child"] = self.child
        app.router.add_post("/admin/bot/start", lifecycle._bot_start)
        return app

    async def asyncSetUp(self):
        self.child = _FakeChild()
        patch = mock.patch.object(lifecycle.maintenance_lock, "busy_locks",
                                  return_value=[])
        patch.start()
        self.addCleanup(patch.stop)
        self.registry = use_a_scratch_registry(self, "pixel", "Pixel-9.pro_2")
        await super().asyncSetUp()

    async def test_both_good_shapes_pass_through_untouched(self):
        for word in ("desk", "remote:pixel", "remote:Pixel-9.pro_2"):
            with self.subTest(word=word):
                resp = await self.client.post("/admin/bot/start",
                                              json={"route": word})
                self.assertEqual(resp.status, 200)
                self.assertEqual(self.child.calls[-1]["route"], word)

    async def test_a_bad_route_is_400_and_nothing_is_spawned(self):
        for word in ("remote:", "remote:pix el", "remote:../etc", "loud",
                     "remote:" + "x" * 65, "--muted", 7, True, ["desk"]):
            with self.subTest(word=word):
                self.child.calls.clear()
                resp = await self.client.post("/admin/bot/start",
                                              json={"route": word})
                self.assertEqual(resp.status, 400)
                body = await resp.json()
                self.assertFalse(body["ok"])
                self.assertIn("remote:<device-id>", body["error"])
                self.assertEqual(self.child.calls, [],
                                 "a refused route must never reach the child")

    async def test_a_device_nobody_paired_is_refused_before_anything_spawns(self):
        """A typo in a device name used to start a conversation that waited for
        ever for something that cannot exist. It is a sentence now."""
        resp = await self.client.post("/admin/bot/start",
                                      json={"route": "remote:nobody"})
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("no paired device nobody", body["error"])
        self.assertIn("/admin/pair/ui", body["error"])
        self.assertEqual(self.child.calls, [])

    async def test_a_start_on_a_paired_device_stamps_last_seen(self):
        before = devices_mod.get("pixel", self.registry).last_seen
        resp = await self.client.post("/admin/bot/start",
                                      json={"route": "remote:pixel"})
        self.assertEqual(resp.status, 200)
        row = devices_mod.get("pixel", self.registry)
        self.assertGreaterEqual(row.last_seen, before)
        self.assertEqual(row.paired_at, before, "paired_at is not a heartbeat")

    async def test_a_desk_start_touches_no_device_at_all(self):
        before = self.registry.read_text(encoding="utf-8")
        resp = await self.client.post("/admin/bot/start", json={"route": "desk"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.registry.read_text(encoding="utf-8"), before)

    async def test_no_route_at_all_stays_the_absence_it_was(self):
        """The door must not invent 'desk': absence means the child decides,
        and the child's own default is the desk. One default, not two."""
        resp = await self.client.post("/admin/bot/start", json={})
        self.assertEqual(resp.status, 200)
        self.assertIsNone(self.child.calls[-1]["route"])


class TheChildTurnsTheWordIntoArgv(unittest.IsolatedAsyncioTestCase):

    def _child(self):
        child = child_mod.BotChild(argv=["/usr/bin/true"])
        # start() sweeps for an already-running companion first (adopt, don't
        # collide). Nothing is running in a test, and the sweep is not what
        # these cases are about.
        async def _none(_self):
            return []

        patch = mock.patch.object(child_mod.BotChild, "probe", _none)
        patch.start()
        self.addCleanup(patch.stop)
        return child

    async def _argv_for(self, **kwargs):
        seen = {}

        async def _spawn(*argv, **spawn_kwargs):
            seen["argv"] = list(argv)
            raise OSError("not really spawning")

        child = self._child()
        with mock.patch.object(child_mod.asyncio, "create_subprocess_exec", _spawn):
            await child.start(**kwargs)
        return seen.get("argv", [])

    async def test_a_remote_route_becomes_two_arguments(self):
        argv = await self._argv_for(route="remote:pixel")
        self.assertIn("--audio", argv)
        self.assertEqual(argv[argv.index("--audio") + 1], "remote:pixel")

    async def test_the_desk_is_passed_explicitly_when_it_is_asked_for(self):
        argv = await self._argv_for(route="desk")
        self.assertEqual(argv[argv.index("--audio") + 1], "desk")

    async def test_no_route_means_no_flag_at_all(self):
        self.assertNotIn("--audio", await self._argv_for())

    async def test_the_child_refuses_a_bad_word_rather_than_spawning_it(self):
        child = self._child()
        spawned = []

        async def _spawn(*argv, **kwargs):
            spawned.append(argv)
            raise OSError("should never be reached")

        with mock.patch.object(child_mod.asyncio, "create_subprocess_exec", _spawn):
            result = await child.start(route="remote:pix el")
        self.assertFalse(result["ok"])
        self.assertIn("remote:<device-id>", result["error"])
        self.assertEqual(spawned, [])

    async def test_the_route_is_recorded_beside_the_other_switches(self):
        child = self._child()

        class _Proc:
            pid = 4242

            async def wait(self):
                return 0

        with mock.patch.object(child_mod.asyncio, "create_subprocess_exec",
                               return_value=_Proc()):
            await child.start(route="remote:pixel", recall=False, retain=True)
        self.assertEqual(child.status()["switches"],
                         {"recall": False, "retain": True, "keep_name": None,
                          "route": "remote:pixel"})
        child.close()


class TheStateCarriesWhatTheSittingReports(unittest.IsolatedAsyncioTestCase):
    """`bot.route` is FETCHED from the bot's own `/route`, not mirrored: the
    transport that knows the answer lives in that process, and a phase-A change
    should not stand up a second mirror to carry seven fields."""

    class _Session:
        def __init__(self, answer=None, status=200, boom=None):
            self.answer, self.status, self.boom = answer, status, boom
            self.asked = []

        def get(self, url, **kwargs):
            self.asked.append(url)
            session = self

            class _Ctx:
                async def __aenter__(self_inner):
                    if session.boom:
                        raise session.boom
                    return self_inner

                async def __aexit__(self_inner, *exc):
                    return False

                @property
                def status(self_inner):
                    return session.status

                async def json(self_inner):
                    return session.answer

            return _Ctx()

    async def test_a_live_route_is_read_from_the_bots_own_door(self):
        route = {"kind": "remote", "device": "pixel", "state": "connected",
                 "path": "relayed", "buffer_ms": 200, "shed_ms": 40}
        session = self._Session(answer=route)
        got = await state._route_of(session, "http://127.0.0.1:65000")
        self.assertEqual(got, route)
        self.assertEqual(session.asked, ["http://127.0.0.1:65000/route"])

    async def test_anything_but_a_route_object_answers_none(self):
        import aiohttp

        cases = {
            "no probe session": (None, "http://x"),
            "an older companion (404)": (self._Session(status=404), "http://x"),
            "a body that is not a route": (self._Session(answer={"ok": True}),
                                           "http://x"),
            "a body that is not an object": (self._Session(answer=[1, 2]),
                                             "http://x"),
            "an unreachable companion": (
                self._Session(boom=aiohttp.ClientError("down")), "http://x"),
            "no panel address": (self._Session(answer={"kind": "desk"}), ""),
        }
        for why, (session, url) in cases.items():
            with self.subTest(why=why):
                self.assertIsNone(await state._route_of(session, url))


if __name__ == "__main__":
    unittest.main()
