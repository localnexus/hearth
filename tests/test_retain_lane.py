"""test_retain_lane.py — the two-switch store: retain (the write lane) binds at close.

Runs WITHOUT config or a data root — every store here is built with an explicit
``sessions_dir`` under ``tempfile``, so ``SessionStore.__post_init__``'s
``companion_sessions_dir()`` fallback (which needs config/active.toml) is never
exercised.

Run:  .venv/bin/python -m unittest discover -s tests -p "test_retain_lane.py"
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from hearth.session import session_store as ss


def _store(tmp, sid, **kw):
    kw.setdefault("held", False)
    kw.setdefault("retain", False)
    return ss.SessionStore(session_id=sid, model="m", voice="v", prompt_sha256="d",
                           sessions_dir=Path(tmp), **kw)


class RetainLane(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)

    def test_unkept_by_default_deletes_at_close(self):
        st = _store(self.d, "session-a")
        st.snapshot([{"role": "user", "content": "x"}])
        self.assertTrue(st.path.exists())
        status = ss.finalize(st, [{"role": "user", "content": "x"}])
        self.assertFalse(st.path.exists())
        self.assertIn("not kept", status)

    def test_retain_keeps_and_marks_held(self):
        st = _store(self.d, "session-b", retain=True)
        st.snapshot([{"role": "user", "content": "x"}])
        status = ss.finalize(st, [{"role": "user", "content": "x"}])
        self.assertTrue(st.path.exists())
        self.assertTrue(ss.load(st.path)["held"])
        self.assertIn("kept", status)

    def test_legacy_files_read_as_kept_unless_recall_only(self):
        base = {"schema": 2, "model": "m", "voice": "v", "persona": "default",
                "started": "x", "updated": "x", "held": False, "messages": []}
        no_key = self.d / "no-key.json"
        ss._atomic_write_json(no_key, dict(base))
        recall_only = self.d / "recall-only.json"
        ss._atomic_write_json(recall_only, dict(base, memory_mode="recall-only"))
        explicit_false = self.d / "explicit-false.json"
        ss._atomic_write_json(explicit_false, dict(base, retain=False))

        self.assertTrue(ss._meta_of(no_key).retain, "no retain key -> reads as kept")
        self.assertFalse(ss._meta_of(recall_only).retain, "recall-only, no key -> unkept")
        self.assertFalse(ss._meta_of(explicit_false).retain, "explicit retain:false -> unkept")

    def test_retain_marker_is_consumed_and_a_stale_id_is_ignored(self):
        st = _store(self.d, "session-c")
        st.snapshot([{"role": "user", "content": "x"}])
        ss.write_retain_request(True, sessions_dir=self.d, session_id="session-c")
        out = {}
        ss.finalize(st, [{"role": "user", "content": "x"}], outcome=out)
        self.assertTrue(st.path.exists(), "a matching session_id is honored")
        self.assertNotIn("stale_request", out)
        self.assertFalse(ss.retain_marker_path(self.d).exists(), "the marker is consumed")

        st2 = _store(self.d, "session-d")
        st2.snapshot([{"role": "user", "content": "x"}])
        ss.write_retain_request(True, sessions_dir=self.d, session_id="not-session-d")
        out2 = {}
        ss.finalize(st2, [{"role": "user", "content": "x"}], outcome=out2)
        self.assertTrue(out2.get("stale_request"), "a mismatched session_id is flagged stale")
        self.assertFalse(st2.path.exists(), "the store's own (default) retain decided instead")

    def test_expiry_sweep_spares_young_orphans(self):
        young = _store(self.d, "session-young")
        young.snapshot([{"role": "user", "content": "x"}])
        old = _store(self.d, "session-old")
        old.snapshot([{"role": "user", "content": "x"}])
        data = ss.load(old.path)
        data["updated"] = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S")
        ss._atomic_write_json(old.path, data)

        removed = ss.discard_ephemeral(self.d)
        self.assertEqual(removed, ["session-old"])
        self.assertTrue(young.path.exists(), "a young orphan survives the sweep")
        self.assertFalse(old.path.exists(), "the expired orphan is gone")


if __name__ == "__main__":
    unittest.main()
