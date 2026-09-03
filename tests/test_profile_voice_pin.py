"""test_profile_voice_pin.py — a character's remembered voice.

`voice = "<bundle>"` at the top of characters/<c>/profile.toml pins which voice the
switch pickers reach for when the selection MOVES to that character. Before it, the
picker offered whichever bundle sorted first, which is wrong for anyone whose keeper
isn't the alphabetically-first clip — the ordinary case once a character holds a
dozen auditions and the keeper is the last of them.

Three properties, and they are the whole feature:
  1. RESOLUTION — the pin is read DATA-then-ROOT, and it is a convenience, never a
     gate: unset, unreadable, or naming a bundle that isn't there all answer None so
     the picker falls back to first-in-list rather than pre-selecting a voice that
     cannot load. active.toml stays the selection record.
  2. PUBLICATION — switch.choices() carries it per character as `default_voice`, so
     BOTH ports get it from the one payload the shared switch card already reads.
  3. PRESERVATION — the pin lives in a PANEL-MANAGED file. The panel's "Save to
     character" rewrites that file, and its serializer knows only the knob sections,
     so without the carry-through here a knob save would silently erase the pin.
     This is the regression that would be invisible until a switch went to the wrong
     voice weeks later.

Run:  .venv/bin/python -m unittest tests.test_profile_voice_pin
"""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from hearth.config import config_loader
from hearth.config import settings_registry as sr
from hearth.control.features import config_knobs as ck
from hearth.control.features import config_profiles as cp
from hearth.supervisor import switch as switch_mod


def _build_install(root: Path) -> None:
    """One character with three voice bundles — enough for 'not the first one'."""
    char = root / "characters" / "zz-keeper"
    char.mkdir(parents=True)
    (char / "persona.md").write_text("## IDENTITY\nx\n## SOUL\ny\n")
    for tag in ("zz-a", "zz-b", "zz-z"):
        d = char / "voices" / tag
        d.mkdir(parents=True)
        (d / "sample.wav").write_bytes(b"RIFFfake")
        (d / "voice.toml").write_text(f'tag = "{tag}"\nref_wav = "sample.wav"\n')


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_install(self.root)
        # _DATA is read at CALL time by _lookup/list_voices/preferred_voice.
        patch = mock.patch.object(config_loader, "_DATA", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.profile = self.root / "characters" / "zz-keeper" / "profile.toml"

    def pin(self, text: str) -> None:
        self.profile.write_text(text)


class Resolution(_Fixture):

    def test_no_profile_file_is_not_an_error(self):
        """The common case: nobody has pinned anything, and nothing complains."""
        self.assertIsNone(config_loader.preferred_voice("zz-keeper"))

    def test_a_pin_resolves(self):
        self.pin('voice = "zz-z"\n')
        self.assertEqual(config_loader.preferred_voice("zz-keeper"), "zz-z")

    def test_a_pin_survives_the_knob_tiers_beside_it(self):
        self.pin('voice = "zz-b"\n\n[llm]\ntemperature = 0.8\n')
        self.assertEqual(config_loader.preferred_voice("zz-keeper"), "zz-b")

    def test_a_stale_pin_falls_back_rather_than_offering_a_dead_voice(self):
        """A renamed or deleted bundle must not pre-select something that cannot
        load — the picker's first-in-list default is the honest answer."""
        self.pin('voice = "zz-gone"\n')
        self.assertIsNone(config_loader.preferred_voice("zz-keeper"))

    def test_a_traversal_name_is_refused(self):
        self.pin('voice = "../../etc/passwd"\n')
        self.assertIsNone(config_loader.preferred_voice("zz-keeper"))

    def test_a_non_string_pin_is_refused(self):
        self.pin("voice = 3\n")
        self.assertIsNone(config_loader.preferred_voice("zz-keeper"))

    def test_a_malformed_profile_does_not_raise(self):
        """This runs on the picker's path; a hand-edit typo must not break the
        page, only the pin."""
        self.pin("not = toml = at all\n")
        self.assertIsNone(config_loader.preferred_voice("zz-keeper"))

    def test_an_unknown_character_is_none(self):
        self.assertIsNone(config_loader.preferred_voice("zz-ghost"))

    def test_the_key_is_declared_in_the_registry(self):
        """Undeclared, the settings overview would flag every pinned profile as
        carrying an unknown key."""
        errors, warnings = sr.strict_check("profile", {"voice": "zz-z"})
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        errors, _ = sr.strict_check("profile", {"voice": "../evil"})
        self.assertTrue(errors, "the name pattern must reject a path-ish pin")


class Publication(_Fixture):

    def _entry(self) -> dict:
        chars = switch_mod.choices()["characters"]
        return next(c for c in chars if c["name"] == "zz-keeper")

    def test_choices_carry_the_pin(self):
        self.pin('voice = "zz-z"\n')
        entry = self._entry()
        self.assertEqual(entry["voices"], ["zz-a", "zz-b", "zz-z"])
        self.assertEqual(entry["default_voice"], "zz-z")

    def test_choices_carry_none_when_unpinned(self):
        """The key is always present — the card reads info.default_voice directly,
        and an absent key would make 'unpinned' indistinguishable from a payload
        served by an older daemon."""
        entry = self._entry()
        self.assertIn("default_voice", entry)
        self.assertIsNone(entry["default_voice"])

    def test_the_card_prefers_the_pin_over_first_in_list(self):
        """The consuming line, pinned to the shared card so the payload and its one
        reader cannot drift apart."""
        js = (Path(switch_mod.__file__).parent.parent / "ui" / "switch_card.js").read_text()
        self.assertIn("info.default_voice || info.voices[0]", js)


class Preservation(_Fixture):

    def test_snapshot_carries_an_existing_pin_through(self):
        snap = cp._snapshot("character", {"llm": {"temperature": 0.9}},
                            existing={"voice": "zz-z", "llm": {"temperature": 0.1}})
        self.assertEqual(snap, {"voice": "zz-z", "llm": {"temperature": 0.9}})

    def test_snapshot_never_invents_a_pin(self):
        """Nothing in the panel sets this key; it is an operator hand-edit."""
        snap = cp._snapshot("character", {"llm": {"temperature": 0.9}}, existing={})
        self.assertEqual(snap, {"llm": {"temperature": 0.9}})
        self.assertEqual(cp._snapshot("character", {"llm": {}}), {"llm": {}})

    def test_a_voice_profile_takes_no_pin(self):
        """The pin answers 'which voice', so it belongs to the character. A voice
        bundle's own profile carrying one would be meaningless."""
        snap = cp._snapshot("voice", {"tts": {"top_p": 0.8}}, existing={"voice": "zz-z"})
        self.assertEqual(snap, {"tts": {"top_p": 0.8}})

    def test_the_pin_is_written_before_the_tables(self):
        """TOML reads a bare key AFTER a [llm] header as llm.voice. If this ever
        regresses, the file still parses — it just quietly stops being a pin."""
        text = cp._dump_profile({"voice": "zz-z", "llm": {"temperature": 0.8}})
        self.assertLess(text.index("voice = "), text.index("[llm]"))
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["voice"], "zz-z")
        self.assertEqual(parsed["llm"], {"temperature": 0.8})

    def test_dump_without_a_pin_is_the_old_output(self):
        data = {"llm": {"temperature": 0.8}}
        self.assertEqual(cp._dump_profile(data), ck._dump(data))

    def test_a_knob_save_round_trips_the_pin_on_disk(self):
        """The whole point, end to end on a real file: pin by hand, save knobs from
        the panel, the pin is still there and still resolves."""
        self.pin('voice = "zz-z"\n')
        path = cp._char_path("zz-keeper")
        data = cp._snapshot("character", {"llm": {"temperature": 0.42}},
                            existing=cp._read_profile(path))
        cp._atomic_write(path, cp._dump_profile(data))
        self.assertEqual(cp._read_profile(path)["llm"], {"temperature": 0.42})
        self.assertEqual(config_loader.preferred_voice("zz-keeper"), "zz-z")


if __name__ == "__main__":
    unittest.main()
