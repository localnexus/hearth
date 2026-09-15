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

A third rule came out of a measured recovery failure: PortAudio enumerates
devices only when its initialise count goes 0 -> 1, so while the start-time
instance is still alive every "fresh" one inherits the device list from
before the loss — the pinned name resolves to the index of the device that
went away and the open fails, forever (284 times at 2s in one sitting).
  3. The instance is treated as process-level state, owned by `pa_pool`. A
     loss RELEASES it; a reopen waits for the pool to be clear and only then
     acquires. Because the two halves share one instance, a half going lost
     CYCLES its peer through the same path, and the peer reopens onto its own
     pin a probe later. THE GATE is untouched by this: each half still
     resolves only its own pinned name in its own direction. A peer that was
     never pinned is the exception — it has no pin to reopen on, so the
     instance is kept rather than terminated out from under it and recovery
     is off for the sitting, rather than that half going quiet unannounced.

What is deliberately leaked, and why:
  - `_on_lost` hands the dead stream's `stop_stream`/`close` to a daemon
    thread that is never joined. If the device is truly wedged, that thread
    blocks forever; joining it would import the same wedge into the async
    code that is trying to move on.
  - `_on_lost` on the output half replaces `self._executor` with a fresh
    `ThreadPoolExecutor`. The old one may have a worker stuck inside a
    `stream.write` that will never return; it is abandoned rather than
    shut down, because shutting it down would itself block.
  - The audio-library instances themselves are NOT leaked any more. The
    shared start-time instance is released on the same daemon thread, right
    after the dead handle is abandoned (the one exception being an unpinned
    peer still playing through it); an instance acquired for a reopen that
    fails is released too. What is still abandoned is the `terminate()`
    call inside that release — it can wedge, so `pa_pool` runs it on its own
    unjoined thread and simply reports the pool as unclear until it returns.

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
from hearth.audio.pa_pool import pool as _pa_pool


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

    # The other half of the same transport, sharing one audio-library
    # instance; set by `RecoveringLocalAudioTransport` once both exist.
    _peer: Optional["_RecoveryHalf"] = None

    # True from the moment a loss is declared until the release of the shared
    # instance has actually been handed to the pool. It closes the window in
    # which the pool has not been told yet and would look clear.
    _release_pending: bool = False

    _lost_at: Optional[float] = None
    _waiting_logged: bool = False
    _gave_up_logged: bool = False

    # Set when recovery has been given up for the rest of the sitting — the
    # shared instance could not be released, because an UNPINNED peer is still
    # playing through it. Cleared only by a fresh `start()`.
    _recovery_off: bool = False

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

    async def _on_lost(self, cycled: bool = False) -> None:
        """Declare the loss, drop the dead handle, and give the shared audio
        library instance back so the device can be seen if it returns.

        `cycled` marks the peer half being taken down with us: its own device
        never went anywhere, but it holds the same library instance, and that
        instance has to terminate before anything can enumerate again.

        The one case where the instance is NOT given back: a peer that was
        never pinned. Terminating closes every open stream, and an unpinned
        peer has no pin to reopen on — it would simply go quiet, with no state
        change and no line, which is the one thing this module never does. So
        the instance stays, the peer keeps playing, and recovery is off for
        the rest of the sitting, said plainly once.
        """
        self.state = "lost"
        self.since = time.time()
        self._lost_at = time.monotonic()
        self._waiting_logged = False
        self._gave_up_logged = False

        peer = self._peer
        shares_instance = peer is not None and peer._py_audio is self._py_audio
        peer_unpinned = (
            not cycled
            and shares_instance
            and (peer.state == "unpinned" or peer.pin is None)
        )
        cycle_peer = (
            not cycled
            and shares_instance
            and peer.state in ("ok", "recovered")
        )

        name = self.pin.name if self.pin else "?"
        if cycled:
            peer_direction = self._peer._direction if self._peer else "?"
            logger.warning(
                f"[audio] {self._direction} device cycled with the {peer_direction} device"
                " — the audio library restarts to see a returned device"
            )
        elif peer_unpinned:
            self._recovery_off = True
            self._gave_up_logged = True
            logger.warning(
                f"[audio] {self._direction} device lost: {name} — staying silent;"
                f" recovery unavailable this sitting (the {peer._direction} side is not pinned)"
            )
        else:
            logger.warning(f"[audio] {self._direction} device lost: {name} — staying silent until it returns")

        handle = self._take_and_clear_handle()
        pa = self._py_audio
        self._release_pending = not peer_unpinned

        if cycle_peer:
            await peer._on_lost(cycled=True)

        if peer_unpinned:
            threading.Thread(target=_abandon, args=(handle,), daemon=True).start()
        else:
            threading.Thread(target=self._abandon_and_release, args=(handle, pa), daemon=True).start()

    def _abandon_and_release(self, handle, pa) -> None:
        """Daemon-thread tail of a loss: close the dead handle, then hand the
        instance to the pool. Never joined — either call may wedge."""
        try:
            _abandon(handle)
        finally:
            try:
                _pa_pool.release(pa)
            finally:
                self._release_pending = False

    def _release_unless_shared(self, pa: pyaudio.PyAudio) -> None:
        """Give a reopen instance back — unless a half is already playing
        through it, which is the case when the peer reopened onto it first."""
        peer = self._peer
        if self._py_audio is pa or (peer is not None and peer._py_audio is pa):
            return
        _pa_pool.release(pa)

    async def _try_reopen(self) -> None:
        if self._recovery_off:
            return  # given up for this sitting, and already said so once

        if self._release_pending or not _pa_pool.clear:
            # Attempting now would only bump the initialise count and re-read
            # the device list from before the loss. Say so once, then keep
            # probing quietly — probing is cheap and costs no instance.
            if not self._waiting_logged:
                self._waiting_logged = True
                logger.info(
                    f"[audio] {self._direction} audio library has not released yet — waiting to reopen"
                )
            if (
                not self._gave_up_logged
                and self._lost_at is not None
                and (time.monotonic() - self._lost_at) > self.CLEANUP_TIMEOUT_S
            ):
                self._gave_up_logged = True
                logger.warning(
                    f"[audio] {self._direction} audio library did not release within"
                    f" {int(self.CLEANUP_TIMEOUT_S)}s — recovery unavailable this sitting;"
                    " stop and start again"
                )
            return

        pa = _pa_pool.acquire()
        if pa is None:
            return

        idx = resolve_index(pa, self.pin)
        if idx is None:
            logger.info(
                f"[audio] {self._direction} device is back but not resolvable by name — staying silent"
            )
            self._release_unless_shared(pa)
            return

        rate = self._sample_rate
        try:
            self._open_stream(pa, idx, rate)
        except Exception:
            try:
                retry_rate = int(pa.get_device_info_by_index(idx)["defaultSampleRate"])
                self._open_stream(pa, idx, retry_rate)
                self._sample_rate = retry_rate
            except Exception as exc:
                logger.warning(
                    f"[audio] {self._direction} device reopen failed ({exc!r}) — staying lost"
                )
                self._release_unless_shared(pa)
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
        self._recovery_off = False
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
        self._recovery_off = False
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

    async def _on_lost(self, cycled: bool = False) -> None:
        await super()._on_lost(cycled=cycled)
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
            self._link_halves()
        return self._input

    def output(self) -> RecoveringOutput:
        if not self._output:
            self._output = RecoveringOutput(self._pyaudio, self._params)
            self._link_halves()
        return self._output

    def _link_halves(self) -> None:
        """Let each half reach the other. They share one audio-library
        instance, so one going lost has to cycle the other."""
        if self._input and self._output:
            self._input._peer = self._output
            self._output._peer = self._input

    def audio_state(self) -> dict:
        return {
            "input": self._input.audio_state() if self._input else _empty_state(),
            "output": self._output.audio_state() if self._output else _empty_state(),
        }
