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
"""

from __future__ import annotations

import asyncio
import json
import os

import websockets
from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.transports.websocket.server import (
    SingleClientWebsocketServerInputTransport,
    SingleClientWebsocketServerParams,
    SingleClientWebsocketServerTransport,
)
from websockets.protocol import State

from hearth.audio import remote_path
from hearth.audio.remote_output import RemoteAudioOutputTransport
from hearth.tts.params import SAMPLE_RATE

#: The socket binds to loopback and nothing else: `tailscale serve` is what
#: puts it on the overlay network, and it is the only thing that should.
WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 65021

AUDIO_IN_RATE = 16000
#: How long a connected client has to send its hello before it is refused.
HELLO_TIMEOUT_S = 10.0


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
        hello = await self._read_hello(websocket)
        if not remote_path.hello_accepted(hello, device_id=gate.device_id,
                                          token=gate.token):
            # One word, for every failure: a wrong key, a wrong device and a
            # first frame of audio are told apart nowhere but here.
            logger.warning("[audio] remote client refused")
            await self._refuse(websocket)
            return

        path, buffer_ms = await self._read_path(websocket)
        await self._answer(websocket, path, buffer_ms)
        logger.info("[audio] remote client connected (path={}, buffer={}ms)",
                    path, buffer_ms)

        self._websocket = websocket
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

    async def _answer(self, websocket, path: str, buffer_ms: int) -> None:
        try:
            await websocket.send(json.dumps(
                {"ok": True, "path": path, "buffer_ms": buffer_ms}))
        except Exception as exc:  # noqa: BLE001 — logged without the detail
            logger.warning("[audio] remote hello answer failed ({})",
                           type(exc).__name__)

    async def _refuse(self, websocket) -> None:
        try:
            await websocket.close(code=remote_path.REFUSED_CODE,
                                  reason=remote_path.REFUSED_REASON)
        except Exception:  # noqa: BLE001 — a refusal that cannot be sent is still one
            pass


class RemoteAudioTransport(SingleClientWebsocketServerTransport):
    """The remote route's transport: the socket, the gate, and the one dict
    ``/route`` reports."""

    def __init__(self, params, *, host: str, port: int, device_id: str,
                 token: str, **kwargs) -> None:
        super().__init__(params, host=host, port=port, **kwargs)
        self._device_id = str(device_id)
        self._token = str(token)           # compared, never logged, never sent
        self._state = "waiting"
        self._path = None
        self._buffer_ms = None

    # ── what the gate needs ───────────────────────────────────────────────
    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def token(self) -> str:
        return self._token

    def note_connected(self, path: str, buffer_ms: int) -> None:
        self._state = "connected"
        self._path = path
        self._buffer_ms = int(buffer_ms)
        if self._output is not None:
            self._output.backlog.set_depth_for(buffer_ms)

    def note_disconnected(self) -> None:
        self._state = "lost"

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
        and numbers only — no address, no key, no audio."""
        shed = self._output.backlog.shed_ms if self._output is not None else 0.0
        return {"kind": "remote", "device": self._device_id, "state": self._state,
                "path": self._path, "buffer_ms": self._buffer_ms,
                "shed_ms": int(shed)}


def build_remote_transport(device_id: str, *, token: str | None = None,
                           host: str | None = None, port: int | None = None,
                           origins: list | None = None) -> RemoteAudioTransport:
    """The one factory ``bot.py`` calls for a remote sitting.

    Raises ``MissingServeToken`` when there is no access key to gate on: an
    ungated audio socket on the overlay network is not a degraded sitting, it
    is a different thing entirely.
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
        session_timeout=None,      # the grace window is phase B
        allowed_origins=origins if origins is not None else allowed_origins(),
    )
    return RemoteAudioTransport(
        params, host=host or WS_HOST, port=port if port is not None else ws_port(),
        device_id=device_id, token=key)
