"""The remote route's transport: the fence, the shape, and the gate on a real
socket.

Three things worth a test and one of them is the reason this file exists at
all:

**The pyaudio fence.** A remote sitting must never initialise PortAudio — the
desk transport's recovery is a gate over PROCESS-level state, and a sitting
with no local device putting that state in play is the sharpest implementation
hazard in the whole change (Norma, role 4). Remembering is not a mechanism, so
this makes `import pyaudio` RAISE and then builds the remote transport through
the same factory `bot.py` calls.

**The shape.** The rates, the raw-PCM choice and the absent session timeout are
what make this transport carry Hearth's audio untouched; they are parameters,
so they can be read back rather than believed.

**The gate, end to end, over a real loopback WebSocket.** The refusal cases are
the whole security story of the route, and a mock of a socket would prove
nothing about a socket. `tailscale status` is the one thing stubbed — a canned
document, so the path classification is deterministic and no subprocess runs.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from unittest import mock

import websockets

from hearth.audio import remote_path, remote_transport
from hearth.audio.remote_output import MIN_BACKLOG_MS, OutputBacklog

DESK_MODULES = ("hearth.audio.pa_pool", "hearth.audio.device_pin",
                "hearth.audio.recovering_transport", "pyaudio")

# One direct peer, so the hello answer is deterministic and no subprocess runs.
STATUS = {"Peer": {"k": {"TailscaleIPs": ["100.64.0.2"],
                         "CurAddr": "203.0.113.9:1312"}}}


class _ExplodingModule(types.ModuleType):
    """Any attribute access raises — a stand-in for a pyaudio that is not
    there, or that must not be reached."""

    def __getattr__(self, name):
        raise ImportError("pyaudio must never be imported on the remote route")


class ThePyaudioFence(unittest.TestCase):

    def test_the_remote_transport_builds_with_pyaudio_poisoned(self):
        saved = {name: sys.modules.get(name) for name in DESK_MODULES}
        for name in DESK_MODULES:
            sys.modules.pop(name, None)
        sys.modules["pyaudio"] = _ExplodingModule("pyaudio")
        try:
            transport = remote_transport.build_remote_transport("pixel", token="k")
            self.assertIsNotNone(transport.input())
            self.assertIsNotNone(transport.output())
            for name in ("hearth.audio.pa_pool", "hearth.audio.device_pin",
                         "hearth.audio.recovering_transport"):
                with self.subTest(module=name):
                    self.assertNotIn(name, sys.modules,
                                     f"the remote route imported {name}")
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_the_module_names_no_desk_module_anywhere_in_its_source(self):
        """Belt and braces on the import above: a lazy import inside a function
        would not show up in sys.modules until it ran."""
        from pathlib import Path

        import hearth.audio.remote_transport as module
        import hearth.audio.remote_output as output_module

        for part in (module, output_module):
            source = Path(part.__file__).read_text(encoding="utf-8")
            body = "\n".join(line for line in source.splitlines()
                             if not line.lstrip().startswith("#"))
            # The docstring names them on purpose (it is the rule); code must not.
            code = body.split('"""', 2)[-1]
            for name in ("pa_pool", "device_pin", "recovering_transport", "pyaudio"):
                with self.subTest(part=part.__name__, name=name):
                    self.assertNotIn(name, code)


class TheTransportShape(unittest.TestCase):

    def test_hearths_rates_ride_untouched_and_the_wire_carries_raw_pcm(self):
        params = remote_transport.build_remote_transport("pixel", token="k")._params
        self.assertEqual(params.audio_in_sample_rate, 16000)
        self.assertEqual(params.audio_out_sample_rate, 24000)
        self.assertIsNone(params.serializer, "raw PCM: no serializer")
        self.assertFalse(params.add_wav_header, "raw PCM: no WAV header")
        self.assertIsNone(params.session_timeout, "the grace window is phase B")
        self.assertTrue(params.audio_in_enabled and params.audio_out_enabled)

    def test_the_socket_binds_to_loopback_only(self):
        transport = remote_transport.build_remote_transport("pixel", token="k")
        self.assertEqual(transport._host, "127.0.0.1")
        self.assertEqual(transport._port, 65021)

    def test_the_port_and_the_origins_follow_the_environment(self):
        with mock.patch.dict("os.environ", {"HEARTH_AUDIO_WS_PORT": "65099"}):
            self.assertEqual(remote_transport.ws_port(), 65099)
        for bad in ("", "soon", "0", "70000"):
            with mock.patch.dict("os.environ", {"HEARTH_AUDIO_WS_PORT": bad}):
                self.assertEqual(remote_transport.ws_port(), 65021)
        with mock.patch.dict("os.environ",
                             {"HEARTH_AUDIO_ORIGINS": "https://a:1, http://b:2 "}):
            self.assertEqual(remote_transport.allowed_origins(),
                             ["https://a:1", "http://b:2"])

    def test_no_key_means_no_sitting_rather_than_an_ungated_socket(self):
        with mock.patch.object(remote_path, "read_serve_token", return_value=None):
            with self.assertRaises(remote_transport.MissingServeToken):
                remote_transport.build_remote_transport("pixel")

    def test_the_route_object_starts_waiting_and_never_carries_the_key(self):
        secret = "a-key-nobody-should-see"
        transport = remote_transport.build_remote_transport("pixel", token=secret)
        self.assertEqual(transport.route_state(),
                         {"kind": "remote", "device": "pixel", "state": "waiting",
                          "path": None, "buffer_ms": None, "shed_ms": 0})
        self.assertNotIn(secret, json.dumps(transport.route_state()))


class TheOutboundQueue(unittest.TestCase):
    """The 2026-09-15 finding, as a rule: a queue that only absorbs turns one
    slow moment into a conversation running behind, without bound."""

    def _backlog(self, depth_ms=MIN_BACKLOG_MS):
        # 24 kHz mono int16 → 48 bytes per millisecond.
        return OutputBacklog(24000, 1, depth_ms=depth_ms)

    def test_a_queue_under_its_ceiling_sheds_nothing(self):
        backlog = self._backlog()
        for _ in range(10):
            self.assertEqual(backlog.push(b"\x00" * 48 * 10), 0.0)  # 10 ms each
        self.assertEqual(backlog.shed_ms, 0.0)
        self.assertAlmostEqual(backlog.queued_ms, 100.0)

    def test_past_the_ceiling_the_OLDEST_audio_goes_and_is_counted(self):
        backlog = self._backlog(depth_ms=100)
        for i in range(10):
            backlog.push(bytes([i + 1]) * 48 * 10)      # 10 ms each, marked
        self.assertEqual(backlog.shed_ms, 0.0)
        shed = backlog.push(bytes([99]) * 48 * 10)      # one over
        self.assertAlmostEqual(shed, 10.0)
        self.assertAlmostEqual(backlog.shed_ms, 10.0)
        self.assertAlmostEqual(backlog.queued_ms, 100.0)
        self.assertEqual(backlog.pop()[0], 2, "the oldest chunk should be gone")

    def test_the_ceiling_follows_the_far_ends_own_depth_with_a_floor(self):
        backlog = self._backlog()
        backlog.set_depth_for(60)     # a direct device
        self.assertEqual(backlog.depth_ms, 200.0, "never below the 200 ms floor")
        backlog.set_depth_for(200)    # a relayed one
        self.assertEqual(backlog.depth_ms, 400.0)

    def test_a_barge_in_empties_the_queue_without_calling_it_a_stall(self):
        backlog = self._backlog()
        backlog.push(b"\x00" * 48 * 50)
        backlog.clear()
        self.assertEqual(backlog.queued_ms, 0.0)
        self.assertEqual(backlog.shed_ms, 0.0, "a deliberate flush is not a stall")

    def test_an_empty_push_is_a_no_op_and_an_empty_pop_answers_none(self):
        backlog = self._backlog()
        self.assertEqual(backlog.push(b""), 0.0)
        self.assertIsNone(backlog.pop())


class TheStallLine(unittest.TestCase):

    def test_at_most_one_line_per_five_seconds_however_long_the_stall_runs(self):
        transport = remote_transport.build_remote_transport("pixel", token="k")
        output = transport.output()
        output._backlog = OutputBacklog(24000, 1, depth_ms=100)
        for _ in range(11):
            output._backlog.push(b"\x00" * 48 * 10)
        with mock.patch.object(remote_transport, "logger") as _unused, \
             mock.patch("hearth.audio.remote_output.logger") as log:
            for _ in range(20):
                output._backlog.push(b"\x00" * 48 * 10)
                output._maybe_say_stalled()
            self.assertEqual(log.warning.call_count, 1)
            line = log.warning.call_args[0][0]
            self.assertEqual(line, "[audio] remote output stalled ({} ms shed)")
            output._last_stall_log -= 10.0     # five seconds later
            output._maybe_say_stalled()
            self.assertEqual(log.warning.call_count, 2)


class TheHelloGateOnARealSocket(unittest.IsolatedAsyncioTestCase):
    """The refusal cases are the whole security story of this route, so they
    are exercised against a real WebSocket rather than a stand-in for one."""

    async def asyncSetUp(self):
        patch = mock.patch.object(remote_path, "tailscale_status", return_value=STATUS)
        patch.start()
        self.addCleanup(patch.stop)
        self.transport = remote_transport.build_remote_transport("pixel", token="key")
        self.input = self.transport.input()
        self.transport.output()          # the parent needs its other half
        self.pushed = []

        async def _push(frame):
            self.pushed.append(frame)

        self.input.push_audio_frame = _push
        self.input.push_frame = lambda *a, **k: asyncio.sleep(0)
        self.server = await websockets.serve(
            self.input._client_handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.addAsyncCleanup(self._close_server)

    async def _close_server(self):
        self.server.close()
        await self.server.wait_closed()

    async def _hello(self, socket, device="pixel", token="key"):
        await socket.send(json.dumps({"hello": {"device": device, "token": token}}))

    async def test_a_good_hello_is_answered_and_then_audio_flows(self):
        async with websockets.connect(
                self.url, additional_headers={"X-Forwarded-For": "100.64.0.2"}) as ws:
            await self._hello(ws)
            answer = json.loads(await ws.recv())
            self.assertEqual(answer, {"ok": True, "path": "direct", "buffer_ms": 60})
            await ws.send(b"\x01\x02" * 160)
            await asyncio.sleep(0.2)
            self.assertEqual(len(self.pushed), 1)
            self.assertEqual(self.pushed[0].sample_rate, 16000)
            self.assertEqual(self.transport.route_state(),
                             {"kind": "remote", "device": "pixel",
                              "state": "connected", "path": "direct",
                              "buffer_ms": 60, "shed_ms": 0})
        await asyncio.sleep(0.2)
        self.assertEqual(self.transport.route_state()["state"], "lost")

    async def test_a_relayed_peer_is_told_the_deeper_buffer(self):
        relayed = {"Peer": {"k": {"TailscaleIPs": ["100.64.0.3"], "CurAddr": ""}}}
        with mock.patch.object(remote_path, "tailscale_status", return_value=relayed):
            async with websockets.connect(
                    self.url,
                    additional_headers={"X-Forwarded-For": "100.64.0.3"}) as ws:
                await self._hello(ws)
                answer = json.loads(await ws.recv())
        self.assertEqual(answer, {"ok": True, "path": "relayed", "buffer_ms": 200})

    async def _closed_on_connect(self):
        """Connect and read nothing — pipecat closes a second device before it
        can say anything at all, which is the rule we are keeping."""
        try:
            async with websockets.connect(self.url) as extra:
                await extra.recv()
        except websockets.ConnectionClosed as closed:
            return closed.rcvd.code if closed.rcvd else closed.code
        return None

    async def _refused(self, first_frame):
        async with websockets.connect(self.url) as ws:
            await ws.send(first_frame)
            with self.assertRaises(websockets.ConnectionClosed) as caught:
                await ws.recv()
        return caught.exception

    async def test_a_wrong_key_a_wrong_device_and_audio_first_are_all_4401(self):
        cases = {
            "a wrong key": json.dumps({"hello": {"device": "pixel", "token": "no"}}),
            "a wrong device": json.dumps({"hello": {"device": "laptop",
                                                    "token": "key"}}),
            "no hello wrapper": json.dumps({"device": "pixel", "token": "key"}),
            "not json at all": "hello?",
            "audio before the hello": b"\x00\x01" * 160,
        }
        for why, frame in cases.items():
            with self.subTest(why=why):
                closed = await self._refused(frame)
                self.assertEqual(closed.rcvd.code, 4401)
                self.assertEqual(closed.rcvd.reason, "refused")
                self.assertEqual(self.pushed, [], "audio reached the pipeline")
                self.assertEqual(self.transport.route_state()["state"], "waiting",
                                 "a refusal must leave the sitting waiting")

    async def test_the_companions_voice_goes_out_as_raw_pcm(self):
        """The direction the parameters alone cannot prove. With no serializer,
        pipecat's own write path sends nothing at all — both halves of this
        transport speak PCM themselves, and only a real socket shows it."""
        from pipecat.frames.frames import OutputAudioRawFrame

        output = self.transport.output()
        voice = bytes(range(256)) * 4
        async with websockets.connect(
                self.url, additional_headers={"X-Forwarded-For": "100.64.0.2"}) as ws:
            await self._hello(ws)
            await ws.recv()                      # the hello answer
            drain = asyncio.create_task(output._drain())
            self.addCleanup(drain.cancel)
            sent = await output.write_audio_frame(
                OutputAudioRawFrame(audio=voice, sample_rate=24000, num_channels=1))
            self.assertTrue(sent)
            heard = await asyncio.wait_for(ws.recv(), timeout=2)
        self.assertEqual(heard, voice, "the bytes on the wire are the audio itself")
        self.assertEqual(self.transport.route_state()["shed_ms"], 0)

    async def test_a_barge_in_clears_the_queue_and_tells_the_far_end(self):
        """Raw PCM carries no frame types, so the one control the device needs
        — stop playing what you have — goes as a message of its own."""
        from pipecat.frames.frames import InterruptionFrame

        output = self.transport.output()
        async with websockets.connect(
                self.url, additional_headers={"X-Forwarded-For": "100.64.0.2"}) as ws:
            await self._hello(ws)
            await ws.recv()
            output.backlog.push(b"\x00" * 4800)
            await output._write_frame(InterruptionFrame())
            told = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        self.assertEqual(told, {"interrupt": True})
        self.assertEqual(output.backlog.queued_ms, 0.0)
        self.assertEqual(output.backlog.shed_ms, 0.0,
                         "a deliberate flush is not a stall")

    async def test_a_second_device_is_refused_while_one_is_connected(self):
        async with websockets.connect(
                self.url, additional_headers={"X-Forwarded-For": "100.64.0.2"}) as first:
            await self._hello(first)
            await first.recv()
            self.assertEqual(await self._closed_on_connect(), 1013)
            # and the one already talking still has the socket
            await first.send(b"\x03\x04" * 160)
            await asyncio.sleep(0.2)
            self.assertEqual(len(self.pushed), 1)
            self.assertEqual(self.transport.route_state()["state"], "connected")


if __name__ == "__main__":
    unittest.main()
