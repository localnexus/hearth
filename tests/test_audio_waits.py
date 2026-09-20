"""config/audio.toml — the remote route's waits as a settings file, and the two
env words the facade derives from it at every conversation start.

The precedence is the whole point: a DATA-root file wins; without one the
operator's env block (or the built-in default) stands, and the shipped template
in the engine tree is never treated as live.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth.config import config_loader


class TheWaitsFile(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.data = base / "data" / "config"
        self.data.mkdir(parents=True)
        self.root = base / "engine" / "config"
        self.root.mkdir(parents=True)
        for name, value in (("DATA_DIR", base / "data"), ("_ROOT", base / "engine"),
                            ("CONFIG_DIR", self.data),
                            ("AUDIO_TOML", self.data / "audio.toml")):
            p = mock.patch.object(config_loader, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_no_data_root_file_means_no_words(self):
        (self.root / "audio.toml").write_text("[audio]\nlost_device_wait_min = 1\n")
        self.assertIsNone(config_loader.load_audio_waits())
        self.assertEqual(config_loader.audio_wait_env(), {})

    def test_the_file_sets_both_words_in_seconds(self):
        (self.data / "audio.toml").write_text(
            "[audio]\nlost_device_wait_min = 15\narrival_wait_min = 2\n")
        self.assertEqual(config_loader.load_audio_waits(),
                         {"lost_device_wait_min": 15, "arrival_wait_min": 2})
        self.assertEqual(config_loader.audio_wait_env(),
                         {"HEARTH_AUDIO_GRACE_S": "900", "HEARTH_AUDIO_START_WAIT_S": "120"})

    def test_a_missing_key_takes_the_default_and_the_ceiling_is_999(self):
        (self.data / "audio.toml").write_text("[audio]\nlost_device_wait_min = 999\n")
        self.assertEqual(config_loader.audio_wait_env(),
                         {"HEARTH_AUDIO_GRACE_S": "59940", "HEARTH_AUDIO_START_WAIT_S": "180"})

    def test_a_wrong_shaped_value_is_skipped_never_guessed(self):
        (self.data / "audio.toml").write_text(
            '[audio]\nlost_device_wait_min = "soon"\narrival_wait_min = 4\n')
        with self.assertRaises(config_loader.ConfigError):
            config_loader.audio_wait_env()      # a type violation fails fast, naming the file
        (self.data / "audio.toml").write_text("[audio]\nlost_device_wait_min = 0\narrival_wait_min = 4\n")
        self.assertEqual(config_loader.audio_wait_env(), {"HEARTH_AUDIO_START_WAIT_S": "240"})

    def test_a_file_without_the_table_is_a_config_error(self):
        (self.data / "audio.toml").write_text("lost_device_wait_min = 3\n")
        with self.assertRaises(config_loader.ConfigError):
            config_loader.load_audio_waits()


if __name__ == "__main__":
    unittest.main()
