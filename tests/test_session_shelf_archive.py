"""The archive is hidden from every walker but the one that asks for it.

`sessions/<character>/.archive/` is where the soft verb puts a conversation, and
the whole point of the convention is what STOPS happening to a file once it is
there: it leaves the resume picker, it leaves the fresh-start sweep, and the
discard verbs cannot reach it. Those are all one fact in the code —
`list_sessions` walks with `glob("*.json")`, which does not descend, and every
other walker in `session_store` reads the shelf through it — so this file pins
the fact from the outside, walker by walker, where a future refactor would break
it.

`include_archived=True` is the single exception, and it is the shelf route's:
the archived files come back marked, and the live ones are still there.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hearth.session import session_store as ss


class ArchiveIsOutOfSight(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = ss.ensure_dir(Path(self._tmp.name) / "sessions")
        self.archive = self.d / ss.ARCHIVE_DIR
        self.archive.mkdir(mode=0o700)
        self._write(self.d / "session-live.json", held=True, updated="2026-09-07T10:00:00")
        # Three archived files, one of each class a walker might otherwise claim:
        # a held one, an unheld recall-only leftover (the sweepable class), and a
        # named one (the name-based resume form).
        self._write(self.archive / "session-old.json", held=True,
                    updated="2026-09-06T10:00:00", name="the-old-one")
        self._write(self.archive / "session-ro.json", held=False,
                    memory_mode="recall-only", updated="2026-09-05T10:00:00")
        self._write(self.archive / "session-plain.json", held=False,
                    updated="2026-09-04T10:00:00")

    @staticmethod
    def _write(path: Path, *, held: bool, updated: str, name=None,
               memory_mode="full"):
        data = {"schema": 2, "model": "m", "voice": "v", "persona": "default",
                "started": updated, "updated": updated, "held": held,
                "messages": [{"role": "user", "content": "x"}]}
        if name:
            data["name"] = name
        if memory_mode != "full":
            data["memory_mode"] = memory_mode
        ss._atomic_write_json(path, data)

    def ids(self, metas):
        return sorted(m.session_id for m in metas)

    # ── the default: the archive does not exist ─────────────────────────────

    def test_the_shelf_lists_only_the_live_sessions(self):
        self.assertEqual(self.ids(ss.list_sessions(self.d)), ["session-live"])

    def test_the_fresh_start_sweep_cannot_reach_an_archived_leftover(self):
        self.assertEqual(ss.ephemeral_orphans(self.d), [])
        self.assertEqual(ss.discard_ephemeral(self.d), [])
        self.assertTrue((self.archive / "session-ro.json").is_file())

    def test_discard_held_cannot_reach_an_archived_held_session(self):
        self.assertEqual(self.ids(ss.held_sessions(self.d)), ["session-live"])
        removed = ss.discard_held(None, self.d)
        self.assertEqual(removed, ["session-live"])
        for leftover in ("session-old", "session-ro", "session-plain"):
            self.assertTrue((self.archive / f"{leftover}.json").is_file(), leftover)

    def test_resume_cannot_name_an_archived_session(self):
        for arg in ("session-old", "session-old.json", "the-old-one",
                    ".archive/session-old", ".archive/session-old.json"):
            with self.subTest(arg=arg):
                self.assertIsNone(ss.resolve_resume_arg(arg, self.d))
        # …while a live one still resolves.
        p = ss.resolve_resume_arg("session-live", self.d)
        self.assertEqual(p.name, "session-live.json")

    def test_hold_the_latest_orphan_skips_the_archive(self):
        # Nothing unheld is on the live shelf, so there is nothing to promote —
        # the unheld archived files must not be candidates.
        self.assertIsNone(ss.hold_latest_orphan(None, self.d))

    def test_the_hold_marker_lives_beside_the_archive_and_is_not_a_session(self):
        ss.write_hold_request("x", self.d)
        self.assertEqual(self.ids(ss.list_sessions(self.d)), ["session-live"])
        ss.clear_hold_request(self.d)

    # ── the one caller that asks ────────────────────────────────────────────

    def test_include_archived_adds_them_marked(self):
        metas = ss.list_sessions(self.d, include_archived=True)
        self.assertEqual(self.ids(metas),
                         ["session-live", "session-old", "session-plain",
                          "session-ro"])
        by_id = {m.session_id: m for m in metas}
        self.assertFalse(by_id["session-live"].archived)
        for sid in ("session-old", "session-ro", "session-plain"):
            self.assertTrue(by_id[sid].archived, sid)
        # Newest first still holds across both shelves.
        self.assertEqual([m.session_id for m in metas],
                         ["session-live", "session-old", "session-ro",
                          "session-plain"])

    def test_include_archived_is_fine_with_no_archive_at_all(self):
        bare = ss.ensure_dir(Path(self._tmp.name) / "bare")
        self.assertEqual(ss.list_sessions(bare, include_archived=True), [])

    def test_an_archived_meta_still_carries_no_content(self):
        import json
        metas = ss.list_sessions(self.d, include_archived=True)
        blob = json.dumps([m.__dict__ for m in metas], default=str)
        self.assertNotIn('"role"', blob)
        self.assertTrue(all(m.turns == 1 for m in metas))


if __name__ == "__main__":
    unittest.main()
