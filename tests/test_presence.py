"""test_presence.py — the read-only presence route (GET /presence) and its tap.

Proves, on the real pipecat FrameProcessor machinery (pipecat.tests.utils.run_test):

  1. passivity — every frame the tap sees comes out the other side, the same
     object (the measure-tap contract);
  2. the flag follows the VAD's own frames, and a session boundary resets it;
  3. the route composes the three booleans and applies the mute guard: a mute
     pressed mid-sentence starves the VAD of its Stopped frame, so the route,
     not the tap, is what keeps a muted mic from reading as a speaking one;
  4. before attach, the route still answers (user_speaking false), so a reader
     that polls early sees a shape, not a 503.

Run:  .venv/bin/python -m unittest tests.test_presence
"""

from __future__ import annotations

import asyncio
import types
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pipecat.frames.frames import (
    EndFrame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import run_test

from hearth.control.features import presence
from hearth.control.features.presence import PresenceTap


def _ctx(muted: bool = False, speaking: bool = False):
    return types.SimpleNamespace(
        mute_gate=types.SimpleNamespace(is_muted=muted),
        speaking_tap=types.SimpleNamespace(is_speaking=speaking))


class PresenceTapTests(unittest.TestCase):

    def setUp(self):
        presence.attach(None)

    def test_every_frame_passes_through_untouched(self):
        tap = PresenceTap()
        audio = InputAudioRawFrame(audio=b"\0\0" * 160, sample_rate=16000, num_channels=1)
        started = VADUserStartedSpeakingFrame()
        down, _up = asyncio.run(run_test(tap, frames_to_send=[audio, started]))
        self.assertTrue(any(f is audio for f in down))
        self.assertTrue(any(f is started for f in down))

    def test_flag_follows_the_vad_frames(self):
        tap = PresenceTap()
        tap.observe(VADUserStartedSpeakingFrame())
        self.assertTrue(tap.user_speaking)
        tap.observe(InputAudioRawFrame(audio=b"\0\0", sample_rate=16000, num_channels=1))
        self.assertTrue(tap.user_speaking)  # unrelated frames leave it alone
        tap.observe(VADUserStoppedSpeakingFrame())
        self.assertFalse(tap.user_speaking)

    def test_session_end_resets_a_stale_true(self):
        tap = PresenceTap()
        tap.observe(VADUserStartedSpeakingFrame())
        tap.observe(EndFrame())
        self.assertFalse(tap.user_speaking)

    def test_the_real_frame_path_runs_observe(self):
        """Through the pipecat machinery: the harness ends with an EndFrame,
        which resets the flag, so the proof is the passthrough plus a spy."""
        seen = []
        tap = PresenceTap()
        orig = tap.observe
        tap.observe = lambda f: (seen.append(type(f).__name__), orig(f))
        started = VADUserStartedSpeakingFrame()
        asyncio.run(run_test(tap, frames_to_send=[started]))
        self.assertIn("VADUserStartedSpeakingFrame", seen)
        self.assertIn("EndFrame", seen)
        self.assertFalse(tap.user_speaking)


class PresenceRouteTests(unittest.TestCase):

    def setUp(self):
        presence.attach(None)

    def _get(self, ctx) -> dict:
        async def go():
            app = web.Application()
            app.add_routes(presence.presence_routes(ctx))
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/presence")
                self.assertEqual(resp.status, 200)
                return await resp.json()
        return asyncio.run(go())

    def test_another_origin_may_read_it(self):
        async def go():
            app = web.Application()
            app.add_routes(presence.presence_routes(_ctx()))
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/presence")
                return resp.headers.get("Access-Control-Allow-Origin")
        self.assertEqual(asyncio.run(go()), "*")

    def test_shape_before_attach(self):
        body = self._get(_ctx())
        self.assertEqual(set(body), {"bot_speaking", "user_speaking", "muted", "ts"})
        self.assertFalse(body["user_speaking"])
        self.assertIsInstance(body["ts"], float)

    def test_composes_the_three_flags(self):
        tap = PresenceTap(); tap._user_speaking = True
        presence.attach(tap)
        body = self._get(_ctx(muted=False, speaking=True))
        self.assertEqual((body["bot_speaking"], body["user_speaking"], body["muted"]),
                         (True, True, False))

    def test_muted_mic_never_reads_as_speaking(self):
        tap = PresenceTap(); tap._user_speaking = True  # the stuck-true case
        presence.attach(tap)
        body = self._get(_ctx(muted=True))
        self.assertTrue(body["muted"])
        self.assertFalse(body["user_speaking"])


if __name__ == "__main__":
    unittest.main()
