"""test_settings_registry.py — the settings registry (schema-driven settings, step 1).

Proves, on the real artifacts:
  1. COVERAGE  — every shipped config file/example validates strictly clean
                 (zero errors, zero warnings): a key cannot exist without appearing.
  2. DERIVATION — the live honored surfaces DERIVE from the registry (derive-knobs
                 stroke 2026-09-01: config_knobs schema/ranges, config_reload
                 _ENGINE_LIVE_KEYS + _VAD_FALLBACK, tag_profiles TEMP_CEILING/
                 _ALLOWED_KNOBS, tts_prep._SPEECH_KNOBS; the tag vocabulary flows
                 the other way, paralinguistics → registry). The ear-verified
                 content values are pinned here — still red on drift, one source.
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


class DerivedSurfaces(unittest.TestCase):
    """The live modules DERIVE their knob surfaces from the registry (derive-knobs
    stroke 2026-09-01 — the step-1 hand-sync parity pins retired). The ear-verified
    content values are pinned HERE, so an accidental registry edit still turns the
    suite red; the derivation checks prove the modules share the registry's
    surfaces instead of keeping copies."""

    def test_content_pins(self):
        self.assertEqual(sr.TEMP_CEILING, 1.4)
        self.assertEqual(sr.ENGINE_LIVE_KNOBS["chatterbox-turbo"],
                         {"temperature", "top_p", "top_k", "repetition_penalty"})
        self.assertEqual(sr.ENGINE_LIVE_KNOBS["chatterbox"],
                         {"temperature", "top_p", "top_k", "repetition_penalty",
                          "exaggeration", "cfg_weight"})
        self.assertEqual(sr.SERVE_SPEECH_KNOBS,
                         {"temperature", "top_p", "top_k", "repetition_penalty", "speed"})
        self.assertEqual(sr.vad_fallback(),
                         {"confidence": 0.7, "start_secs": 0.2, "stop_secs": 0.5,
                          "min_volume": 0.6})
        self.assertEqual(sr.CANONICAL_TAGS, {
            "laugh", "chuckle", "sigh", "gasp", "groan", "sniff", "cough", "shush",
            "clear throat", "whispering", "angry", "happy", "sarcastic", "crying",
            "surprised", "fear", "dramatic", "narration", "advertisement"})
        self.assertEqual(sr.live_knob_ranges("tts"), {
            "temperature": (0.0, 2.0, False), "top_p": (0.0, 1.0, False),
            "top_k": (1, 10_000, True), "repetition_penalty": (0.5, 5.0, False)})
        self.assertEqual(sr.live_knob_ranges("vad"), {
            "confidence": (0.0, 1.0, False), "start_secs": (0.05, 1.0, False),
            "stop_secs": (0.2, 3.0, False), "min_volume": (0.0, 1.0, False)})
        facts = sr.llm_knob_facts()
        self.assertEqual(facts["temperature"], (0.0, 2.0))
        self.assertEqual(facts["reasoning_effort"], {"none", "low", "medium", "high"})
        self.assertEqual(facts["persona_max_len"], 16_000)

    def test_modules_derive_from_registry(self):
        self.assertEqual(cr._ENGINE_LIVE_KEYS, sr.ENGINE_LIVE_KNOBS)
        self.assertEqual(cr._VAD_FALLBACK, sr.vad_fallback())
        self.assertEqual(tag_profiles._ALLOWED_KNOBS, sr.ENGINE_LIVE_KNOBS)
        self.assertEqual(tag_profiles.TEMP_CEILING, sr.TEMP_CEILING)
        self.assertIs(tts_prep._SPEECH_KNOBS, sr.SERVE_SPEECH_KNOBS)
        self.assertEqual(ck._TTS_RANGES, sr.live_knob_ranges("tts"))
        self.assertEqual(ck._VAD_RANGES, sr.live_knob_ranges("vad"))
        # The one reversed derivation: vocabulary flows paralinguistics → registry.
        self.assertEqual({f"[{t}]" for t in sr.CANONICAL_TAGS}, paralinguistics._CANONICAL)

    def test_knob_schema_reflects_registry(self):
        s = ck._SCHEMA["llm"]
        self.assertEqual(_bounds(sr._OvLLM, "temperature"),
                         (s["temperature"]["min"], s["temperature"]["max"]))
        self.assertEqual(_literal_values(sr._OvLLM, "reasoning_effort"),
                         set(s["reasoning_effort"]["values"]))
        self.assertEqual(s["persona"]["max_len"], 16_000)
        for section, model in (("tts", sr._OvTTS), ("vad", sr._OvVAD)):
            ranges = ck._TTS_RANGES if section == "tts" else ck._VAD_RANGES
            self.assertEqual(set(model.model_fields), set(ranges), section)
            for key, (lo, hi, integer) in ranges.items():
                self.assertEqual(_bounds(model, key), (lo, hi), f"{section}.{key}")
                self.assertEqual(_is_int(model, key), integer, f"{section}.{key}")

    def test_tts_baseline_defaults_match_shipped_file(self):
        live = cr.load_tts_baseline("chatterbox-turbo")
        defaults = {k: sr._TtsLive.model_fields[k].default for k in sr._TtsLive.model_fields}
        self.assertEqual(defaults, live)

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
