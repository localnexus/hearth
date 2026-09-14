"""test_voice_loudness.py — the per-voice `loudness` key (S2-voice-loudness).

Proves, on the real package:
  1. `_to_pcm` (pure numpy, no model): gain 1.0/2.0/0.5 scale the samples before
     the int16 clip; the clipped-sample count is reported accurately.
  2. `load_active` resolves a voice.toml's `loudness` into `ActiveConfig.loudness`,
     defaulting to 1.0 when the key is absent.
  3. The registry lock: `loudness` is declared on the voice-file schema with
     default 1.0, and an out-of-range value is a lenient WARN at load time but a
     strict ERROR under `python -m hearth.config.check`.

Run:  .venv/bin/python -m unittest tests.test_voice_loudness
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from hearth.config import config_loader
from hearth.config import settings_registry as sr
from hearth.tts.mlx_tts_service import _to_pcm


class ToPcm(unittest.TestCase):
    def test_gain_one_matches_today(self):
        arr = np.array([0.1, -0.2, 0.5, -0.9, 0.0], dtype=np.float32)
        expected = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        pcm, clipped = _to_pcm(arr, 1.0)
        self.assertEqual(pcm, expected)
        self.assertEqual(clipped, 0)
        self.assertEqual(len(pcm), len(arr) * 2)

    def test_gain_two_doubles_and_clips(self):
        arr = np.array([0.1, -0.2, 0.5, -0.9, 0.0], dtype=np.float32)
        doubled = arr * 2.0
        expected = (np.clip(doubled, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        expected_clipped = int(np.count_nonzero(np.abs(doubled) > 1.0))
        pcm, clipped = _to_pcm(arr, 2.0)
        self.assertEqual(pcm, expected)
        self.assertEqual(clipped, expected_clipped)

    def test_gain_half_halves(self):
        arr = np.array([0.4, -0.8, 1.0, -0.6], dtype=np.float32)
        expected = (np.clip(arr * 0.5, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        pcm, clipped = _to_pcm(arr, 0.5)
        self.assertEqual(pcm, expected)
        self.assertEqual(clipped, 0)
        self.assertEqual(len(pcm), len(arr) * 2)

    def test_dtype_is_int16(self):
        arr = np.array([0.3, -0.3], dtype=np.float32)
        pcm, _ = _to_pcm(arr, 1.0)
        self.assertEqual(np.frombuffer(pcm, dtype="<i2").dtype, np.dtype("<i2"))

    def test_clip_count_exact(self):
        # 3 of 5 samples exceed magnitude 1.0 after doubling (0.6, 0.9, -0.7 -> 1.2, 1.8, -1.4);
        # the other 2 (0.1, -0.2 -> 0.2, -0.4) stay in range.
        arr = np.array([0.6, 0.9, -0.7, 0.1, -0.2], dtype=np.float32)
        _, clipped = _to_pcm(arr, 2.0)
        self.assertEqual(clipped, 3)

    def test_clip_count_zero_in_range_at_gain_one(self):
        arr = np.array([0.1, -0.2, 0.9, -0.99], dtype=np.float32)
        _, clipped = _to_pcm(arr, 1.0)
        self.assertEqual(clipped, 0)


def _build_install(root: Path, *, loudness_line: str) -> None:
    """One character, one model, one voice — enough for load_active()."""
    char = root / "characters" / "zz-char"
    (char / "voices" / "zz-voice").mkdir(parents=True)
    char.joinpath("persona.md").write_text("## IDENTITY\nzz identity\n## SOUL\nzz soul\n")
    char.joinpath("voices", "zz-voice", "sample.wav").write_bytes(b"RIFFfake")
    char.joinpath("voices", "zz-voice", "voice.toml").write_text(
        f'tag = "zz-voice"\nref_wav = "sample.wav"\n{loudness_line}'
    )
    model_dir = root / "config" / "models" / "zz-model"
    model_dir.mkdir(parents=True)
    model_dir.joinpath("model.toml").write_text(
        'id = "zz-id"\ntemperature = 0.7\nreasoning_effort = "none"\n'
    )
    model_dir.joinpath("system-prompt-template.md").write_text("SYS: {{persona}}")
    active = root / "config" / "active.toml"
    active.write_text('character = "zz-char"\nmodel = "zz-model"\nvoice = "zz-voice"\n')


class LoadActiveLoudness(unittest.TestCase):
    def _load(self, loudness_line: str):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _build_install(root, loudness_line=loudness_line)
            active = root / "config" / "active.toml"
            with mock.patch.object(config_loader, "_DATA", root), \
                 mock.patch.object(config_loader, "DATA_DIR", root), \
                 mock.patch.object(config_loader, "ACTIVE_TOML", active):
                return config_loader.load_active()

    def test_loudness_from_voice_toml(self):
        active = self._load("loudness = 1.5\n")
        self.assertEqual(active.loudness, 1.5)

    def test_loudness_defaults_to_one_without_the_key(self):
        active = self._load("")
        self.assertEqual(active.loudness, 1.0)


class RegistryLock(unittest.TestCase):
    def test_the_key_is_declared_in_the_registry(self):
        """Undeclared, the settings check would flag every voice.toml with a
        loudness key as carrying an unknown key."""
        errors, warnings = sr.strict_check("voice", {"tag": "t", "ref_wav": "x.wav", "loudness": 1.0})
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(sr.VoiceFile.model_fields["loudness"].default, 1.0)

    def test_out_of_range_is_a_strict_error(self):
        errors, _ = sr.strict_check("voice", {"tag": "t", "ref_wav": "x.wav", "loudness": 9})
        self.assertTrue(errors, "the strict check must reject loudness above the 4.0 ceiling")

    def test_out_of_range_only_warns_at_load_time(self):
        """`_schema_check` is lenient at load time by design: an out-of-range
        value only WARNs — a boot that works today must keep working."""
        notes = sr.loader_check("voice", {"tag": "t", "ref_wav": "x.wav", "loudness": 9})
        self.assertTrue(notes, "the lenient loader path must still surface the note")


if __name__ == "__main__":
    unittest.main()
