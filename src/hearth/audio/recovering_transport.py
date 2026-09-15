"""recovering_transport.py — a local audio transport that survives a device
that lapses mid-session.

THE GATE: recovery may only re-open onto the SAME device identity it started
on, never onto whatever PortAudio now calls the default. Re-opening on a
different device would route a private conversation through the room
speakers; the index for a reopen is derived only from the pinned NAME in the
pinned DIRECTION, uniquely, via `device_pin.resolve_index`, or it does not
happen at all. If the pinned device is not back, the transport stays silent
and reports its state — it never guesses at a substitute.

Two rules follow from a measured failure (a dead PortAudio stream wedges any
call against it forever, and Ctrl-C does not land on a wedged call):
  1. Detection never makes a blocking call on the dead stream/instance. The
     watchdog only ever compares timestamps already stamped by the audio
     callback/write path, and the one real I/O check (`uid_present`) is
     already async and is bounded with `asyncio.wait_for` — a timeout there
     reads as "unknown", never as "present".
  2. A dead stream's teardown is never awaited on the event loop. It is
     abandoned to its own thread, unjoined.

What is deliberately leaked, and why:
  - `_on_lost` hands the dead stream's `stop_stream`/`close` to a daemon
    thread that is never joined. If the device is truly wedged, that thread
    blocks forever; joining it would import the same wedge into the async
    code that is trying to move on.
  - `_on_lost` on the output half replaces `self._executor` with a fresh
    `ThreadPoolExecutor`. The old one may have a worker stuck inside a
    `stream.write` that will never return; it is abandoned rather than
    shut down, because shutting it down would itself block.

Log lines carry names only — never a UID, never audio.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pyaudio
from loguru import logger

from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.local.audio import (
    LocalAudioInputTransport,
    LocalAudioOutputTransport,
    LocalAudioTransport,
)

from hearth.audio.device_pin import DevicePin, pin_default, resolve_index, uid_present


def _abandon(handle) -> None:
    try:
        handle.stop_stream()
        handle.close()
    except Exception:
        pass


def _empty_state() -> dict:
    return {"state": "unpinned", "device": None, "since": None, "uid": None}


class _RecoveryHalf:
    """Shared state machine for a recovering input or output half.

    Concrete halves supply `_direction`, `_get_handle`/`_set_handle` (the
    PortAudio stream attribute) and `_check_liveness` (a non-blocking
    timestamp check for their own kind of stuck-ness).
    """

    _direction = ""  # "in" | "out", set by the concrete subclass

    pin: Optional[DevicePin] = None
    state: str = "unpinned"
    since: Optional[float] = None
    _last_activity_at: Optional[float] = None
    _watchdog: Optional[asyncio.Task] = None

    PERIOD_S = 2.0
    PROBE_TIMEOUT_S = 5.0
    INPUT_STALE_S = 3.0
    OUTPUT_STUCK_S = 5.0
    CLEANUP_TIMEOUT_S = 3.0

    def audio_state(self) -> dict:
        return {
            "state": self.state,
            "device": self.pin.name if self.pin else None,
            "since": self.since,
            "uid": self.pin.uid if self.pin else None,
        }

    def _get_handle(self):
        raise NotImplementedError

    def _set_handle(self, value) -> None:
        raise NotImplementedError

    def _check_liveness(self) -> bool:
        raise NotImplementedError

    def _open_stream(self, pa, idx: int, rate: int) -> None:
        raise NotImplementedError

    def _take_and_clear_handle(self):
        handle = self._get_handle()
        self._set_handle(None)
        return handle

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self.PERIOD_S)
            await self._probe()

    async def _probe(self) -> None:
        """One round of detection. A no-op when unpinned. Never blocks on
        the (possibly dead) stream or PyAudio instance directly."""
        if self.state == "unpinned" or self.pin is None:
            return

        try:
            present = await asyncio.wait_for(uid_present(self.pin.uid), self.PROBE_TIMEOUT_S)
        except asyncio.TimeoutError:
            present = None

        if self.state in ("ok", "recovered"):
            if present is False or self._check_liveness():
                await self._on_lost()
            return

        if self.state == "lost" and present is True:
            await self._try_reopen()
        # present is None, or still absent while lost: nothing to do.

    async def _on_lost(self) -> None:
        self.state = "lost"
        self.since = time.time()
        name = self.pin.name if self.pin else "?"
        logger.warning(f"[audio] {self._direction} device lost: {name} — staying silent until it returns")

        handle = self._take_and_clear_handle()
        threading.Thread(target=_abandon, args=(handle,), daemon=True).start()

    async def _try_reopen(self) -> None:
        pa = pyaudio.PyAudio()  # a fresh instance: the original may be wedged
        idx = resolve_index(pa, self.pin)
        if idx is None:
            logger.info(
                f"[audio] {self._direction} device is back but not resolvable by name — staying silent"
            )
            return

        rate = self._sample_rate
        try:
            self._open_stream(pa, idx, rate)
        except Exception:
            retry_rate = int(pa.get_device_info_by_index(idx)["defaultSampleRate"])
            try:
                self._open_stream(pa, idx, retry_rate)
                self._sample_rate = retry_rate
            except Exception:
                logger.warning(f"[audio] {self._direction} device reopen failed — staying lost")
                return

        self._py_audio = pa
        self.state = "recovered"
        self.since = time.time()
        logger.info(f"[audio] {self._direction} device back: {self.pin.name}")

    async def _cleanup_half(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

        handle = self._take_and_clear_handle()
        if handle is None:
            return

        # A raw, unjoined daemon thread — never the loop's default executor.
        # That executor is joined by asyncio.run()'s own shutdown, so a
        # close() that never returns would wedge shutdown itself; a bare
        # thread we only poll for, never join, cannot do that.
        done = threading.Event()

        def _close_and_signal() -> None:
            _abandon(handle)
            done.set()

        threading.Thread(target=_close_and_signal, daemon=True).start()

        deadline = time.monotonic() + self.CLEANUP_TIMEOUT_S
        while not done.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        if not done.is_set():
            logger.warning(f"[audio] {self._direction} teardown abandoned after {self.CLEANUP_TIMEOUT_S}s")


class RecoveringInput(_RecoveryHalf, LocalAudioInputTransport):
    """A `LocalAudioInputTransport` that pins its device by identity and
    recovers onto that same identity if it drops out mid-session."""

    _direction = "in"

    async def start(self, frame) -> None:
        self.pin = await pin_default("in")
        idx = resolve_index(self._py_audio, self.pin) if self.pin else None
        if idx is not None:
            self._params = self._params.model_copy(update={"input_device_index": idx})
            self.state = "ok"
        else:
            self.state = "unpinned"

        await super().start(frame)
        self._watchdog = asyncio.create_task(self._watch())

    def _audio_in_callback(self, in_data, frame_count, time_info, status):
        self._last_activity_at = time.monotonic()
        return super()._audio_in_callback(in_data, frame_count, time_info, status)

    async def cleanup(self) -> None:
        await self._cleanup_half()
        await BaseInputTransport.cleanup(self)

    def _get_handle(self):
        return self._in_stream

    def _set_handle(self, value) -> None:
        self._in_stream = value

    def _check_liveness(self) -> bool:
        if self._in_stream is None or self._last_activity_at is None:
            return False
        return (time.monotonic() - self._last_activity_at) > self.INPUT_STALE_S

    def _open_stream(self, pa, idx: int, rate: int) -> None:
        num_frames = int(rate / 100) * 2  # 20ms of audio, matches pipecat's own start()
        stream = pa.open(
            format=pa.get_format_from_width(2),
            channels=self._params.audio_in_channels,
            rate=rate,
            frames_per_buffer=num_frames,
            stream_callback=self._audio_in_callback,
            input=True,
            input_device_index=idx,
        )
        stream.start_stream()
        self._in_stream = stream


class RecoveringOutput(_RecoveryHalf, LocalAudioOutputTransport):
    """A `LocalAudioOutputTransport` that pins its device by identity and
    recovers onto that same identity if it drops out mid-session."""

    _direction = "out"
    _write_started_at: Optional[float] = None

    async def start(self, frame) -> None:
        self.pin = await pin_default("out")
        idx = resolve_index(self._py_audio, self.pin) if self.pin else None
        if idx is not None:
            self._params = self._params.model_copy(update={"output_device_index": idx})
            self.state = "ok"
        else:
            self.state = "unpinned"

        await super().start(frame)
        self._watchdog = asyncio.create_task(self._watch())

    async def write_audio_frame(self, frame) -> bool:
        self._write_started_at = time.monotonic()
        try:
            return await super().write_audio_frame(frame)
        finally:
            self._write_started_at = None

    async def _on_lost(self) -> None:
        await super()._on_lost()
        # The old worker may be wedged inside a write that never returns;
        # it is abandoned on purpose, never shut down (that would block too).
        self._executor = ThreadPoolExecutor(max_workers=1)

    async def cleanup(self) -> None:
        await self._cleanup_half()
        await BaseOutputTransport.cleanup(self)

    def _get_handle(self):
        return self._out_stream

    def _set_handle(self, value) -> None:
        self._out_stream = value

    def _check_liveness(self) -> bool:
        if self._out_stream is None or self._write_started_at is None:
            return False
        return (time.monotonic() - self._write_started_at) > self.OUTPUT_STUCK_S

    def _open_stream(self, pa, idx: int, rate: int) -> None:
        stream = pa.open(
            format=pa.get_format_from_width(2),
            channels=self._params.audio_out_channels,
            rate=rate,
            output=True,
            output_device_index=idx,
        )
        stream.start_stream()
        self._out_stream = stream


class RecoveringLocalAudioTransport(LocalAudioTransport):
    """`LocalAudioTransport` whose halves are `RecoveringInput`/`RecoveringOutput`."""

    def input(self) -> RecoveringInput:
        if not self._input:
            self._input = RecoveringInput(self._pyaudio, self._params)
        return self._input

    def output(self) -> RecoveringOutput:
        if not self._output:
            self._output = RecoveringOutput(self._pyaudio, self._params)
        return self._output

    def audio_state(self) -> dict:
        return {
            "input": self._input.audio_state() if self._input else _empty_state(),
            "output": self._output.audio_state() if self._output else _empty_state(),
        }
