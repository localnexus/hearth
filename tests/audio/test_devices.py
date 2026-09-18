"""The enrolled-device registry: rows, ids, and the one write that loses something.

`config/devices.toml` is small, is written three ways (pair, start, forget) and
is read by a page that polls. What is worth pinning is not the shape of a
dataclass but the promises around it:

  * a file that is missing, unreadable or malformed is an EMPTY LIST and a
    warning — never an exception on the path that starts a conversation;
  * a re-pair refreshes the row it already had rather than growing a second one,
    and keeps the date it was first paired;
  * a minted id is readable, unique, and always a legal route word;
  * `forget` is the only verb that removes anything, and it copies the file
    beside itself first, without ever overwriting an archive;
  * every write lands whole — tmp + os.replace — and leaves no .tmp behind.

Every case runs against its own temporary file (the `path=` seam) with an
injected clock and an injected draw, so nothing here reads the operator's data
folder, sleeps, or depends on entropy.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import tomllib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hearth.audio import devices as reg
from hearth.audio.route import DEVICE_RE

PT = timezone(timedelta(hours=-7))
T0 = datetime(2026, 9, 18, 4, 10, 11, tzinfo=PT)
T1 = datetime(2026, 9, 19, 9, 0, 0, tzinfo=PT)


class _Sink:
    """Captures loguru lines for the duration of a with-block."""

    def __enter__(self):
        from loguru import logger

        self.lines: list[str] = []
        self._logger = logger
        self._id = logger.add(lambda msg: self.lines.append(str(msg)))
        return self

    def __exit__(self, *exc):
        self._logger.remove(self._id)
        return False

    def count(self, fragment: str) -> int:
        return sum(1 for line in self.lines if fragment in line)


class _Case(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.file = self.dir / "devices.toml"
        reg.forget_cache()

    def draw(self, *values: str):
        """A pinned draw: the given suffixes in order, the last one for ever."""
        queue = list(values)

        def _draw() -> str:
            return queue.pop(0) if len(queue) > 1 else queue[0]

        return _draw

    def write(self, text: str) -> None:
        self.file.write_text(text, encoding="utf-8")


# ── reading ──────────────────────────────────────────────────────────────────

class ReadingTheRegistry(_Case):

    def test_a_missing_file_is_an_empty_list_and_says_nothing(self):
        with _Sink() as sink:
            self.assertEqual(reg.load(self.file), [])
        self.assertEqual(sink.count("[devices]"), 0,
                         "a machine that has never paired is not a fault")

    def test_a_malformed_file_is_empty_and_one_warning_never_a_raise(self):
        self.write('[[device]]\nid = "pixel"\nlabel = "unclosed\n')
        with _Sink() as sink:
            self.assertEqual(reg.load(self.file), [])
        self.assertEqual(sink.count("[devices]"), 1, sink.lines)
        self.assertIn("malformed", sink.lines[0])
        self.assertIn("devices.toml", sink.lines[0])

    def test_a_row_without_a_usable_id_is_skipped_and_the_rest_survive(self):
        self.write('[[device]]\nlabel = "no id"\n\n'
                   '[[device]]\nid = "pix el"\n\n'
                   '[[device]]\nid = "pixel"\nlabel = "Pixel"\n'
                   'paired_at = "2026-09-18T04:10:11-07:00"\n'
                   'last_seen = "2026-09-18T04:10:11-07:00"\n')
        with _Sink() as sink:
            rows = reg.load(self.file)
        self.assertEqual([d.id for d in rows], ["pixel"])
        self.assertEqual(sink.count("[devices]"), 1)
        self.assertIn("2 rows", sink.lines[0])

    def test_last_seen_falls_back_to_paired_at_and_a_missing_label_is_a_word(self):
        self.write('[[device]]\nid = "pixel"\npaired_at = "2026-09-18T04:10:11-07:00"\n')
        row = reg.load(self.file)[0]
        self.assertEqual(row.last_seen, "2026-09-18T04:10:11-07:00")
        self.assertEqual(row.label, "device")

    def test_get_finds_one_row_and_answers_none_for_a_stranger(self):
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        self.assertEqual(reg.get("pixel", self.file).label, "Pixel")
        self.assertIsNone(reg.get("nobody", self.file))

    def test_an_unreadable_file_is_empty_and_warns_rather_than_raising(self):
        self.file.mkdir()   # a directory where a file should be: OSError on read
        with _Sink() as sink:
            self.assertEqual(reg.load(self.file), [])
        self.assertEqual(sink.count("[devices]"), 1)


# ── enrol ────────────────────────────────────────────────────────────────────

class Enrolling(_Case):

    def test_a_first_pair_mints_an_id_and_writes_the_file_with_its_header(self):
        device = reg.enrol("Pixel", now=T0, path=self.file,
                           draw=self.draw("3f4a"))
        self.assertEqual(device.id, "pixel-3f4a")
        self.assertEqual(device.label, "Pixel")
        self.assertEqual(device.paired_at, "2026-09-18T04:10:11-07:00")
        self.assertEqual(device.last_seen, device.paired_at)
        text = self.file.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# config/devices.toml"))
        self.assertIn("last_seen means the last time a conversation was started",
                      text)
        self.assertEqual([d.id for d in reg.load(self.file)], ["pixel-3f4a"])

    def test_a_known_id_refreshes_the_row_it_already_had(self):
        first = reg.enrol("Pixel", now=T0, path=self.file, draw=self.draw("3f4a"))
        again = reg.enrol("the hallway Pixel", first.id, now=T1, path=self.file,
                          draw=self.draw("beef"))
        self.assertEqual(again.id, first.id)
        self.assertEqual(again.label, "the hallway Pixel")
        self.assertEqual(again.paired_at, first.paired_at, "the first pairing stands")
        self.assertEqual(again.last_seen, "2026-09-19T09:00:00-07:00")
        self.assertEqual(len(reg.load(self.file)), 1, "a re-pair is not a second row")

    def test_an_unknown_but_legal_id_is_adopted_as_written(self):
        """The migration in one screen: a device that paired before the registry
        keeps the id its route word already uses."""
        device = reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        self.assertEqual(device.id, "pixel")
        self.assertEqual([d.id for d in reg.load(self.file)], ["pixel"])

    def test_an_id_that_is_not_an_id_is_ignored_and_a_fresh_one_minted(self):
        for wanted in ("", "   ", "pix el", "../etc", "x" * 65, "remote:pixel"):
            with self.subTest(device_id=wanted):
                self.file.unlink(missing_ok=True)
                device = reg.enrol("Pixel", wanted, now=T0, path=self.file,
                                   draw=self.draw("3f4a"))
                self.assertEqual(device.id, "pixel-3f4a")

    def test_a_label_is_trimmed_stripped_and_never_empty(self):
        long = "P" * 60
        device = reg.enrol(long + "\n\tx\x00", now=T0, path=self.file,
                           draw=self.draw("aaaa"))
        self.assertEqual(len(device.label), reg.LABEL_MAX)
        self.assertNotIn("\n", device.label)
        self.assertNotIn("\x00", device.label)
        blank = reg.enrol("   ", now=T0, path=self.file, draw=self.draw("bbbb"))
        self.assertEqual(blank.label, "device")
        self.assertEqual(blank.id, "device-bbbb")

    def test_two_phones_of_the_same_name_are_two_rows(self):
        one = reg.enrol("Pixel", now=T0, path=self.file, draw=self.draw("3f4a"))
        two = reg.enrol("Pixel", now=T0, path=self.file, draw=self.draw("beef"))
        self.assertNotEqual(one.id, two.id)
        self.assertEqual(len(reg.load(self.file)), 2)


# ── minting ──────────────────────────────────────────────────────────────────

class MintingAnId(_Case):

    def test_the_slug_is_the_readable_half(self):
        self.assertEqual(reg.slug("Pixel"), "pixel")
        self.assertEqual(reg.slug("the Kitchen's Pixel 9!"), "the-kitchen-s-pixel-9")
        self.assertEqual(reg.slug("   "), "device")
        self.assertEqual(reg.slug("✨✨"), "device")
        self.assertLessEqual(len(reg.slug("a" * 40)), reg.SLUG_MAX)
        self.assertFalse(reg.slug("a" * 23 + " b").endswith("-"),
                         "a truncated slug must not end on a dash")

    def test_a_minted_id_is_always_a_legal_route_word(self):
        for label in ("Pixel", "", "✨", "a" * 90, "a spare iPhone 15 Pro Max"):
            with self.subTest(label=label):
                ident = reg.mint_id(label, set(), draw=lambda: "3f4a")
                self.assertTrue(DEVICE_RE.fullmatch(ident), ident)

    def test_a_collision_is_redrawn(self):
        ident = reg.mint_id("Pixel", {"pixel-3f4a", "pixel-beef"},
                            draw=self.draw("3f4a", "beef", "c0de"))
        self.assertEqual(ident, "pixel-c0de")

    def test_a_draw_that_never_stops_colliding_widens_instead_of_spinning(self):
        ident = reg.mint_id("Pixel", {"pixel-3f4a", "pixel-3f4a-2"},
                            draw=lambda: "3f4a")
        self.assertEqual(ident, "pixel-3f4a-3")

    def test_the_default_draw_is_four_hex_characters(self):
        ident = reg.mint_id("Pixel", set())
        self.assertRegex(ident, r"^pixel-[0-9a-f]{4}$")


# ── touch ────────────────────────────────────────────────────────────────────

class Touching(_Case):

    def test_a_start_stamps_last_seen_and_leaves_everything_else(self):
        first = reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        self.assertTrue(reg.touch("pixel", now=T1, path=self.file))
        row = reg.get("pixel", self.file)
        self.assertEqual(row.last_seen, "2026-09-19T09:00:00-07:00")
        self.assertEqual(row.paired_at, first.paired_at)
        self.assertEqual(row.label, "Pixel")

    def test_an_unknown_id_is_a_no_op_that_says_so(self):
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        before = self.file.read_text(encoding="utf-8")
        self.assertFalse(reg.touch("nobody", now=T1, path=self.file))
        self.assertEqual(self.file.read_text(encoding="utf-8"), before)

    def test_touching_with_no_file_at_all_never_raises(self):
        self.assertFalse(reg.touch("pixel", now=T1, path=self.file))
        self.assertFalse(self.file.exists())


# ── forget, and the archive ──────────────────────────────────────────────────

class Forgetting(_Case):

    def _two(self):
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        reg.enrol("iPad", "ipad", now=T0, path=self.file)

    def test_the_row_goes_and_the_file_is_copied_beside_itself_first(self):
        self._two()
        before = self.file.read_text(encoding="utf-8")
        self.assertTrue(reg.forget("pixel", self.file))
        self.assertEqual([d.id for d in reg.load(self.file)], ["ipad"])
        archives = sorted(p for p in self.dir.iterdir()
                          if ".prev-" in p.name)
        self.assertEqual(len(archives), 1, archives)
        self.assertEqual(archives[0].read_text(encoding="utf-8"), before,
                         "the archive is what the file was, not what it became")

    def test_an_archive_is_never_overwritten(self):
        self._two()
        self.assertTrue(reg.forget("pixel", self.file))
        self.assertTrue(reg.forget("ipad", self.file))
        archives = sorted(p.name for p in self.dir.iterdir() if ".prev-" in p.name)
        self.assertEqual(len(archives), 2, archives)
        self.assertEqual(len(set(archives)), 2)

    def test_the_predicted_name_is_the_one_a_preview_can_promise(self):
        self._two()
        predicted = reg.archive_name(self.file)
        self.assertTrue(reg.forget("pixel", self.file))
        self.assertTrue(predicted.exists(), predicted)

    def test_forgetting_a_stranger_changes_nothing_and_archives_nothing(self):
        self._two()
        before = self.file.read_text(encoding="utf-8")
        self.assertFalse(reg.forget("nobody", self.file))
        self.assertEqual(self.file.read_text(encoding="utf-8"), before)
        self.assertEqual([p for p in self.dir.iterdir() if ".prev-" in p.name], [])

    def test_forgetting_the_last_row_leaves_a_readable_empty_registry(self):
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        self.assertTrue(reg.forget("pixel", self.file))
        self.assertEqual(reg.load(self.file), [])
        tomllib.loads(self.file.read_text(encoding="utf-8"))  # still valid TOML


# ── the emitter, and the atomic write ────────────────────────────────────────

class TheWrite(_Case):

    def test_nothing_is_left_beside_the_file(self):
        reg.enrol("Pixel", now=T0, path=self.file, draw=self.draw("3f4a"))
        reg.touch("pixel-3f4a", now=T1, path=self.file)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["devices.toml"], "a .tmp survived the write")

    def test_a_label_with_quotes_and_backslashes_round_trips(self):
        nasty = 'the "back\\room" phone'
        device = reg.enrol(nasty, now=T0, path=self.file, draw=self.draw("3f4a"))
        self.assertEqual(device.label, nasty)
        doc = tomllib.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(doc["device"][0]["label"], nasty)
        self.assertEqual(reg.get(device.id, self.file).label, nasty)

    def test_the_emitter_writes_four_keys_per_row_and_nothing_else(self):
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        doc = tomllib.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc["device"][0]),
                         ["id", "label", "last_seen", "paired_at"])
        self.assertEqual(sorted(doc), ["device"])

    def test_a_control_character_can_never_reach_the_file(self):
        text = reg.emit([reg.Device("pixel", "a\nb\x07", "t", "t")])
        self.assertNotIn("\x07", text)
        doc = tomllib.loads(text)
        self.assertEqual(doc["device"][0]["label"], "ab")


# ── the poll's cache ─────────────────────────────────────────────────────────

class TheMtimeCache(_Case):

    def test_a_second_read_of_an_unchanged_file_does_not_parse_again(self):
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        first = reg.load_cached(self.file)
        self.assertIs(reg.load_cached(self.file), first,
                      "the poll must pay a stat, not a parse")

    def test_a_write_is_seen_by_the_next_read(self):
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        self.assertEqual(len(reg.load_cached(self.file)), 1)
        reg.enrol("iPad", "ipad", now=T0, path=self.file)
        self.assertEqual([d.id for d in reg.load_cached(self.file)],
                         ["pixel", "ipad"])

    def test_a_file_that_appears_later_is_seen(self):
        self.assertEqual(reg.load_cached(self.file), [])
        reg.enrol("Pixel", "pixel", now=T0, path=self.file)
        self.assertEqual(len(reg.load_cached(self.file)), 1)


class TheDefaultLocation(_Case):

    def test_it_sits_in_the_data_folder_beside_the_other_config_files(self):
        from hearth.config import config_loader as cl

        self.assertEqual(reg.devices_toml(), cl.CONFIG_DIR / "devices.toml")


if __name__ == "__main__":
    unittest.main()
