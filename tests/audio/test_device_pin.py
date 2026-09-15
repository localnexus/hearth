"""test_device_pin.py — pinning a direction's default device by identity, and
resolving that pin back to a live PortAudio index by name.

Proves:
  1. a pin is built from the helper's own default-uid + device table;
  2. a helper that is down, or a default with no uid, yields no pin — silently;
  3. a name match resolves to an index only when it is unique AND in-direction;
  4. presence of a uid in the current device table is honestly tri-state.

Run:  .venv/bin/python -m unittest tests.audio.test_device_pin
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from hearth.audio.device_pin import DevicePin, pin_default, resolve_index, uid_present

_DEVICES = {
    "ok": True,
    "default_input_uid": "uid-mic",
    "default_output_uid": "uid-ears",
    "devices": [
        {"uid": "uid-mic", "name": "desk mic", "in": 1, "out": 0, "rate": 48000},
        {"uid": "uid-ears", "name": "desk ears", "in": 0, "out": 2, "rate": 44100},
        {"uid": "uid-loop", "name": "loop", "in": 2, "out": 2, "rate": 48000},
    ],
}


class _PA:
    def __init__(self, table):
        self._table = table

    def get_device_count(self):
        return len(self._table)

    def get_device_info_by_index(self, i):
        return self._table[i]


def _entry(index, name, max_in, max_out, rate=48000.0):
    return {
        "index": index,
        "name": name,
        "maxInputChannels": max_in,
        "maxOutputChannels": max_out,
        "defaultSampleRate": rate,
    }


class PinDefaultTests(unittest.TestCase):

    @patch("hearth.recording.recording.run_route", new_callable=AsyncMock)
    def test_pin_default_input_from_the_helper(self, run_route):
        run_route.return_value = _DEVICES
        pin = asyncio.run(pin_default("in"))
        self.assertEqual(pin, DevicePin(uid="uid-mic", name="desk mic", direction="in", rate=48000))

    @patch("hearth.recording.recording.run_route", new_callable=AsyncMock)
    def test_pin_default_output_from_the_helper(self, run_route):
        run_route.return_value = _DEVICES
        pin = asyncio.run(pin_default("out"))
        self.assertEqual(pin, DevicePin(uid="uid-ears", name="desk ears", direction="out", rate=44100))

    @patch("hearth.recording.recording.run_route", new_callable=AsyncMock)
    def test_no_pin_when_the_helper_is_down(self, run_route):
        run_route.return_value = None
        self.assertIsNone(asyncio.run(pin_default("in")))

    @patch("hearth.recording.recording.run_route", new_callable=AsyncMock)
    def test_no_pin_when_the_default_has_no_uid(self, run_route):
        run_route.return_value = {**_DEVICES, "default_input_uid": None}
        self.assertIsNone(asyncio.run(pin_default("in")))

    @patch("hearth.recording.recording.run_route", new_callable=AsyncMock)
    def test_uid_present_true_false_and_unknown(self, run_route):
        run_route.return_value = _DEVICES
        self.assertTrue(asyncio.run(uid_present("uid-mic")))
        self.assertFalse(asyncio.run(uid_present("uid-nowhere")))
        run_route.return_value = None
        self.assertIsNone(asyncio.run(uid_present("uid-mic")))


class ResolveIndexTests(unittest.TestCase):

    def test_resolve_unique_name_in_direction(self):
        # "some other mic" has the right channels and the wrong name: only the name check keeps it out.
        pa = _PA([_entry(0, "desk mic", 1, 0), _entry(1, "desk ears", 0, 2), _entry(2, "some other mic", 1, 0)])
        pin = DevicePin(uid="uid-mic", name="desk mic", direction="in", rate=48000)
        self.assertEqual(resolve_index(pa, pin), 0)

    def test_resolve_refuses_an_ambiguous_name(self):
        pa = _PA([_entry(0, "desk mic", 1, 0), _entry(1, "desk mic", 1, 0)])
        pin = DevicePin(uid="uid-mic", name="desk mic", direction="in", rate=48000)
        self.assertIsNone(resolve_index(pa, pin))

    def test_resolve_refuses_an_absent_name(self):
        # an input device exists, but under another name: name equality alone must refuse it
        pa = _PA([_entry(0, "desk ears", 0, 2), _entry(1, "some other mic", 1, 0)])
        pin = DevicePin(uid="uid-mic", name="desk mic", direction="in", rate=48000)
        self.assertIsNone(resolve_index(pa, pin))

    def test_resolve_filters_by_direction(self):
        pa = _PA([_entry(0, "desk mic", 0, 2)])
        pin = DevicePin(uid="uid-mic", name="desk mic", direction="in", rate=48000)
        self.assertIsNone(resolve_index(pa, pin))


if __name__ == "__main__":
    unittest.main()
