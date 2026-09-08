"""session/verbs.py — the fence, without an HTTP client in sight.

A session id is a name, not a path. These tests pin the four ways that could
stop being true — a traversal id, a symlink pointing out of the tree, a
directory or a missing file wearing a session's name — plus the two things the
fence must NOT refuse: an ordinary session, and one inside `.archive/`. The
refusal reason is checked for what it does not say: never the path.

The loopback matrix is here too, because "is the browser on this machine?" is
the whole difference between reveal and download.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth.session import verbs


class _Rooted(unittest.TestCase):
    """A temp data root with one companion and one session file in it."""

    CHARACTER = "zz-verbs-test"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sessions = self.root / "characters" / self.CHARACTER / "sessions"
        self.sessions.mkdir(parents=True)
        (self.sessions / "session-a.json").write_text('{"messages": []}', encoding="utf-8")
        patcher = mock.patch.object(verbs, "sessions_root", lambda c: self.sessions)
        patcher.start()
        self.addCleanup(patcher.stop)


class IdShape(unittest.TestCase):

    def test_plain_stems_pass(self):
        for good in ("session-2026-09-07T10-00-00", "a", "a.b_c-d", "session.1"):
            self.assertTrue(verbs.valid_session_id(good), good)

    def test_paths_and_dotfiles_are_refused(self):
        for bad in ("", None, ".", "..", "../x", "a/b", ".hidden", "a..b",
                    "/etc/passwd", "a b", "a\0b"):
            self.assertFalse(verbs.valid_session_id(bad), repr(bad))


class Confinement(_Rooted):

    def test_an_ordinary_session_resolves(self):
        p = verbs.resolve_session_path(self.CHARACTER, "session-a")
        self.assertEqual(p.name, "session-a.json")
        self.assertTrue(p.is_file())

    def test_traversal_is_refused_before_the_disk_is_touched(self):
        with self.assertRaises(verbs.SessionPathError) as cm:
            verbs.resolve_session_path(self.CHARACTER, "../../../../etc/passwd")
        self.assertEqual(cm.exception.reason, "invalid session id")

    def test_a_symlink_out_of_the_tree_is_refused(self):
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        (self.sessions / "escape.json").symlink_to(outside)
        with self.assertRaises(verbs.SessionPathError) as cm:
            verbs.resolve_session_path(self.CHARACTER, "escape")
        self.assertIn("link", cm.exception.reason)

    def test_an_archived_session_is_inside_the_fence(self):
        arch = self.sessions / verbs.ARCHIVE_DIR
        arch.mkdir()
        (arch / "session-b.json").write_text('{"messages": []}', encoding="utf-8")
        p = verbs.resolve_session_path(self.CHARACTER, "session-b", archived=True)
        self.assertEqual(p.parent.name, verbs.ARCHIVE_DIR)
        # …and the same id is NOT found in the unarchived root.
        with self.assertRaises(verbs.SessionPathError):
            verbs.resolve_session_path(self.CHARACTER, "session-b")

    def test_a_directory_is_not_a_session(self):
        (self.sessions / "adir.json").mkdir()
        with self.assertRaises(verbs.SessionPathError) as cm:
            verbs.resolve_session_path(self.CHARACTER, "adir")
        self.assertEqual(cm.exception.reason, "no such session")

    def test_a_missing_session_is_refused(self):
        with self.assertRaises(verbs.SessionPathError) as cm:
            verbs.resolve_session_path(self.CHARACTER, "session-nope")
        self.assertEqual(cm.exception.reason, "no such session")

    def test_no_refusal_reason_carries_a_path(self):
        for sid, kw in (("../x", {}), ("session-nope", {}), ("session-b", {"archived": True})):
            with self.subTest(sid=sid):
                try:
                    verbs.resolve_session_path(self.CHARACTER, sid, **kw)
                except verbs.SessionPathError as exc:
                    self.assertNotIn("/", exc.reason)
                    self.assertNotIn(self._tmp.name, str(exc))


class LoopbackMatrix(unittest.TestCase):

    def test_same_machine(self):
        for peer in ("127.0.0.1", "127.0.0.53", "::1", "[::1]", "::ffff:127.0.0.1"):
            self.assertTrue(verbs.is_loopback_peer(peer), peer)

    def test_across_the_network(self):
        for peer in (None, "", "10.0.0.4", "100.64.1.2", "192.168.1.9",
                     "fd7a:115c:a1e0::1", "::ffff:10.0.0.4", "not-an-address"):
            self.assertFalse(verbs.is_loopback_peer(peer), repr(peer))


class RevealArgv(_Rooted):

    def test_fixed_argv_on_macos(self):
        p = self.sessions / "session-a.json"
        with mock.patch.object(verbs.sys, "platform", "darwin"):
            self.assertEqual(verbs.reveal_argv(p), ["/usr/bin/open", "-R", str(p)])

    def test_empty_elsewhere_so_the_route_can_answer_501(self):
        with mock.patch.object(verbs.sys, "platform", "linux"):
            self.assertEqual(verbs.reveal_argv(self.sessions / "session-a.json"), [])


if __name__ == "__main__":
    unittest.main()
