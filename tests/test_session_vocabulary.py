"""test_session_vocabulary.py — the two-switches wording stays off every surface
a person reads, and the new words actually land where they must.

Static: no bot, no server, string checks over the tree. Two pins:

  1. none of the retired one-mode/saved-by-default phrases survive anywhere
     under docs/, the users' manual, src/hearth/ui/, the admin routes' HTML,
     or start.sh / stop.sh (a `.archive/` copy is exempt — it is a fossil on
     purpose);
  2. "remember the past" and "keep this conversation" — the two switches'
     own words — each appear at least once in the five surfaces a person
     actually reads them on first.

Run:  .venv/bin/python -m unittest tests.test_session_vocabulary
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WALK_DIRS = [
    ROOT / "docs",
    ROOT / "src" / "hearth" / "config" / "users-manual",
    ROOT / "src" / "hearth" / "ui",
]
ROUTES_HTML = sorted((ROOT / "src" / "hearth" / "supervisor" / "routes").glob("*.html"))
STANDALONE_FILES = [ROOT / "start.sh", ROOT / "stop.sh"]

BANNED_PHRASES = [
    "saves either way",
    "save either way",
    "fresh meeting, and nothing is written",
    "recall-only sitting's transcript",
    "Saved by default",
    "saved-by-default",
    "saved by default",
    "transcript-ephemeral",
    "only recall-only leftovers",
    "Memory: ${",
]

REQUIRED_PHRASES = ["remember the past", "keep this conversation"]
REQUIRED_IN = [
    ROOT / "docs" / "memory" / "session-modes.md",
    ROOT / "docs" / "runbook" / "03.5-session-continuity.md",
    ROOT / "src" / "hearth" / "config" / "users-manual" / "the-memory-thread.md",
    ROOT / "src" / "hearth" / "config" / "users-manual" / "the-pages-behind-the-door.md",
    ROOT / "src" / "hearth" / "supervisor" / "routes" / "launch_page.html",
]


def _is_archived(path: Path) -> bool:
    return ".archive" in path.parts


def _corpus() -> list[Path]:
    files: list[Path] = []
    for d in WALK_DIRS:
        files += [p for p in d.rglob("*") if p.is_file() and not _is_archived(p)]
    files += [p for p in ROUTES_HTML if not _is_archived(p)]
    files += [p for p in STANDALONE_FILES if p.is_file()]
    return files


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # a binary asset (png/jpg/pyc/…) — not prose, nothing to scan


class BannedPhrases(unittest.TestCase):
    def test_no_retired_vocabulary_survives_the_swept_surfaces(self):
        offenders = []
        for path in _corpus():
            text = _read(path)
            if text is None:
                continue
            for phrase in BANNED_PHRASES:
                if phrase in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {phrase!r}")
        self.assertEqual(offenders, [], "retired wording found:\n" + "\n".join(offenders))


class RequiredPhrases(unittest.TestCase):
    def test_both_switches_are_named_on_every_surface_a_person_meets_them_first(self):
        missing = []
        for path in REQUIRED_IN:
            text = _read(path) or ""
            for phrase in REQUIRED_PHRASES:
                if phrase not in text:
                    missing.append(f"{path.relative_to(ROOT)}: missing {phrase!r}")
        self.assertEqual(missing, [], "missing required wording:\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
