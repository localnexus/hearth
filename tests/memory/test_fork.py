"""The fork verb — branching a companion's memory track at a juncture.

Preview-then-confirm, the juncture cut (dated inclusivity, undated records stay),
create-only --as, forked_from provenance, and what never travels: the intent
slot, and sessions unless asked for.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth import memory as seam_mod  # noqa: E402
from hearth.memory import maybe_attach  # noqa: E402
from hearth.memory.backend import SessionRecord  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory import __main__ as memory_cli  # noqa: E402


class _CLIBackend:
    """Seam-backend stand-in for the curation CLI: records every call."""

    name = "fakebackend"

    def __init__(self, forget_result: bool = True,
                 forget_raises: Exception | None = None,
                 clear_raises: Exception | None = None) -> None:
        self.forgets: list[tuple[str, str]] = []
        self.clears: list[str] = []
        self.stored: list[str] = []
        self._forget_result = forget_result
        self._forget_raises = forget_raises
        self._clear_raises = clear_raises

    def forget(self, companion: str, session_id: str) -> bool:
        self.forgets.append((companion, session_id))
        if self._forget_raises is not None:
            raise self._forget_raises
        return self._forget_result

    def clear(self, companion: str) -> None:
        self.clears.append(companion)
        if self._clear_raises is not None:
            raise self._clear_raises

    def store(self, companion: str, record: SessionRecord) -> None:  # noqa: ARG002
        self.stored.append(record.session_id)


def _record(sid: str, ended: str, n_turns: int = 2, name: str = "") -> SessionRecord:
    messages = []
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"user line {i} of {sid}"})
        messages.append({"role": "assistant", "content": f"assistant line {i} of {sid}"})
    return SessionRecord(
        companion="testchar", session_id=sid, started="2026-08-29T10:00:00",
        ended=ended, name=name, messages=messages,
    )


class TestForkVerb(unittest.TestCase):
    """fork --as/--until: metadata-selected record copy + restamp, identity
    scaffold verified by the startup loaders, source-tier enrollment, opt-in
    session copy, never the intent slot; preview writes nothing; a failed
    verification rolls the whole fork back (create-only both ways)."""

    SRC = "zz-src"

    def setUp(self):
        from hearth.config import config_loader
        self.config_loader = config_loader
        self._data = tempfile.TemporaryDirectory()
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._data.cleanup)
        self.addCleanup(self._root.cleanup)
        self.data = Path(self._data.name)
        cdir = self.data / "characters" / self.SRC
        (cdir / "voices" / "main").mkdir(parents=True)
        (cdir / "theme").mkdir()
        (cdir / "sessions").mkdir()
        (cdir / "persona.md").write_text(
            "## IDENTITY\nan example\n\n## SOUL\nwarm\n", encoding="utf-8")
        (cdir / "persona.alt.md").write_text(
            "## IDENTITY\nanother\n\n## SOUL\ndry\n", encoding="utf-8")
        (cdir / "voices" / "main" / "voice.toml").write_text(
            'tag = "main"\nref_wav = "sample.wav"\n', encoding="utf-8")
        (cdir / "voices" / "main" / "sample.wav").write_bytes(b"RIFF")
        (cdir / "theme" / "style.css").write_text("body{}", encoding="utf-8")
        rec_dir = cdir / "memory" / "records"
        for sid, ended in (("early", "2026-08-30T10:00:00"),
                           ("late-day", "2026-08-30T23:30:00"),
                           ("after", "2026-08-31T10:00:00")):
            records_mod.write_record(_record(sid, ended), directory=rec_dir)
        (rec_dir / "undated.json").write_text(json.dumps(
            {"schema": 1, "kind": "memory-record", "companion": self.SRC,
             "session_id": "undated", "messages": []}), encoding="utf-8")
        (cdir / "memory" / "intent.json").write_text("{}", encoding="utf-8")
        for sid, started in (("held-1", "2026-08-30T09:00:00"),
                             ("held-2", "2026-08-31T09:00:00")):
            (cdir / "sessions" / f"{sid}.json").write_text(json.dumps(
                {"messages": [], "started": started, "held": True}),
                encoding="utf-8")
        (self.data / "config").mkdir()
        self.memory_toml = self.data / "config" / "memory.toml"
        self.memory_toml.write_text(
            '# operator file\n[memory]\nenabled = true\nbackend = "floor"\n'
            "\n[memory.companions]\n", encoding="utf-8")
        for name, value in (("_DATA", self.data),
                            ("_ROOT", Path(self._root.name)),
                            ("MEMORY_TOML", self.memory_toml)):
            patcher = mock.patch.object(config_loader, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fork(self, *, until="2026-08-30", include_sessions=False, yes=True,
              target="zz-fork"):
        return memory_cli._cmd_fork(self.SRC, target, until,
                                    include_sessions, yes)

    def test_preview_writes_nothing(self):
        rc = self._fork(yes=False)
        self.assertEqual(rc, 1)
        self.assertFalse((self.data / "characters" / "zz-fork").exists())
        self.assertNotIn("zz-fork", self.memory_toml.read_text())

    def test_plan_selects_by_metadata_bare_date_inclusive(self):
        from hearth.memory import fork as fork_mod
        p = fork_mod.plan(self.SRC, "zz-fork", "2026-08-30")
        self.assertEqual([r.session_id for _, r in p.records],
                         ["early", "late-day"])  # 23:30 same-day is INSIDE the juncture
        self.assertEqual(p.left_behind, 1)
        self.assertEqual(p.undated, 1)
        self.assertEqual(p.tier, "floor")

    def test_fork_scaffolds_restamps_and_enrolls(self):
        rc = self._fork()
        self.assertEqual(rc, 0)
        fdir = self.data / "characters" / "zz-fork"
        for rel in ("persona.md", "persona.alt.md", "voices/main/voice.toml",
                    "voices/main/sample.wav", "theme/style.css"):
            self.assertTrue((fdir / rel).is_file(), rel)
        copied = json.loads((fdir / "memory/records/early.json").read_text())
        self.assertEqual(copied["companion"], "zz-fork")
        self.assertEqual(copied["forked_from"]["companion"], self.SRC)
        self.assertFalse((fdir / "memory/records/after.json").exists())
        self.assertFalse((fdir / "memory/intent.json").exists())
        self.assertFalse((fdir / "sessions").exists())
        self.assertIn('zz-fork = "floor"', self.memory_toml.read_text())
        self.assertIn("# operator file", self.memory_toml.read_text())

    def test_include_sessions_copies_only_up_to_the_juncture(self):
        rc = self._fork(include_sessions=True)
        self.assertEqual(rc, 0)
        sdir = self.data / "characters" / "zz-fork" / "sessions"
        self.assertTrue((sdir / "held-1.json").is_file())
        self.assertFalse((sdir / "held-2.json").exists())

    def test_fork_is_create_only(self):
        (self.data / "characters" / "zz-fork").mkdir()
        rc = self._fork()
        self.assertEqual(rc, 1)

    def test_bad_until_is_an_error(self):
        self.assertEqual(self._fork(until="whenever"), 1)
        self.assertFalse((self.data / "characters" / "zz-fork").exists())

    def test_failed_verification_rolls_the_fork_back(self):
        with mock.patch.object(self.config_loader, "load_voice",
                               side_effect=RuntimeError("broken bundle")):
            rc = self._fork()
        self.assertEqual(rc, 1)
        self.assertFalse((self.data / "characters" / "zz-fork").exists())
        self.assertNotIn("zz-fork", self.memory_toml.read_text())

    def test_hindsight_tier_replays_into_the_forks_bank(self):
        import types
        text = self.memory_toml.read_text().replace(
            "[memory.companions]\n",
            f'[memory.companions]\n{self.SRC} = "hindsight"\n')
        self.memory_toml.write_text(text, encoding="utf-8")
        backend = _CLIBackend()
        stores: list[tuple[str, str]] = []
        backend.store = lambda companion, record: stores.append(
            (companion, record.session_id))
        seam = types.SimpleNamespace(backend=backend, close=lambda: None)
        with mock.patch.object(seam_mod, "maybe_attach", lambda c: seam):
            rc = self._fork()
        self.assertEqual(rc, 0)
        self.assertEqual(stores, [("zz-fork", "early"), ("zz-fork", "late-day")])
        self.assertIn('zz-fork = "hindsight"', self.memory_toml.read_text())

if __name__ == "__main__":
    unittest.main()
