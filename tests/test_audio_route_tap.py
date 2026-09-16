"""`GET /route` — the sitting's own account of where its audio is.

A drop-in on the companion's panel, in the same shape as `/presence`: the bot
imports the module, the import registers the route, and `build_pipeline` hands
over the live transport's reporter. The facade fetches it for `/admin/state`.

What is worth pinning is mostly what it REFUSES to do. A status route that can
take the panel down with it is worse than one that occasionally says less than
it knows, so a reporter that raises, or answers something that is not a route,
is answered with the resting shape rather than a 500. And the object must carry
names and numbers only — no address, no key, no audio — because this is the one
route on the box meant to be read from outside the page.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth.control.control_routes import PanelContext
from hearth.control.features import audio_route

RESTING = {"kind": "desk", "device": None, "state": "pinned", "path": None,
           "buffer_ms": None, "shed_ms": 0, "grace_left": None}
REMOTE = {"kind": "remote", "device": "pixel", "state": "connected",
          "path": "direct", "buffer_ms": 120, "shed_ms": 120, "grace_left": None}
LOST = {"kind": "remote", "device": "pixel", "state": "lost", "path": "direct",
        "buffer_ms": 120, "shed_ms": 0, "grace_left": 160}


def _ctx() -> PanelContext:
    return PanelContext(worker=None, context=None, mute_gate=None,
                        speaking_tap=None, meter=None, engine_info={},
                        recorder=None)


class TheSnapshot(unittest.TestCase):

    def tearDown(self):
        audio_route.attach(None)

    def test_before_anything_is_attached_the_shape_is_still_the_shape(self):
        audio_route.attach(None)
        self.assertEqual(audio_route.snapshot(), RESTING)

    def test_an_attached_reporter_is_what_is_reported(self):
        audio_route.attach(lambda: dict(REMOTE))
        self.assertEqual(audio_route.snapshot(), REMOTE)

    def test_a_reporter_that_raises_never_breaks_the_panel(self):
        def _boom():
            raise RuntimeError("the transport is mid-teardown")

        audio_route.attach(_boom)
        self.assertEqual(audio_route.snapshot(), RESTING)

    def test_a_reporter_that_answers_nonsense_is_answered_with_the_shape(self):
        for nonsense in (None, [], "connected", 7):
            with self.subTest(nonsense=nonsense):
                audio_route.attach(lambda: nonsense)
                self.assertEqual(audio_route.snapshot(), RESTING)

    def test_a_partial_report_fills_in_rather_than_dropping_keys(self):
        audio_route.attach(lambda: {"kind": "remote", "device": "pixel"})
        self.assertEqual(audio_route.snapshot(),
                         {"kind": "remote", "device": "pixel", "state": "pinned",
                          "path": None, "buffer_ms": None, "shed_ms": 0,
                          "grace_left": None})

    def test_the_countdown_rides_the_object_and_is_null_unless_it_is_running(self):
        """The one field the Stop card cannot work out for itself: only the
        sitting knows how long it has been waiting, and for how long more."""
        audio_route.attach(lambda: dict(LOST))
        self.assertEqual(audio_route.snapshot()["grace_left"], 160)
        audio_route.attach(lambda: dict(REMOTE))
        self.assertIsNone(audio_route.snapshot()["grace_left"],
                          "a device that is here is not being waited for")
        audio_route.attach(None)
        self.assertIsNone(audio_route.snapshot()["grace_left"],
                          "and the desk never counts down at all")

    def test_a_reporter_cannot_smuggle_extra_fields_onto_the_object(self):
        """The route object is read from outside this process; it says the
        seven things it says and nothing a future caller quietly adds."""
        audio_route.attach(lambda: dict(REMOTE, address="100.64.0.2",
                                        token="a-key"))
        self.assertEqual(set(audio_route.snapshot()), set(RESTING))
        self.assertEqual(len(RESTING), 7, "seven keys, and a device can add none")


class TheRoute(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        app = web.Application()
        app.add_routes(audio_route.audio_route_routes(_ctx()))
        return app

    async def asyncTearDown(self):
        audio_route.attach(None)
        await super().asyncTearDown()

    async def test_the_route_answers_the_object(self):
        audio_route.attach(lambda: dict(REMOTE))
        resp = await self.client.get("/route")
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.json(), REMOTE)

    async def test_the_route_answers_even_with_nothing_attached(self):
        audio_route.attach(None)
        resp = await self.client.get("/route")
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.json(), RESTING)


if __name__ == "__main__":
    unittest.main()
