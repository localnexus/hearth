"""audio/remote_output.py — the companion's voice, on its way to the device.

Split out of ``remote_transport`` because it answers one question on its own:
what happens when the far end cannot keep up.

Measured on this box 2026-09-15: on a TCP socket, a consumer slower than the
frame cadence accumulates delay **without bound** instead of shedding it — a
planted 25 ms delay (over the 20 ms frame) put the uplink at 740 ms and
climbing, while 5 ms left it flat. A queue that only absorbs therefore turns a
slow moment into a conversation running minutes behind, invisibly, because
nothing in the browser can see it until it is seconds deep.

So the outbound side keeps its own queue with a ceiling, sheds the OLDEST audio
to stay under it, counts every millisecond it shed, and says so at most once
every five seconds. A shed frame is a glitch you hear once. An unbounded queue
is a companion answering the question before last.

Raw int16 at 24 kHz goes out; the only thing that is not audio is a one-word
control message on a barge-in, because raw PCM carries no frame types and the
far end has to be told to stop playing what it has.
"""

from __future__ import annotations

import asyncio
import collections
import json
import time

import websockets
from loguru import logger
from pipecat.frames.frames import Frame, InterruptionFrame
from pipecat.transports.websocket.server import (
    SingleClientWebsocketServerOutputTransport,
)

from hearth.tts.params import SAMPLE_RATE

#: The outbound queue may hold this much audio before the oldest is shed.
MIN_BACKLOG_MS = 200
#: At most one stall line per this many seconds, however long the stall runs.
STALL_LOG_EVERY_S = 5.0


# ── the outbound queue ────────────────────────────────────────────────────────

class OutputBacklog:
    """Audio waiting to go out, with a ceiling and an honest count of what it
    dropped to stay under it.

    Pure: bytes in, bytes out, milliseconds counted. No socket, no clock, no
    loop — so the shed rule is a unit test rather than a thing you find out
    about during a conversation.
    """

    def __init__(self, sample_rate: int, channels: int = 1,
                 depth_ms: int = MIN_BACKLOG_MS) -> None:
        self._bytes_per_ms = max(1.0, (sample_rate / 1000.0) * max(1, channels) * 2)
        self._queue: collections.deque = collections.deque()
        self._queued_bytes = 0
        self._shed_ms = 0.0
        self.depth_ms = float(depth_ms)

    @property
    def queued_ms(self) -> float:
        return self._queued_bytes / self._bytes_per_ms

    @property
    def shed_ms(self) -> float:
        """Total audio dropped for this sitting, in milliseconds."""
        return self._shed_ms

    def set_depth_for(self, buffer_ms: int) -> None:
        """The ceiling follows the far end's own depth: twice it, or 200 ms,
        whichever is larger. A relayed sitting is allowed more slack because
        its buffer already is."""
        self.depth_ms = float(max(MIN_BACKLOG_MS, 2 * int(buffer_ms or 0)))

    def push(self, chunk: bytes) -> float:
        """Queue one chunk; shed the OLDEST audio until the queue fits.

        → the milliseconds shed by THIS push (0.0 when nothing was).
        """
        if not chunk:
            return 0.0
        self._queue.append(bytes(chunk))
        self._queued_bytes += len(chunk)
        shed_bytes = 0
        while self._queue and self.queued_ms > self.depth_ms:
            oldest = self._queue.popleft()
            self._queued_bytes -= len(oldest)
            shed_bytes += len(oldest)
        shed = shed_bytes / self._bytes_per_ms
        self._shed_ms += shed
        return shed

    def pop(self) -> bytes | None:
        """The oldest chunk still queued, or ``None``."""
        if not self._queue:
            return None
        chunk = self._queue.popleft()
        self._queued_bytes -= len(chunk)
        return chunk

    def clear(self) -> None:
        """Drop everything queued WITHOUT counting it as shed — a barge-in
        throws this audio away on purpose, which is not a stall."""
        self._queue.clear()
        self._queued_bytes = 0


# ── the two halves ────────────────────────────────────────────────────────────

class RemoteAudioOutputTransport(SingleClientWebsocketServerOutputTransport):
    """The companion's voice, out to the device — raw int16 at 24 kHz."""

    def __init__(self, transport, params, **kwargs):
        super().__init__(transport, params, **kwargs)
        self._backlog = OutputBacklog(SAMPLE_RATE, params.audio_out_channels)
        self._drain_task = None
        self._last_stall_log = 0.0

    @property
    def backlog(self) -> OutputBacklog:
        return self._backlog

    async def start(self, frame):
        await super().start(frame)
        self._backlog = OutputBacklog(self.sample_rate or SAMPLE_RATE,
                                      self._params.audio_out_channels,
                                      depth_ms=self._backlog.depth_ms)
        if self._drain_task is None:
            self._drain_task = self.create_task(self._drain())

    async def stop(self, frame):
        await self._stop_drain()
        await super().stop(frame)

    async def cancel(self, frame):
        await self._stop_drain()
        await super().cancel(frame)

    async def _stop_drain(self) -> None:
        if self._drain_task is not None:
            await self.cancel_task(self._drain_task)
            self._drain_task = None

    async def write_audio_frame(self, frame) -> bool:
        """Queue one chunk of the companion's voice. Paced exactly as pipecat
        paces it, so the pipeline still runs against an audio-device clock."""
        if not self._websocket:
            return False
        shed = self._backlog.push(frame.audio)
        if shed:
            self._maybe_say_stalled()
        await self._write_audio_sleep()
        return True

    def _maybe_say_stalled(self) -> None:
        now = time.monotonic()
        if now - self._last_stall_log < STALL_LOG_EVERY_S:
            return
        self._last_stall_log = now
        logger.warning("[audio] remote output stalled ({} ms shed)",
                       int(self._backlog.shed_ms))

    async def _write_frame(self, frame: Frame) -> None:
        """Raw PCM carries no frame types, so the one control the far end
        genuinely needs — 'stop playing, they interrupted you' — goes as a
        text message and clears whatever is still queued here."""
        if isinstance(frame, InterruptionFrame):
            self._backlog.clear()
            await self._send_text({"interrupt": True})

    async def _send_text(self, doc: dict) -> None:
        socket = self._websocket
        if socket is None:
            return
        try:
            await socket.send(json.dumps(doc))
        except websockets.ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001 — a send failure is never fatal
            logger.debug("[audio] remote control message dropped ({})",
                         type(exc).__name__)

    async def _drain(self) -> None:
        """Hand queued audio to the socket as fast as the socket will take it.

        Separate from the producer on purpose: it is the gap between these two
        that the backlog measures, and a shed here is the only thing standing
        between a slow phone and a conversation that runs minutes behind.
        """
        while True:
            chunk = self._backlog.pop()
            if chunk is None:
                await asyncio.sleep(0.005)
                continue
            socket = self._websocket
            if socket is None:
                continue
            try:
                await socket.send(chunk)
            except websockets.ConnectionClosed:
                await asyncio.sleep(0.02)
            except Exception as exc:  # noqa: BLE001 — never kill the drain
                logger.debug("[audio] remote send failed ({})", type(exc).__name__)
                await asyncio.sleep(0.02)
