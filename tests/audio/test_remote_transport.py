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

**The wait, end to end.** A device that goes away is waited for, a device that
comes back inside the window rejoins a conversation that was never torn down,
and a window that runs out closes the conversation by taking the Stop button's
own path. The clock is injected and the stop is handed in, so three minutes of
waiting costs milliseconds and the close can be asserted rather than performed.

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

from hearth.audio import remote_grace, remote_path, remote_transport
from hearth.audio import route as audio_route
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
        self.assertIsNone(params.session_timeout, "the wait is ours, not pipecat's")
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
                          "path": None, "buffer_ms": None, "shed_ms": 0,
                          "grace_left": None})
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
            self.assertEqual(answer, {"ok": True, "path": "direct",
                                      "buffer_ms": 120, "grace_s": 180})
            await ws.send(b"\x01\x02" * 160)
            await asyncio.sleep(0.2)
            self.assertEqual(len(self.pushed), 1)
            self.assertEqual(self.pushed[0].sample_rate, 16000)
            self.assertEqual(self.transport.route_state(),
                             {"kind": "remote", "device": "pixel",
                              "state": "connected", "path": "direct",
                              "buffer_ms": 120, "shed_ms": 0,
                              "grace_left": None})
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
        self.assertEqual(answer, {"ok": True, "path": "relayed",
                                  "buffer_ms": 200, "grace_s": 180})

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


class _Clock:
    """A clock that only moves when a test says so."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


class TheWaitForADeviceThatWentAway(unittest.IsolatedAsyncioTestCase):
    """Loss, return, and the end of the wait — on the same real socket as the
    gate above, because a device going away IS a socket closing and a mock of
    one would prove nothing about it.

    Two things are handed in rather than performed: the clock (so a
    three-minute window costs milliseconds) and the self-stop (so the close is
    a recorded call rather than a signal at the test runner's own process).
    """

    async def asyncSetUp(self):
        patch = mock.patch.object(remote_path, "tailscale_status",
                                  return_value=STATUS)
        patch.start()
        self.addCleanup(patch.stop)
        audio_route.clear_device_gone()
        self.addCleanup(audio_route.clear_device_gone)
        self.clock = _Clock()
        self.stops = []
        self.transport = remote_transport.build_remote_transport(
            "pixel", token="key", clock=self.clock,
            stop_sitting=lambda: self.stops.append("stop"))
        self.addCleanup(self.transport.note_shutdown)   # no task outlives a case
        self.input = self.transport.input()
        self.transport.output()
        self.input.push_audio_frame = self._swallow
        self.input.push_frame = lambda *a, **k: asyncio.sleep(0)
        self.server = await websockets.serve(
            self.input._client_handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.addAsyncCleanup(self._close_server)

    async def _swallow(self, frame):
        pass

    async def _close_server(self):
        self.server.close()
        await self.server.wait_closed()

    def _state(self) -> dict:
        return self.transport.route_state()

    async def _settle(self, ready, timeout=3.0) -> None:
        """Wait for the transport to catch up with the socket, without a sleep
        long enough to be a guess."""
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            if ready():
                return
            await asyncio.sleep(0.02)
        self.fail(f"the transport never got there: {self._state()}")

    async def _join(self):
        socket = await websockets.connect(
            self.url, additional_headers={"X-Forwarded-For": "100.64.0.2"})
        await socket.send(json.dumps({"hello": {"device": "pixel",
                                                "token": "key"}}))
        answer = json.loads(await socket.recv())
        await self._settle(lambda: self._state()["state"] == "connected")
        return socket, answer

    async def _lose(self, socket) -> None:
        await socket.close()
        await self._settle(lambda: self._state()["state"] == "lost")

    # ── the loss ──────────────────────────────────────────────────────────
    async def test_a_connected_device_that_goes_is_lost_and_the_count_runs(self):
        socket, _ = await self._join()
        self.assertIsNone(self._state()["grace_left"],
                          "nothing is counting while it is here")
        with mock.patch.object(remote_transport, "logger") as log:
            await self._lose(socket)
        self.assertEqual(self._state()["state"], "lost")
        self.assertEqual(self._state()["grace_left"], 180)
        self.clock.tick(20)
        self.assertEqual(self._state()["grace_left"], 160, "2:40 on the card")
        said = [call[0][0] for call in log.info.call_args_list]
        self.assertIn("[audio] remote client lost — waiting up to {} s", said)

    async def test_a_device_refused_at_the_door_is_not_a_device_that_was_lost(self):
        """A stranger knocking is not the sitting's device going away: no
        window opens, and the conversation is exactly where it was."""
        async with websockets.connect(self.url) as ws:
            await ws.send(json.dumps({"hello": {"device": "pixel",
                                                "token": "wrong"}}))
            with self.assertRaises(websockets.ConnectionClosed):
                await ws.recv()
        await asyncio.sleep(0.2)
        self.assertEqual(self._state()["state"], "waiting")
        self.assertIsNone(self._state()["grace_left"])
        self.assertFalse(self.transport._grace.active)
        self.assertEqual(self.stops, [])

    async def test_a_client_that_leaves_before_its_hello_is_not_a_loss_either(self):
        async with websockets.connect(self.url):
            pass
        await asyncio.sleep(0.2)
        self.assertEqual(self._state()["state"], "waiting")
        self.assertFalse(self.transport._grace.active)

    # ── the return ────────────────────────────────────────────────────────
    async def test_a_device_back_inside_the_window_rejoins_with_a_new_depth(self):
        socket, _ = await self._join()
        await self._lose(socket)
        self.clock.tick(12)
        relayed = {"Peer": {"k": {"TailscaleIPs": ["100.64.0.3"], "CurAddr": ""}}}
        with mock.patch.object(remote_transport, "logger") as log, \
             mock.patch.object(remote_path, "tailscale_status", return_value=relayed):
            async with websockets.connect(
                    self.url,
                    additional_headers={"X-Forwarded-For": "100.64.0.3"}) as back:
                await back.send(json.dumps({"hello": {"device": "pixel",
                                                      "token": "key"}}))
                answer = json.loads(await back.recv())
                await self._settle(lambda: self._state()["state"] == "connected")
                said = [call[0] for call in log.info.call_args_list]
                self.assertIn(
                    ("[audio] remote client rejoined after {} s "
                     "(path={}, buffer={}ms)", 12, "relayed", 200), said)
                self.assertEqual(answer["buffer_ms"], 200,
                                 "a new network is a newly decided depth")
                self.assertIsNone(self._state()["grace_left"])
                self.assertTrue(self.transport._grace_task is None
                                or self.transport._grace_task.cancelled())
        self.clock.tick(600)
        await asyncio.sleep(0.4)
        self.assertEqual(self.stops, [], "a rejoin cancels the close")

    # ── the end of the wait ───────────────────────────────────────────────
    async def test_the_window_running_out_closes_the_sitting_once(self):
        socket, _ = await self._join()
        await self._lose(socket)
        with mock.patch.object(remote_transport, "logger") as log:
            self.clock.tick(181)
            await self._settle(lambda: self._state()["state"] == "ended")
            await asyncio.sleep(0.6)     # several more poll turns
        self.assertEqual(self.stops, ["stop"], "exactly once, however long")
        self.assertEqual(self._state()["state"], "ended")
        self.assertIsNone(self._state()["grace_left"])
        log.warning.assert_called_once_with(
            "[audio] remote device did not return within {} s — "
            "closing the sitting", 180)

    async def test_the_reason_is_set_before_the_signal_so_it_survives_the_close(self):
        socket, _ = await self._join()
        await self._lose(socket)
        seen = []
        self.transport._stop_sitting = lambda: seen.append(audio_route.device_gone())
        self.clock.tick(181)
        await self._settle(lambda: bool(seen))
        self.assertEqual(seen, [True], "the flag is set BEFORE the signal")
        self.assertEqual(audio_route.exit_status(), audio_route.EXIT_DEVICE_GONE)

    async def test_a_device_arriving_after_the_end_is_told_it_is_over(self):
        socket, _ = await self._join()
        await self._lose(socket)
        self.clock.tick(181)
        await self._settle(lambda: self._state()["state"] == "ended")
        with mock.patch.object(remote_transport, "logger") as log:
            async with websockets.connect(self.url) as late:
                # The door shuts on arrival, so the hello may not even land —
                # which is the same answer, arriving sooner.
                try:
                    await late.send(json.dumps({"hello": {"device": "pixel",
                                                          "token": "key"}}))
                except websockets.ConnectionClosed:
                    pass
                with self.assertRaises(websockets.ConnectionClosed) as caught:
                    await late.recv()
        closed = caught.exception
        self.assertEqual(closed.rcvd.code, 4410)
        self.assertEqual(closed.rcvd.reason, "ended")
        self.assertEqual(log.warning.call_args_list, [],
                         "nothing was refused here")

    # ── the button, during the wait ───────────────────────────────────────
    async def test_a_stop_during_the_window_cancels_the_wait(self):
        """The button and the countdown can land together. The button wins:
        one close, and an exit status that does not blame the device for a
        conversation the person ended."""
        socket, _ = await self._join()
        await self._lose(socket)
        task = self.transport._grace_task
        self.transport.note_shutdown()
        self.clock.tick(600)
        await asyncio.sleep(0.5)
        self.assertTrue(task.cancelled() or task.done())
        self.assertEqual(self.stops, [])
        self.assertFalse(audio_route.device_gone())
        self.assertEqual(audio_route.exit_status(), 0)

    async def test_the_cancel_and_end_frames_are_what_tell_it_so(self):
        """The runner's own words for a conversation coming down reach the
        transport as these two calls, and both must stand the wait down."""
        base = remote_transport.SingleClientWebsocketServerInputTransport
        for word in ("stop", "cancel"):
            with self.subTest(word=word):
                with mock.patch.object(self.transport, "note_shutdown") as told, \
                     mock.patch.object(base, word, new=mock.AsyncMock()) as parent:
                    await getattr(self.input, word)(object())
                    told.assert_called_once_with()
                    self.assertEqual(parent.await_count, 1,
                                     "and the transport still does its own part")

    async def test_a_socket_closing_during_a_stop_never_opens_a_window(self):
        socket, _ = await self._join()
        self.transport.note_shutdown()
        await socket.close()
        await asyncio.sleep(0.3)
        self.assertEqual(self._state()["state"], "connected",
                         "the shutdown is the story, not a loss")
        self.assertFalse(self.transport._grace.active)
        self.assertEqual(self.stops, [])

    # ── what the far end is told ──────────────────────────────────────────
    async def test_the_hello_answer_says_how_long_it_will_be_waited_for(self):
        transport = remote_transport.build_remote_transport(
            "pixel", token="key", grace_s=45)
        self.assertEqual(transport.grace_s, 45)
        _socket, answer = await self._join()
        self.assertEqual(answer["grace_s"], 180)
        self.assertEqual(set(answer), {"ok", "path", "buffer_ms", "grace_s"})

    async def test_the_window_length_follows_the_environment(self):
        with mock.patch.dict("os.environ",
                             {remote_grace.GRACE_ENV: "300"}):
            transport = remote_transport.build_remote_transport("pixel", token="k")
        self.assertEqual(transport.grace_s, 300)


class TheWaitForADeviceThatNeverArrives(unittest.IsolatedAsyncioTestCase):
    """The other absence: a conversation started for a device that never opens
    its talk page. The window opens when the socket starts listening, the
    first hello closes it, and running out is the same self-stop — with its
    own reason, because the launch page must not say "did not come back" of a
    device that was never there."""

    async def asyncSetUp(self):
        patch = mock.patch.object(remote_path, "tailscale_status",
                                  return_value=STATUS)
        patch.start()
        self.addCleanup(patch.stop)
        audio_route.clear_device_gone()
        self.addCleanup(audio_route.clear_device_gone)
        self.clock = _Clock()
        self.stops = []
        self.transport = remote_transport.build_remote_transport(
            "pixel", token="key", clock=self.clock, start_wait_s=30,
            stop_sitting=lambda: self.stops.append("stop"))
        self.addCleanup(self.transport.note_shutdown)
        self.input = self.transport.input()
        self.transport.output()
        self.input.push_audio_frame = self._swallow
        self.input.push_frame = lambda *a, **k: asyncio.sleep(0)
        self.server = await websockets.serve(
            self.input._client_handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.addAsyncCleanup(self._close_server)

    async def _swallow(self, frame):
        pass

    async def _close_server(self):
        self.server.close()
        await self.server.wait_closed()

    def _state(self) -> dict:
        return self.transport.route_state()

    async def _settle(self, ready, timeout=3.0) -> None:
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            if ready():
                return
            await asyncio.sleep(0.02)
        self.fail(f"the transport never got there: {self._state()}")

    async def _join(self):
        socket = await websockets.connect(
            self.url, additional_headers={"X-Forwarded-For": "100.64.0.2"})
        await socket.send(json.dumps({"hello": {"device": "pixel",
                                                "token": "key"}}))
        answer = json.loads(await socket.recv())
        await self._settle(lambda: self._state()["state"] == "connected")
        return socket, answer

    def test_before_the_socket_listens_nothing_is_counting(self):
        state = self._state()
        self.assertEqual(state["state"], "waiting")
        self.assertIsNone(state["grace_left"])
        self.assertEqual(self.transport.start_wait_s, 30)

    async def test_listening_opens_the_wait_and_the_stop_card_counts_it(self):
        self.transport.note_listening()
        self.assertEqual(self._state()["state"], "waiting")
        self.assertEqual(self._state()["grace_left"], 30)
        self.clock.tick(12)
        self.assertEqual(self._state()["grace_left"], 18)
        self.assertEqual(self.stops, [])

    async def test_a_second_listening_does_not_restart_the_count(self):
        self.transport.note_listening()
        self.clock.tick(20)
        self.transport.note_listening()
        self.assertEqual(self._state()["grace_left"], 10)

    async def test_the_first_hello_ends_the_wait(self):
        self.transport.note_listening()
        self.clock.tick(25)
        socket, answer = await self._join()
        self.assertTrue(answer["ok"])
        self.assertIsNone(self._state()["grace_left"])
        self.clock.tick(600)
        await asyncio.sleep(0.4)             # past several polls
        self.assertEqual(self.stops, [], "a device that arrived is not one that never came")
        self.assertEqual(self._state()["state"], "connected")
        await socket.close()

    async def test_running_out_closes_the_sitting_once_with_its_own_reason(self):
        self.transport.note_listening()
        self.clock.tick(31)
        await self._settle(lambda: self.stops == ["stop"])
        self.assertEqual(self._state()["state"], "ended")
        self.assertIsNone(self._state()["grace_left"])
        self.assertTrue(audio_route.device_never_came())
        self.assertFalse(audio_route.device_gone())
        self.assertEqual(audio_route.exit_status(), audio_route.EXIT_DEVICE_NEVER_CAME)
        self.clock.tick(31)
        await asyncio.sleep(0.4)
        self.assertEqual(self.stops, ["stop"], "once")

    async def test_a_device_arriving_after_the_end_is_told_it_is_over(self):
        self.transport.note_listening()
        self.clock.tick(31)
        await self._settle(lambda: self.stops == ["stop"])
        socket = await websockets.connect(self.url)
        with self.assertRaises(websockets.exceptions.ConnectionClosed) as caught:
            await socket.recv()
        self.assertEqual(caught.exception.rcvd.code, 4410)

    async def test_a_stop_during_the_wait_cancels_it(self):
        self.transport.note_listening()
        self.clock.tick(10)
        self.transport.note_shutdown()
        self.clock.tick(60)
        await asyncio.sleep(0.4)
        self.assertEqual(self.stops, [], "the button's stop is already in flight")
        self.assertFalse(audio_route.device_never_came())

    async def test_the_loss_window_is_untouched_by_the_start_wait(self):
        """A device that arrives late and then goes gets the loss window, not
        whatever was left of the start wait."""
        self.transport.note_listening()
        self.clock.tick(25)
        socket, _ = await self._join()
        await socket.close()
        await self._settle(lambda: self._state()["state"] == "lost")
        self.assertEqual(self._state()["grace_left"], 180)

    def test_the_length_follows_its_own_environment_word(self):
        with mock.patch.dict(remote_transport.os.environ,
                             {remote_grace.START_WAIT_ENV: "42",
                              remote_grace.GRACE_ENV: "900"}):
            transport = remote_transport.build_remote_transport("pixel", token="k")
        self.assertEqual(transport.start_wait_s, 42)
        self.assertEqual(transport.grace_s, 900)


class TheSelfStopTakesTheButtonsOwnPath(unittest.TestCase):
    """No second close path, no new file, no marker of its own: the sitting
    sends itself the signal the button sends, and everything downstream of it
    is the ladder that already exists."""

    def test_it_is_the_stop_buttons_signal_sent_from_inside(self):
        with mock.patch.object(remote_transport.os, "kill") as kill, \
             mock.patch.object(remote_transport.os, "getpid", return_value=4242):
            remote_transport.stop_this_sitting()
        kill.assert_called_once_with(4242, remote_transport.signal.SIGINT)

    def test_it_is_the_default_and_bot_py_passes_nothing(self):
        transport = remote_transport.build_remote_transport("pixel", token="k")
        self.assertIs(transport._stop_sitting, remote_transport.stop_this_sitting)


if __name__ == "__main__":
    unittest.main()
