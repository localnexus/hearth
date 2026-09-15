"""test_recovering_transport.py — the local audio transport that recovers
onto the SAME device identity it started on, never a different one.

Proves:
  1. start pins the device by explicit index; unpinned start touches nothing;
  2. a stale input or a stuck output write is detected without ever blocking
     on the dead stream, and the dead handle is closed off-thread;
  3. THE GATE — a device that comes back under a different name/index is
     never opened; only the same pinned name, resolved fresh, is;
  4. a refused sample rate retries once at the device's own rate;
  5. cleanup that hangs on a wedged close still returns within its timeout;
  6. the transport wires up recovering halves and reports their state;
  7. the audio library is enumerated only when its instance count reaches
     zero, so a reopen waits for the pool to be clear, never leaks an
     instance, cycles the peer half, and survives a terminate that wedges.

Run:  .venv/bin/python -m unittest tests.audio.test_recovering_transport
"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import StartFrame
from pipecat.transports.local.audio import LocalAudioTransportParams

from hearth.audio import recovering_transport as rt
from hearth.audio.device_pin import DevicePin
from hearth.audio.pa_pool import PyAudioPool
from hearth.audio.recovering_transport import (
    RecoveringInput,
    RecoveringLocalAudioTransport,
    RecoveringOutput,
)


def _entry(index, name, max_in, max_out, rate=48000.0):
    return {
        "index": index,
        "name": name,
        "maxInputChannels": max_in,
        "maxOutputChannels": max_out,
        "defaultSampleRate": rate,
    }


class _Stream:
    def __init__(self, kw):
        self.kw = kw
        self.calls = []
        self.close_ident = None
        self.close_gate: "threading.Event | None" = None

    def start_stream(self):
        self.calls.append("start_stream")

    def stop_stream(self):
        self.calls.append("stop_stream")

    def close(self):
        if self.close_gate is not None:
            self.close_gate.wait()
        self.close_ident = threading.get_ident()
        self.calls.append("close")

    def write(self, data):
        self.calls.append("write")


class _PA:
    def __init__(self, table, fail_open_rates=None, library=None):
        self._table = table
        self.fail_open_rates = set(fail_open_rates or ())
        self.library = library
        self.terminated = False
        self.open_calls = []
        self.opened = []

    def terminate(self):
        self.terminated = True
        if self.library is not None:
            self.library.on_terminate(self)

    def get_device_count(self):
        return len(self._table)

    def get_device_info_by_index(self, i):
        return self._table[i]

    def get_format_from_width(self, width):
        return 8

    def open(self, **kw):
        self.open_calls.append(kw)
        if kw.get("rate") in self.fail_open_rates:
            raise OSError("device refused this rate")
        stream = _Stream(kw)
        self.opened.append(stream)
        return stream


class _Library:
    """A count-aware stand-in for the audio library itself.

    The real one enumerates devices exactly once, when its instance count
    goes 0 -> 1; every instance made while the count is above zero inherits
    that snapshot. `pending_devices` is the world as it really is; it only
    becomes visible to an instance created at count zero.
    """

    def __init__(self, devices=()):
        self.pending_devices = list(devices)
        self.count = 0
        self.visible = []
        self.created = []
        self.fail_open_rates = set()
        self.terminate_gate = None  # set to an Event to wedge terminate()
        self.terminate_entered = threading.Event()

    def create(self):
        if self.count == 0:
            self.visible = list(self.pending_devices)  # the one enumeration
        self.count += 1
        pa = _PA(self.visible, fail_open_rates=self.fail_open_rates, library=self)
        self.created.append(pa)
        return pa

    def on_terminate(self, pa):
        self.terminate_entered.set()
        if self.terminate_gate is not None:
            self.terminate_gate.wait()
        self.count -= 1


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Sink:
    """Captures loguru lines for the duration of a with-block."""

    def __enter__(self):
        from loguru import logger

        self.lines = []
        self._logger = logger
        self._id = logger.add(lambda msg: self.lines.append(str(msg)))
        return self

    def __exit__(self, *exc):
        self._logger.remove(self._id)
        return False

    def count(self, fragment):
        return sum(1 for line in self.lines if fragment in line)


_START = StartFrame(audio_in_sample_rate=16000, audio_out_sample_rate=24000)
_MIC_PIN = DevicePin(uid="uid-mic", name="desk mic", direction="in", rate=48000)
_EARS_PIN = DevicePin(uid="uid-ears", name="desk ears", direction="out", rate=48000)


class _PoolCase(unittest.TestCase):
    """Every case gets its own pool: the real one is process-level state and
    would otherwise carry one test's live instance into the next."""

    def setUp(self):
        self.library = _Library()
        patcher = patch.object(rt, "_pa_pool", PyAudioPool())
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_library(self, library):
        """Point the pool at a count-aware fake library."""
        self.library = library
        patcher = patch.object(rt, "_pa_pool", PyAudioPool(factory=library.create))
        patcher.start()
        self.addCleanup(patcher.stop)
        return rt._pa_pool


async def _start_and_stop_watchdog(inst, frame):
    await inst.start(frame)
    if inst._watchdog is not None:
        inst._watchdog.cancel()
    return inst


class StartTests(_PoolCase):

    @patch("hearth.audio.recovering_transport.pin_default", new_callable=AsyncMock)
    def test_start_opens_the_pinned_device_by_explicit_index(self, pin_default):
        pin_default.return_value = _MIC_PIN
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())

        asyncio.run(_start_and_stop_watchdog(inst, _START))

        self.assertEqual(pa.opened[-1].kw["input_device_index"], 0)
        self.assertEqual(inst.state, "ok")

    @patch("hearth.audio.recovering_transport.pin_default", new_callable=AsyncMock)
    def test_unpinned_start_passes_no_index(self, pin_default):
        pin_default.return_value = None
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())

        asyncio.run(_start_and_stop_watchdog(inst, _START))

        self.assertIsNone(pa.opened[-1].kw["input_device_index"])
        self.assertEqual(inst.state, "unpinned")

        with patch(
            "hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock
        ) as uid_present:
            asyncio.run(inst._probe())
            uid_present.assert_not_called()


class LossDetectionTests(_PoolCase):

    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_input_gone_stale_is_lost_and_the_handle_is_abandoned(self, uid_present):
        uid_present.return_value = True
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "ok"
        stream = _Stream({})
        inst._in_stream = stream
        inst._last_activity_at = time.monotonic() - 10

        asyncio.run(inst._probe())

        self.assertEqual(inst.state, "lost")
        self.assertIsNone(inst._in_stream)

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and "close" not in stream.calls:
            time.sleep(0.01)
        self.assertIn("close", stream.calls)
        self.assertNotEqual(stream.close_ident, threading.get_ident())

    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_output_write_stuck_is_lost_and_the_executor_is_replaced(self, uid_present):
        uid_present.return_value = True
        pa = _PA([_entry(0, "desk ears", 0, 2)])
        inst = RecoveringOutput(pa, LocalAudioTransportParams())
        inst.pin = _EARS_PIN
        inst.state = "ok"
        inst._out_stream = _Stream({})
        inst._write_started_at = time.monotonic() - 10
        old_executor = inst._executor

        asyncio.run(inst._probe())

        self.assertEqual(inst.state, "lost")
        self.assertIsNone(inst._out_stream)
        self.assertIsNot(inst._executor, old_executor)


class GateTests(_PoolCase):

    @patch("hearth.audio.recovering_transport.pyaudio.PyAudio")
    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_absent_uid_never_reopens(self, uid_present, pyaudio_cls):
        fresh = _PA([_entry(0, "desk mic", 1, 0)])
        pyaudio_cls.return_value = fresh
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "lost"

        uid_present.return_value = False
        asyncio.run(inst._probe())
        self.assertEqual(fresh.open_calls, [])
        self.assertEqual(inst.state, "lost")

        uid_present.return_value = None
        asyncio.run(inst._probe())
        self.assertEqual(fresh.open_calls, [])
        self.assertEqual(inst.state, "lost")

    @patch("hearth.audio.recovering_transport.pyaudio.PyAudio")
    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_the_gate_a_different_device_on_the_old_index_is_never_opened(
        self, uid_present, pyaudio_cls
    ):
        uid_present.return_value = True
        fresh = _PA([_entry(0, "some other mic", 1, 0)])
        pyaudio_cls.return_value = fresh
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "lost"

        asyncio.run(inst._probe())

        self.assertEqual(fresh.open_calls, [])
        self.assertEqual(inst.state, "lost")

    @patch("hearth.audio.recovering_transport.pyaudio.PyAudio")
    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_same_name_back_on_a_new_index_reopens_there(self, uid_present, pyaudio_cls):
        uid_present.return_value = True
        fresh = _PA([_entry(0, "some other mic", 1, 0), _entry(1, "desk mic", 1, 0)])
        pyaudio_cls.return_value = fresh
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "lost"
        inst._sample_rate = 16000

        asyncio.run(inst._probe())

        self.assertEqual(len(fresh.open_calls), 1)
        self.assertEqual(fresh.open_calls[0]["input_device_index"], 1)
        self.assertEqual(inst.state, "recovered")
        self.assertIsNotNone(inst._in_stream)
        self.assertIs(inst._py_audio, fresh)

    @patch("hearth.audio.recovering_transport.pyaudio.PyAudio")
    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_pinned_rate_refused_retries_at_the_device_rate(self, uid_present, pyaudio_cls):
        uid_present.return_value = True
        fresh = _PA([_entry(0, "desk mic", 1, 0, rate=44100.0)], fail_open_rates={16000})
        pyaudio_cls.return_value = fresh
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "lost"
        inst._sample_rate = 16000

        asyncio.run(inst._probe())

        self.assertEqual(len(fresh.open_calls), 2)
        self.assertEqual(fresh.open_calls[0]["rate"], 16000)
        self.assertEqual(fresh.open_calls[1]["rate"], 44100)
        self.assertEqual(inst._sample_rate, 44100)
        self.assertEqual(inst.state, "recovered")


class CleanupTests(_PoolCase):

    def test_cleanup_that_hangs_returns_within_the_timeout(self):
        pa = _PA([_entry(0, "desk mic", 1, 0)])
        inst = RecoveringInput(pa, LocalAudioTransportParams())
        stream = _Stream({})
        stream.close_gate = threading.Event()
        inst._in_stream = stream

        sink = []
        sink_id = None
        try:
            from loguru import logger

            sink_id = logger.add(lambda msg: sink.append(str(msg)))

            started = time.monotonic()
            asyncio.run(inst.cleanup())
            elapsed = time.monotonic() - started
        finally:
            stream.close_gate.set()
            if sink_id is not None:
                logger.remove(sink_id)

        self.assertLess(elapsed, 4.0)
        self.assertIsNone(inst._in_stream)
        self.assertTrue(any("teardown abandoned" in line for line in sink))


class TransportTests(_PoolCase):

    @patch("hearth.audio.recovering_transport.pyaudio.PyAudio")
    def test_transport_builds_recovering_halves_and_reports_state(self, pyaudio_cls):
        pyaudio_cls.return_value = _PA([])
        transport = RecoveringLocalAudioTransport(LocalAudioTransportParams())

        self.assertIsInstance(transport.input(), RecoveringInput)
        self.assertIsInstance(transport.output(), RecoveringOutput)

        state = transport.audio_state()
        self.assertIn("input", state)
        self.assertIn("output", state)
        self.assertEqual(state["input"]["state"], "unpinned")
        self.assertEqual(state["output"]["state"], "unpinned")


class PoolTests(_PoolCase):
    """The audio library enumerates devices only at count 0 -> 1, so a reopen
    is worthless until every instance has terminated. These prove the pool
    that makes that true — and that nothing waits on a call that can wedge."""

    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_a_device_back_while_the_old_instance_lives_waits_for_the_release(self, uid_present):
        # mutation: delete the `not _pa_pool.clear` guard at the top of
        # _try_reopen (or the release in the loss path) — a reopen is then
        # attempted against the device list from before the loss.
        uid_present.return_value = True
        library = _Library()  # the mic is not in the world right now
        pool = self.use_library(library)
        shared = library.create()  # the start-time instance both halves hold
        library.terminate_gate = threading.Event()
        self.addCleanup(library.terminate_gate.set)

        inst = RecoveringInput(shared, LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "ok"
        inst._sample_rate = 16000
        inst._in_stream = _Stream({})
        inst._last_activity_at = time.monotonic() - 10

        with _Sink() as sink:
            asyncio.run(inst._probe())
            self.assertEqual(inst.state, "lost")

            library.pending_devices = [_entry(0, "desk mic", 1, 0)]  # it comes back
            self.assertTrue(_wait_for(library.terminate_entered.is_set))

            asyncio.run(inst._probe())
            asyncio.run(inst._probe())

            self.assertEqual(inst.state, "lost")
            self.assertEqual(len(library.created), 1)  # nothing new was made
            self.assertEqual(sink.count("has not released yet"), 1)  # said once

            library.terminate_gate.set()
            self.assertTrue(_wait_for(lambda: pool.clear and not inst._release_pending))
            asyncio.run(inst._probe())

        self.assertEqual(inst.state, "recovered")
        self.assertEqual(len(library.created), 2)
        self.assertEqual(library.count, 1)
        self.assertEqual(inst._in_stream.kw["input_device_index"], 0)
        self.assertIs(inst._py_audio, library.created[-1])

    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_b_a_reopen_whose_open_raises_gives_its_instance_back(self, uid_present):
        # mutation: delete `self._release_unless_shared(pa)` from the failure
        # tail of _try_reopen — the count never returns to zero and every
        # later attempt is refused for the rest of the sitting.
        uid_present.return_value = True
        library = _Library([_entry(0, "desk mic", 1, 0)])
        library.fail_open_rates = {16000, 48000}  # both rates refused
        pool = self.use_library(library)

        inst = RecoveringInput(_PA([]), LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "lost"
        inst._sample_rate = 16000

        asyncio.run(inst._probe())

        self.assertEqual(inst.state, "lost")
        self.assertEqual(len(library.created), 1)
        self.assertTrue(_wait_for(lambda: pool.clear))
        self.assertEqual(library.count, 0)  # back to where it started

        library.fail_open_rates = set()  # the device is really back now
        asyncio.run(inst._probe())

        self.assertEqual(inst.state, "recovered")
        self.assertEqual(len(library.created), 2)
        self.assertEqual(inst._in_stream.kw["input_device_index"], 0)

    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_c_one_sided_loss_cycles_the_peer_and_each_reopens_on_its_own_pin(self, uid_present):
        # mutation: delete the peer cycle from _on_lost — the output half
        # keeps the stale instance alive, so the count never reaches zero and
        # neither half ever reopens.
        uid_present.return_value = True
        library = _Library([_entry(0, "desk mic", 1, 0), _entry(1, "desk ears", 0, 2)])
        pool = self.use_library(library)

        with patch.object(rt.pyaudio, "PyAudio", library.create):
            transport = RecoveringLocalAudioTransport(LocalAudioTransportParams())
        half_in = transport.input()
        half_out = transport.output()

        half_in.pin = _MIC_PIN
        half_in.state = "ok"
        half_in._sample_rate = 16000
        half_in._in_stream = _Stream({})
        half_in._last_activity_at = time.monotonic() - 10  # the mic went stale

        half_out.pin = _EARS_PIN
        half_out.state = "ok"
        half_out._sample_rate = 24000
        half_out._out_stream = _Stream({})  # the ears are perfectly fine

        with _Sink() as sink:
            asyncio.run(half_in._probe())

            self.assertEqual(half_in.state, "lost")
            self.assertEqual(half_out.state, "lost")  # taken down with it
            self.assertEqual(sink.count("cycled with the in device"), 1)
            self.assertIsNone(half_out._out_stream)

            self.assertTrue(
                _wait_for(
                    lambda: pool.clear
                    and not half_in._release_pending
                    and not half_out._release_pending
                )
            )
            asyncio.run(half_in._probe())
            asyncio.run(half_out._probe())

        self.assertEqual(half_in.state, "recovered")
        self.assertEqual(half_out.state, "recovered")
        self.assertEqual(len(library.created), 2)  # one at start, one to recover on
        self.assertIs(half_in._py_audio, half_out._py_audio)

        recovery = library.created[-1]
        self.assertEqual(len(recovery.open_calls), 2)
        self.assertEqual(half_in._in_stream.kw["input_device_index"], 0)  # its own pin
        self.assertEqual(half_out._out_stream.kw["output_device_index"], 1)  # its own pin
        self.assertNotIn("output_device_index", half_in._in_stream.kw)
        self.assertNotIn("input_device_index", half_out._out_stream.kw)

    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_d_a_terminate_that_wedges_says_so_once_and_stays_lost(self, uid_present):
        # mutation: run terminate() inline in PyAudioPool.release instead of
        # on its own daemon thread — this test then wedges the loop forever
        # instead of reporting and carrying on.
        uid_present.return_value = True
        library = _Library()
        pool = self.use_library(library)
        shared = library.create()
        library.terminate_gate = threading.Event()
        self.addCleanup(library.terminate_gate.set)

        inst = RecoveringInput(shared, LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "ok"
        inst._sample_rate = 16000
        inst._in_stream = _Stream({})
        inst._last_activity_at = time.monotonic() - 10

        with _Sink() as sink:
            asyncio.run(inst._probe())
            self.assertTrue(_wait_for(library.terminate_entered.is_set))

            library.pending_devices = [_entry(0, "desk mic", 1, 0)]
            inst._lost_at = time.monotonic() - 10  # well past CLEANUP_TIMEOUT_S
            for _ in range(4):
                asyncio.run(inst._probe())

            self.assertEqual(inst.state, "lost")
            self.assertEqual(len(library.created), 1)  # no instance made
            self.assertEqual(library.count, 1)
            self.assertEqual(sink.count("did not release within"), 1)
            self.assertEqual(sink.count("has not released yet"), 1)

            library.terminate_gate.set()  # it comes back from the dead
            self.assertTrue(_wait_for(lambda: pool.clear and not inst._release_pending))
            asyncio.run(inst._probe())

        self.assertEqual(inst.state, "recovered")
        self.assertEqual(inst._in_stream.kw["input_device_index"], 0)

    @patch("hearth.audio.recovering_transport.uid_present", new_callable=AsyncMock)
    def test_e_the_reopen_failure_line_carries_the_exception(self, uid_present):
        # mutation: drop the `({exc!r})` from the warning in _try_reopen —
        # the line a log shows hundreds of times stops saying why.
        uid_present.return_value = True
        library = _Library([_entry(0, "desk mic", 1, 0)])
        library.fail_open_rates = {16000, 48000}
        self.use_library(library)

        inst = RecoveringInput(_PA([]), LocalAudioTransportParams())
        inst.pin = _MIC_PIN
        inst.state = "lost"
        inst._sample_rate = 16000

        with _Sink() as sink:
            asyncio.run(inst._probe())

        self.assertEqual(sink.count("device reopen failed"), 1)
        self.assertTrue(any("device refused this rate" in line for line in sink.lines))


if __name__ == "__main__":
    unittest.main()
