"""audio/remote_transport.py — a sitting's audio on an enrolled device.

The desk route opens a local device and pins it. This one opens a WebSocket on
``127.0.0.1:65021`` instead and waits for one enrolled device to arrive through
``tailscale serve``. Raw little-endian int16 rides the socket: 16 kHz up (the
microphone), 24 kHz down (the companion's voice), 20 ms to a message, no WAV
header and no serializer — the rates already match on both ends, so nothing is
resampled anywhere.

**This module never touches PortAudio.** Not ``pa_pool``, not ``device_pin``,
not ``recovering_transport``, not ``pyaudio`` — directly or transitively. The
desk transport's recovery is a gate over PROCESS-level PortAudio state, and a
sitting with no local device has no business putting that state in play. The
rule is checked by a test that makes ``import pyaudio`` raise and builds this
transport anyway.

Three things sit on top of pipecat's single-client WebSocket server:

**The hello gate.** The desk pins a device by identity and never reopens on
another. The remote route's equivalent is the first frame: a device that
cannot name itself and carry the access key is closed with 4401 and the word
``refused``, and the sitting goes back to waiting. Audio reaches the pipeline
only after that frame is accepted. A second device, while one is connected, is
refused by pipecat's own rule, which is kept in front of the gate.

**Path-aware depth.** The hello is answered with the far end's jitter-buffer
depth, looked up from how the peer actually got here (``remote_path``). It is
decided per connection, so a device that rejoins on a different network is told
a new depth rather than keeping a stale one.

**A stall detector on the way out**, which lives next door in
``remote_output``: the outbound queue sheds the oldest audio past a bound and
counts what it shed, because a TCP socket with a slow consumer accumulates
delay without bound instead of dropping it (measured 2026-09-15).

**The wait, and the end of it.** A socket that goes is not a conversation that
is over — a screen sleeps, a network flips, a lift has no signal — so the
device is waited for, and the far end is told at the hello exactly how long it
has. Within the window a hello from the same device is a REJOIN: the pipeline
was never torn down, the answer carries a freshly decided depth for whatever
network it came back on, and the conversation continues mid-answer. Past the
window the conversation closes ITSELF, and it does that by taking the Stop
button's own path — the same signal, sent from inside — so the transcript is
kept or deleted exactly as the switches said and no second close path exists to
disagree with the first. The reason survives the exit as a status of 3.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal

import websockets
from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.transports.websocket.server import (
    SingleClientWebsocketServerInputTransport,
    SingleClientWebsocketServerParams,
    SingleClientWebsocketServerTransport,
)
from websockets.protocol import State

from hearth.audio import remote_grace, remote_path
from hearth.audio import route as audio_route
from hearth.audio.remote_output import RemoteAudioOutputTransport
from hearth.tts.params import SAMPLE_RATE

#: The socket binds to loopback and nothing else: `tailscale serve` is what
#: puts it on the overlay network, and it is the only thing that should.
WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 65021

AUDIO_IN_RATE = 16000
#: How long a connected client has to send its hello before it is refused.
HELLO_TIMEOUT_S = 10.0
#: How often the waiting task looks at the window. Short enough that the close
#: lands on the second it was promised for, cheap enough to be free.
GRACE_POLL_S = 0.25


def stop_this_sitting() -> None:
    """Close this conversation the way the Stop button closes it.

    The button sends SIGINT to the bot's process group; the runner's own signal
    handler turns that into a cancel, the pipeline comes down, and the close
    ladder in ``bot.main`` runs — capture finalised, panel down, drained, and
    the keep-or-delete switch read exactly as it was left, late marker and all.

    So this is that same signal, sent from inside, and deliberately nothing
    else. A second way to close a conversation is a second way for the
    transcript to end up somewhere nobody expected.
    """
    os.kill(os.getpid(), signal.SIGINT)


class MissingServeToken(RuntimeError):
    """No ``config/serve-token`` — a remote sitting must not start ungated."""


def ws_port() -> int:
    """The socket's port: ``HEARTH_AUDIO_WS_PORT``, else 65021."""
    raw = os.environ.get("HEARTH_AUDIO_WS_PORT", "").strip()
    if not raw:
        return DEFAULT_WS_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_WS_PORT
    return port if 1 <= port <= 65535 else DEFAULT_WS_PORT


def allowed_origins() -> list:
    """The page origins allowed to open the socket.

    By default: this machine's own names, on the port the page is served at,
    under both schemes — looked up at start rather than written down, so a
    renamed machine or a new network needs no edit here.
    ``HEARTH_AUDIO_ORIGINS`` (comma-separated) replaces the list whole — an
    operator serving the page from somewhere else needs the whole list to be
    theirs, not a list with ours still in it.
    """
    raw = os.environ.get("HEARTH_AUDIO_ORIGINS", "").strip()
    if not raw:
        return remote_path.facade_origins()
    return [part.strip() for part in raw.split(",") if part.strip()]


class RemoteAudioInputTransport(SingleClientWebsocketServerInputTransport):
    """The device's microphone, in — behind the hello gate."""

    async def _client_handler(self, websocket) -> None:
        # pipecat's own rule, kept in front of the gate: one device at a time,
        # and the one already talking keeps the socket.
        if self._websocket and self._websocket.state is State.OPEN:
            logger.warning("[audio] remote client refused")
            await websocket.close(code=1013,
                                  reason="Server already has a connected client")
            return

        gate = self._transport
        if gate.ended:
            # The conversation already closed itself over this device's
            # absence, and the socket has not finished coming down yet.
            # Nothing is being refused here — there is simply nothing left to
            # join — and the page needs to hear that rather than back off and
            # try again into a door that is closing.
            await self._say_ended(websocket)
            return

        hello = await self._read_hello(websocket)
        if not remote_path.hello_accepted(hello, device_id=gate.device_id,
                                          token=gate.token):
            # One word, for every failure: a wrong key, a wrong device and a
            # first frame of audio are told apart nowhere but here.
            logger.warning("[audio] remote client refused")
            await self._refuse(websocket)
            return

        path, buffer_ms = await self._read_path(websocket)
        await self._answer(websocket, path, buffer_ms, gate.grace_s)

        self._websocket = websocket
        # The gate says the line: only it knows whether this is a first
        # connection or a device coming back, and the two read differently.
        gate.note_connected(path, buffer_ms)
        await self._callbacks.on_client_connected(websocket)
        if not self._monitor_task and self._params.session_timeout:
            self._monitor_task = self.create_task(
                self._monitor_websocket(websocket, self._params.session_timeout))

        try:
            async for message in websocket:
                # Raw PCM only. A text frame after the hello says nothing this
                # phase listens for, and is dropped rather than parsed.
                if isinstance(message, (bytes, bytearray)):
                    await self.push_audio_frame(InputAudioRawFrame(
                        audio=bytes(message),
                        sample_rate=self.sample_rate or AUDIO_IN_RATE,
                        num_channels=self._params.audio_in_channels))
        except websockets.ConnectionClosed:
            logger.debug("[audio] remote client closed the socket")
        except Exception as exc:  # noqa: BLE001 — a bad client is not a crash
            logger.warning("[audio] remote receive stopped ({})", type(exc).__name__)

        await self._callbacks.on_client_disconnected(websocket)
        await websocket.close()
        if self._websocket is websocket:
            self._websocket = None
        gate.note_disconnected()

    async def _read_hello(self, websocket):
        """The first frame, bounded. Anything but a well-formed hello object —
        a timeout, a closed socket, audio, malformed JSON — answers ``None``,
        which the caller refuses."""
        try:
            first = await asyncio.wait_for(websocket.recv(), timeout=HELLO_TIMEOUT_S)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            return None
        except Exception:  # noqa: BLE001 — an unreadable first frame is a refusal
            return None
        return remote_path.parse_hello(first)

    async def _read_path(self, websocket) -> tuple:
        """Which way this device came, and how deep its buffer must be.

        ``tailscale status`` is a subprocess, so it runs off the loop; a peer
        we cannot place is ``unknown``, which buys the deeper buffer.
        """
        headers = getattr(getattr(websocket, "request", None), "headers", None)
        address = remote_path.peer_address(headers, websocket.remote_address)
        status = await asyncio.to_thread(remote_path.tailscale_status)
        return remote_path.describe(address, status)

    async def _answer(self, websocket, path: str, buffer_ms: int,
                      grace_s: int) -> None:
        """The hello's answer: the depth to hold, and how long this
        conversation will wait if the socket goes. The far end needs the second
        one to count down honestly instead of retrying into the dark."""
        try:
            await websocket.send(json.dumps(
                {"ok": True, "path": path, "buffer_ms": buffer_ms,
                 "grace_s": grace_s}))
        except Exception as exc:  # noqa: BLE001 — logged without the detail
            logger.warning("[audio] remote hello answer failed ({})",
                           type(exc).__name__)

    async def _refuse(self, websocket) -> None:
        try:
            await websocket.close(code=remote_path.REFUSED_CODE,
                                  reason=remote_path.REFUSED_REASON)
        except Exception:  # noqa: BLE001 — a refusal that cannot be sent is still one
            pass

    async def _say_ended(self, websocket) -> None:
        """Not a refusal, and logged as nothing: the conversation is over."""
        try:
            await websocket.close(code=remote_path.ENDED_CODE,
                                  reason=remote_path.ENDED_REASON)
        except Exception:  # noqa: BLE001 — a closed socket says the same thing
            pass

    # ── the conversation coming down by another hand ──────────────────────
    # A Stop (the button, or a Ctrl-C at the desk) reaches the transport as an
    # end or a cancel frame. Whatever the window was waiting for, it is not
    # waited for any more — and the self-stop must never fire on top of a stop
    # already in flight, which would be a second signal at a dying process.

    async def stop(self, frame) -> None:
        self._transport.note_shutdown()
        await super().stop(frame)

    async def cancel(self, frame) -> None:
        self._transport.note_shutdown()
        await super().cancel(frame)


class RemoteAudioTransport(SingleClientWebsocketServerTransport):
    """The remote route's transport: the socket, the gate, and the one dict
    ``/route`` reports."""

    def __init__(self, params, *, host: str, port: int, device_id: str,
                 token: str, grace=None, stop_sitting=None, **kwargs) -> None:
        super().__init__(params, host=host, port=port, **kwargs)
        self._device_id = str(device_id)
        self._token = str(token)           # compared, never logged, never sent
        self._state = "waiting"
        self._path = None
        self._buffer_ms = None
        self._grace = grace if grace is not None else remote_grace.GraceWindow()
        self._stop_sitting = stop_sitting or stop_this_sitting
        self._grace_task = None
        self._stop_fired = False           # the self-stop happens once, or not
        self._shutting_down = False

    # ── what the gate needs ───────────────────────────────────────────────
    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def token(self) -> str:
        return self._token

    @property
    def grace_s(self) -> int:
        """How long this conversation waits for its device — the number the
        hello answer carries and the log lines quote."""
        return int(round(self._grace.seconds))

    @property
    def ended(self) -> bool:
        """True once the wait ran out. From here a device that arrives is told
        the conversation is over rather than refused."""
        return self._state == "ended"

    def note_connected(self, path: str, buffer_ms: int) -> None:
        """A device is on the socket — for the first time, or back again.

        A rejoin cancels the wait, says how long the gap was, and takes a
        freshly decided depth: a device that comes back on a different network
        is a different path, and keeping the old depth would be keeping a
        measurement of a network that is no longer there.
        """
        gap = None
        if self._state == "lost":
            self._cancel_grace()
            gap = self._grace.rejoin()
        self._state = "connected"
        self._path = path
        self._buffer_ms = int(buffer_ms)
        if self._output is not None:
            self._output.backlog.set_depth_for(buffer_ms)
        if gap is None:
            logger.info("[audio] remote client connected (path={}, buffer={}ms)",
                        path, self._buffer_ms)
        else:
            logger.info("[audio] remote client rejoined after {} s "
                        "(path={}, buffer={}ms)",
                        int(round(gap)), path, self._buffer_ms)

    def note_disconnected(self) -> None:
        """The socket went.

        Only a CONNECTED device can be lost. A client that was refused, or one
        that closed before its hello was accepted, leaves the conversation
        exactly where it was — still waiting for the device it was started for
        — and must not open a window, because a stranger knocking is not the
        device going away.
        """
        if self._state != "connected" or self._shutting_down:
            # A conversation that is already coming down has no one to wait
            # for: the socket closing is part of the shutdown, not a loss.
            return
        self._state = "lost"
        self._grace.lose()
        logger.info("[audio] remote client lost — waiting up to {} s",
                    self.grace_s)
        try:
            self._grace_task = asyncio.get_running_loop().create_task(
                self._wait_out_grace())
        except RuntimeError:
            # No loop (a unit test calling this directly): the state and the
            # window are still true, there is simply nothing to run the wait.
            self._grace_task = None

    def note_shutdown(self) -> None:
        """The conversation is coming down by another hand — the Stop button,
        a Ctrl-C at the desk, the runner cancelling.

        Whatever the window was waiting for, it is not waited for any more. The
        self-stop must never fire on top of a stop already in flight: that
        would be a second signal at a dying process, and a reason on the exit
        that says the device went when the person pressed the button.
        """
        self._shutting_down = True
        self._cancel_grace()

    # ── the wait ──────────────────────────────────────────────────────────
    def _cancel_grace(self) -> None:
        task, self._grace_task = self._grace_task, None
        if task is not None and not task.done():
            task.cancel()

    async def _wait_out_grace(self) -> None:
        """Sit out the window, looking at the clock rather than trusting one
        long sleep — so a test can move time without spending it, and so the
        close lands on the second it was promised for."""
        while True:
            remaining = self._grace.remaining()
            if remaining <= 0:
                break
            await asyncio.sleep(min(GRACE_POLL_S, remaining))
        self._close_over_the_device()

    def _close_over_the_device(self) -> None:
        """The wait ran out. Say so, remember why, and press Stop from inside.

        Exactly once: a window that expires while a stop is already in flight
        is a stop that has already happened.
        """
        if self._stop_fired or self._shutting_down or self._state != "lost":
            return
        self._stop_fired = True
        self._state = "ended"
        logger.warning("[audio] remote device did not return within {} s — "
                       "closing the sitting", self.grace_s)
        # Set BEFORE the signal: the close ladder runs on the way out and the
        # entry point reads this after it, which is how the reason survives.
        audio_route.mark_device_gone()
        self._stop_sitting()

    # ── the halves ────────────────────────────────────────────────────────
    def input(self) -> RemoteAudioInputTransport:
        if not self._input:
            self._input = RemoteAudioInputTransport(
                self, self._host, self._port, self._params, self._callbacks,
                name=self._input_name)
        return self._input

    def output(self) -> RemoteAudioOutputTransport:
        if not self._output:
            self._output = RemoteAudioOutputTransport(
                self, self._params, name=self._output_name)
        return self._output

    # ── what the launch page reads ────────────────────────────────────────
    def route_state(self) -> dict:
        """The sitting's route, for ``GET /route`` and the Stop card. Names
        and numbers only — no address, no key, no audio.

        ``grace_left`` is the countdown the Stop card shows, in whole seconds,
        and it is null unless a device is actually away.
        """
        shed = self._output.backlog.shed_ms if self._output is not None else 0.0
        return {"kind": "remote", "device": self._device_id, "state": self._state,
                "path": self._path, "buffer_ms": self._buffer_ms,
                "shed_ms": int(shed),
                "grace_left": self._grace.left() if self._state == "lost" else None}


def build_remote_transport(device_id: str, *, token: str | None = None,
                           host: str | None = None, port: int | None = None,
                           origins: list | None = None,
                           grace_s: float | None = None, clock=None,
                           stop_sitting=None) -> RemoteAudioTransport:
    """The one factory ``bot.py`` calls for a remote sitting.

    Raises ``MissingServeToken`` when there is no access key to gate on: an
    ungated audio socket on the overlay network is not a degraded sitting, it
    is a different thing entirely.

    ``bot.py`` passes none of the keyword arguments. They are the seams a test
    drives: its own key, its own port, its own window and clock, and — the one
    that matters — its own stand-in for the self-stop, so the close can be
    asserted rather than performed.
    """
    key = token or remote_path.read_serve_token()
    if not key:
        raise MissingServeToken(str(remote_path.serve_token_path()))
    params = SingleClientWebsocketServerParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=AUDIO_IN_RATE,
        audio_out_sample_rate=SAMPLE_RATE,
        add_wav_header=False,      # raw PCM: the page reads int16 straight
        serializer=None,           # both halves speak PCM themselves
        session_timeout=None,      # the wait below is ours, not pipecat's
        allowed_origins=origins if origins is not None else allowed_origins(),
    )
    window = remote_grace.GraceWindow(
        grace_s, **({"clock": clock} if clock is not None else {}))
    return RemoteAudioTransport(
        params, host=host or WS_HOST, port=port if port is not None else ws_port(),
        device_id=device_id, token=key, grace=window, stop_sitting=stop_sitting)
