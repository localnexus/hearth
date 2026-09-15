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
  6. the transport wires up recovering halves and reports their state.

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

from hearth.audio.device_pin import DevicePin
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
    def __init__(self, table, fail_open_rates=None):
        self._table = table
        self.fail_open_rates = fail_open_rates or set()
        self.open_calls = []
        self.opened = []

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


_START = StartFrame(audio_in_sample_rate=16000, audio_out_sample_rate=24000)
_MIC_PIN = DevicePin(uid="uid-mic", name="desk mic", direction="in", rate=48000)
_EARS_PIN = DevicePin(uid="uid-ears", name="desk ears", direction="out", rate=48000)


async def _start_and_stop_watchdog(inst, frame):
    await inst.start(frame)
    if inst._watchdog is not None:
        inst._watchdog.cancel()
    return inst


class StartTests(unittest.TestCase):

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


class LossDetectionTests(unittest.TestCase):

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


class GateTests(unittest.TestCase):

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


class CleanupTests(unittest.TestCase):

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


class TransportTests(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
