"""test_settings_registry.py — the settings registry (schema-driven settings, step 1).

Proves, on the real artifacts:
  1. COVERAGE  — every shipped config file/example validates strictly clean
                 (zero errors, zero warnings): a key cannot exist without appearing.
  2. PARITY    — the registry agrees with every live honored-surface constant it
                 mirrors (config_knobs._SCHEMA, config_reload._ENGINE_LIVE_KEYS +
                 _VAD_FALLBACK, tag_profiles.TEMP_CEILING/_ALLOWED_KNOBS,
                 paralinguistics._CANONICAL, tts_prep._SPEECH_KNOBS, the shipped
                 tts.toml [live] values). Divergence = red suite, not silent drift.
  3. BEHAVIOR  — lenient vs strict posture: type violations raise SchemaError;
                 unknown keys and out-of-range values warn (lenient) / bind (strict);
                 missing required keys stay the loaders' _require contract.
  4. LOADER    — config_loader._schema_check wiring: a type-bad model.toml raises
                 ConfigError naming the file; an unknown key only warns.
  5. SYNC      — the generated settings reference on disk matches generate_manual().

Run:  .venv/bin/python -m unittest tests.test_settings_registry
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from typing import Union, get_args, get_origin

from hearth.config import config_loader as cl
from hearth.config import config_reload as cr
from hearth.config import settings_registry as sr
from hearth.control.features import config_knobs as ck
from hearth.serve import tts_prep
from hearth.tts import paralinguistics, tag_profiles

_ROOT = cl._ROOT
_PY = sys.executable


def _bounds(model, name):
    lo = hi = None
    for m in model.model_fields[name].metadata:
        lo = getattr(m, "ge", None) if getattr(m, "ge", None) is not None else lo
        hi = getattr(m, "le", None) if getattr(m, "le", None) is not None else hi
    return lo, hi


def _is_int(model, name) -> bool:
    ann = model.model_fields[name].annotation
    return ann is int or int in get_args(ann)


def _literal_values(model, name) -> set:
    ann = model.model_fields[name].annotation
    if get_origin(ann) in (types.UnionType, Union):
        for a in get_args(ann):
            if get_origin(a) is not None:
                ann = a
                break
    return set(get_args(ann))


class ShippedFilesValidateClean(unittest.TestCase):
    """Every shipped config file/example passes strict validation with zero notes."""

    SHIPPED = [
        ("active", "config/active.toml.example", None),
        ("model", "config/models/example/model.toml.example", None),
        ("voice", "characters/example/voices/default/voice.toml", None),
        ("overrides", "config/overrides.toml.example", None),
        ("tts-baseline", "config/tts/chatterbox-turbo/tts.toml", None),
        ("vad", "config/vad.toml", None),
        ("serve", "config/serve.toml.example", "serve"),
        ("memory", "config/memory.toml.example", "memory"),
    ]

    def test_shipped_files(self):
        for kind, rel, top in self.SHIPPED:
            with self.subTest(file=rel):
                path = _ROOT / rel
                self.assertTrue(path.is_file(), f"missing shipped file {path}")
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                if top:
                    data = data[top]
                errors, warnings = sr.strict_check(kind, data)
                self.assertEqual(errors, [], f"{rel}: {errors}")
                self.assertEqual(warnings, [], f"{rel}: {warnings}")


class ParityWithLiveModules(unittest.TestCase):
    """The registry mirrors every honored-surface constant it documents."""

    def test_knob_schema_llm(self):
        s = ck._SCHEMA["llm"]
        self.assertEqual((_bounds(sr._OvLLM, "temperature")),
                         (s["temperature"]["min"], s["temperature"]["max"]))
        self.assertEqual(_literal_values(sr._OvLLM, "reasoning_effort"),
                         set(s["reasoning_effort"]["values"]))
        self.assertEqual(sr._OvLLM.model_fields["persona"].metadata[0].max_length,
                         s["persona"]["max_len"])

    def test_knob_schema_tts_vad(self):
        for section, model in (("tts", sr._OvTTS), ("vad", sr._OvVAD)):
            ranges = ck._TTS_RANGES if section == "tts" else ck._VAD_RANGES
            self.assertEqual(set(model.model_fields), set(ranges), section)
            for key, (lo, hi, integer) in ranges.items():
                self.assertEqual(_bounds(model, key), (lo, hi), f"{section}.{key}")
                self.assertEqual(_is_int(model, key), integer, f"{section}.{key}")

    def test_engine_live_keys(self):
        self.assertEqual(sr.TURBO_LIVE_KNOBS, cr._ENGINE_LIVE_KEYS["chatterbox-turbo"])
        self.assertEqual(sr.TURBO_LIVE_KNOBS, set(sr._OvTTS.model_fields))
        self.assertEqual(sr.TURBO_LIVE_KNOBS, tag_profiles._ALLOWED_KNOBS["chatterbox-turbo"])

    def test_vad_fallback(self):
        defaults = {k: sr._VadLive.model_fields[k].default for k in sr._VadLive.model_fields}
        self.assertEqual(defaults, cr._VAD_FALLBACK)

    def test_tts_baseline_defaults_match_shipped_file(self):
        live = cr.load_tts_baseline("chatterbox-turbo")
        defaults = {k: sr._TtsLive.model_fields[k].default for k in sr._TtsLive.model_fields}
        self.assertEqual(defaults, live)

    def test_tag_ceiling_and_canonical_tags(self):
        self.assertEqual(sr.TEMP_CEILING, tag_profiles.TEMP_CEILING)
        self.assertEqual({f"[{t}]" for t in sr.CANONICAL_TAGS}, paralinguistics._CANONICAL)

    def test_serve_speech_knobs(self):
        self.assertEqual(sr.SERVE_SPEECH_KNOBS, tts_prep._SPEECH_KNOBS)
        self.assertEqual(set(sr._ServeIdentityTts.model_fields),
                         sr.SERVE_SPEECH_KNOBS | {"allow_tag_profiles"})

    def test_gate_defaults_spot_checks(self):
        self.assertEqual(sr.ServeTable.model_fields["port"].default, 65001)
        self.assertEqual(sr.ServeTable.model_fields["token_source"].default, "config/serve-token")
        self.assertEqual(sr._MemServe.model_fields["idle_close_voice"].default, 5)
        self.assertEqual(sr._MemServe.model_fields["idle_close_chat"].default, 480)
        self.assertEqual(sr.MemoryTable.model_fields["recall_limit"].default, 6)
        self.assertEqual(sr.OpenclawTable.model_fields["quick_wait_s"].default, 8.0)


class ValidationBehavior(unittest.TestCase):
    def test_type_violation_raises(self):
        with self.assertRaises(sr.SchemaError):
            sr.loader_check("active", {"character": 123, "model": "m", "voice": "v"})

    def test_unknown_key_warns(self):
        w = sr.loader_check("active", {"character": "a", "model": "m", "voice": "v", "zzz": 1})
        self.assertTrue(any("unknown key 'zzz'" in x for x in w), w)

    def test_range_and_enum_warn_lenient(self):
        w = sr.loader_check("model", {"id": "x", "temperature": 9.9, "reasoning_effort": "nope"})
        self.assertEqual(len([x for x in w if "temperature" in x]), 1, w)
        self.assertEqual(len([x for x in w if "reasoning_effort" in x]), 1, w)

    def test_missing_is_silent_lenient_but_binds_strict(self):
        self.assertEqual([x for x in sr.loader_check("model", {"id": "x"}) if "missing" in x], [])
        errors, _ = sr.strict_check("model", {"id": "x"})
        self.assertTrue(any("missing required key" in e for e in errors), errors)

    def test_range_binds_strict(self):
        errors, _ = sr.strict_check("model",
                                    {"id": "x", "temperature": 9.9, "reasoning_effort": "none"})
        self.assertTrue(any("temperature" in e for e in errors), errors)

    def test_nested_unknown_keys(self):
        w = sr.loader_check("serve", {"enabled": True, "identity":
                                      {"character": "a", "voice": "b", "tts": {"exaggeration": 1}}})
        self.assertTrue(any("identity.tts.exaggeration" in x for x in w), w)
        w = sr.loader_check("tts-baseline", {"tag_profiles": {"happy": {"bogus": 1}}})
        self.assertTrue(any("tag_profiles.happy.bogus" in x for x in w), w)

    def test_non_canonical_tag_warns(self):
        _, w = sr.strict_check("tts-baseline", {"tag_profiles": {"smirk": {"temperature": 1.0}}})
        self.assertTrue(any("not a canonical tag" in x for x in w), w)


class LoaderIntegration(unittest.TestCase):
    """config_loader._schema_check wiring, via subprocess (fresh anchors)."""

    def _run(self, code: str, **env) -> subprocess.CompletedProcess:
        e = dict(os.environ)
        e.pop("HEARTH_DATA", None)
        e.pop("HEARTH_ROOT", None)
        e.update(env)
        e["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([_PY, "-c", code], capture_output=True, text=True,
                              env=e, cwd=str(_ROOT))

    def test_type_bad_model_toml_raises_config_error(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = Path(d) / "config" / "models" / "bad"
            mdir.mkdir(parents=True)
            (mdir / "model.toml").write_text(
                'id = "x"\ntemperature = "abc"\nreasoning_effort = "none"\n')
            r = self._run(
                "import hearth.config.config_loader as c\n"
                "try:\n    c.load_model('bad')\nexcept c.ConfigError as e:\n"
                "    print('CONFIGERROR', e)\n", HEARTH_DATA=d)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn("CONFIGERROR", r.stdout)
            self.assertIn("invalid value in", r.stdout)
            self.assertIn("temperature", r.stdout)

    def test_unknown_key_warns_but_loads(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = Path(d) / "config" / "models" / "odd"
            mdir.mkdir(parents=True)
            (mdir / "model.toml").write_text(
                'id = "x"\ntemperature = 0.7\nreasoning_effort = "none"\nzzz_custom = 1\n')
            r = self._run(
                "import hearth.config.config_loader as c\n"
                "print('ID', c.load_model('odd')['id'])\n", HEARTH_DATA=d)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn("ID x", r.stdout)
            self.assertIn("unknown key 'zzz_custom'", r.stderr)


class GeneratedArtifacts(unittest.TestCase):
    def test_json_schema_bundle(self):
        js = sr.json_schema()
        self.assertEqual(set(js), set(sr.REGISTRY))
        x = js["model"]["schema"]["properties"]["temperature"]["x-hearth"]
        self.assertEqual(x["hot_via"], "llm.temperature")

    def test_manual_in_sync(self):
        dirs = [
            _ROOT / "docs" / "config-manual",
            _ROOT.parent / "docs" / "reference" / "config-manual",
        ]
        home = next((d for d in dirs if d.is_dir()), None)
        self.assertIsNotNone(home, f"config-manual home not found at {dirs}")
        for fname, text in sr.generate_manual_pages().items():
            with self.subTest(page=fname):
                page = home / fname
                self.assertTrue(page.is_file(), f"generated page missing: {page}")
                self.assertEqual(page.read_text(encoding="utf-8"), text,
                                 f"{page} is stale — regenerate: "
                                 "python -m hearth.config.check --emit-manual <dir>")


if __name__ == "__main__":
    unittest.main()
