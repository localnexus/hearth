"""Memory-seam invariants (hearth/memory/).

 1. digest is deterministic, extractive, and blind to non-user/assistant roles
 2. canonical records round-trip atomically at 0600/0700, malformed files skip
 3. floor recall: newest-first, limit honored, provenance on every item
 4. augment: byte-identical with no items; dated block with items
 5. containment (decider 6): a failing backend degrades to floor, never raises,
    and the canonical record is still written when the backend index fails
 6. the config gate: absent/disabled ⇒ None; enabled ⇒ defaults + per-companion
    map; "none" opts a companion out; unknown backend ⇒ ConfigError
    (subprocess-run with a scratch HEARTH_DATA — anchors resolve at import)

Run:  .venv/bin/python -m unittest tests/test_memory.py
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import MemorySeam, maybe_attach  # noqa: E402
from hearth.memory.backend import MemoryItem, SessionRecord, digest_record  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory.floor import FloorBackend  # noqa: E402


def _record(sid: str, ended: str, n_turns: int = 2, name: str = "") -> SessionRecord:
    messages = []
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"user line {i} of {sid}"})
        messages.append({"role": "assistant", "content": f"assistant line {i} of {sid}"})
    return SessionRecord(
        companion="testchar", session_id=sid, started="2026-08-29T10:00:00",
        ended=ended, name=name, messages=messages,
    )


class TestDigest(unittest.TestCase):
    def test_deterministic_and_extractive(self):
        r = _record("s1", "2026-08-29T11:00:00", name="tea talk")
        d1, d2 = digest_record(r), digest_record(r)
        self.assertEqual(d1, d2)
        self.assertIn("tea talk", d1)
        self.assertIn("user line 0 of s1", d1)      # opening line
        self.assertIn("assistant line 1 of s1", d1)  # closing line
        self.assertIn("2 exchanges", d1)

    def test_ignores_system_and_junk(self):
        r = SessionRecord(
            companion="c", session_id="s", started="", ended="",
            messages=[{"role": "system", "content": "SECRET ENVELOPE"},
                      {"role": "user", "content": "hello"},
                      {"role": "assistant", "content": "hi"}],
        )
        self.assertNotIn("SECRET ENVELOPE", digest_record(r))

    def test_skips_compaction_meta_as_representative_line(self):
        # Run-observed 2026-08-30: a compacted session's first user message is the
        # tool's meta-marker; the digest surfaced it (backup path included) as the
        # companion's remembered opening line. It must be skipped, not remembered.
        r = SessionRecord(
            companion="c", session_id="s", started="", ended="2026-08-30T03:00:00",
            messages=[
                {"role": "user", "content": "[session compact applied — full transcript "
                                            "backed up under pre-compaction-bak/2026.08.30/s.json; "
                                            "continue as established partners]"},
                {"role": "assistant", "content": "compact body summary"},
                {"role": "user", "content": "real opening line"},
                {"role": "assistant", "content": "real reply"},
            ],
        )
        d = digest_record(r)
        self.assertNotIn("session compact", d)
        self.assertNotIn("pre-compaction-bak", d)
        self.assertIn("real opening line", d)
        self.assertIn("1 exchange", d)  # meta line no longer counts as a user turn


class TestRecords(unittest.TestCase):
    def test_roundtrip_perms_order_and_malformed_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "records"
            p1 = records_mod.write_record(_record("s1", "2026-08-28T20:00:00"), d)
            p2 = records_mod.write_record(_record("s2", "2026-08-29T20:00:00"), d)
            self.assertEqual(stat.S_IMODE(p1.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
            (d / "junk.json").write_text("{not json", encoding="utf-8")
            (d / "wrongkind.json").write_text(json.dumps({"kind": "other"}), encoding="utf-8")
            newest = list(records_mod.iter_records("testchar", d, newest_first=True))
            self.assertEqual([r.session_id for r in newest], ["s2", "s1"])
            self.assertEqual(records_mod.load_record(p2).messages,
                             _record("s2", "2026-08-29T20:00:00").messages)

    def test_idempotent_per_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            records_mod.write_record(_record("s1", "2026-08-28T20:00:00"), d)
            records_mod.write_record(_record("s1", "2026-08-28T21:00:00"), d)
            got = list(records_mod.iter_records("testchar", d))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].ended, "2026-08-28T21:00:00")


class TestFloor(unittest.TestCase):
    def test_recall_provenance_limit_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for sid, ended in (("a", "2026-08-27T09:00:00"),
                               ("b", "2026-08-28T09:00:00"),
                               ("c", "2026-08-29T09:00:00")):
                records_mod.write_record(_record(sid, ended), d)
            items = FloorBackend(d).recall("testchar", "ignored", 2)
            self.assertEqual(len(items), 2)
            self.assertEqual([i.source_session for i in items], ["c", "b"])
            for i in items:
                self.assertIsInstance(i, MemoryItem)
                self.assertTrue(i.when.startswith("2026-08-2"))
                self.assertTrue(i.text)


class _BoomBackend:
    name = "boom"

    def recall(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("backend down")

    def store(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("backend down")

    def consolidate(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("backend down")

    def close(self):
        raise RuntimeError("backend down")


class TestSeam(unittest.TestCase):
    def _seam(self, backend, floor_dir: Path) -> MemorySeam:
        seam = MemorySeam("testchar", "default", backend, {"recall_limit": 3})
        seam._floor = FloorBackend(floor_dir)
        return seam

    def test_augment_byte_identical_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            seam = self._seam(FloorBackend(Path(tmp)), Path(tmp))
            base = "SYSTEM PROMPT"
            self.assertEqual(seam.augment(base), base)

    def test_augment_injects_dated_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            seam = self._seam(FloorBackend(d), d)
            out = seam.augment("SYSTEM PROMPT")
            self.assertTrue(out.startswith("SYSTEM PROMPT"))
            self.assertIn("## MEMORY — from earlier conversations", out)
            self.assertIn("(2026-08-29)", out)

    def test_recall_degrades_to_floor_then_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            seam = self._seam(_BoomBackend(), d)
            items = seam.recall()   # backend raises → floor answers
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].source_session, "s1")
            seam_empty = self._seam(_BoomBackend(), Path(tmp) / "nowhere")
            self.assertEqual(seam_empty.recall(), [])

    def test_session_end_keeps_record_despite_backend_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            seam = self._seam(_BoomBackend(), d)
            msgs = [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"}]
            with mock.patch.object(records_mod, "records_dir", return_value=d):
                status = seam.on_session_end(msgs, store=None)
            self.assertIn("record kept", status)
            self.assertIn("index skipped", status)
            self.assertEqual(len(list(records_mod.iter_records("testchar", d))), 1)
            seam.close()  # contained close must not raise

    def test_session_end_skips_empty_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            seam = self._seam(FloorBackend(d), d)
            with mock.patch.object(records_mod, "records_dir", return_value=d):
                self.assertEqual(seam.on_session_end([], store=None), "")
            self.assertEqual(list(records_mod.iter_records("testchar", d)), [])


class TestHindsightSidecar(unittest.TestCase):
    """Sidecar plumbing only — no hindsight install needed: a stub runner stands
    in for the real server (spawn → parse HINDSIGHT_URL → terminate)."""

    def test_spawn_parse_terminate(self):
        from hearth.memory.backend_hindsight import HindsightBackend
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub_runner.py"
            stub.write_text(
                "import time\n"
                "print('startup noise', flush=True)\n"
                "print('HINDSIGHT_URL=http://127.0.0.1:59999', flush=True)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            b = HindsightBackend({"mode": "sidecar", "python": sys.executable,
                                  "runner": str(stub), "llm_model": "m"})
            b._start_sidecar()
            proc = b._proc
            try:
                self.assertEqual(b._url, "http://127.0.0.1:59999")
                self.assertIsNone(proc.poll())  # still running until close
            finally:
                b.close()
            self.assertIsNotNone(proc.poll())   # terminated by close
            self.assertIsNone(b._proc)

    def test_sidecar_requires_python_path(self):
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "llm_model": "m"})
        with self.assertRaises(ValueError):
            b._start_sidecar()


# ── the config gate: anchors resolve at import ⇒ subprocess (test_data_root shape) ──

_GATE_PROBE = """
import json
from hearth.config import config_loader
from hearth import memory
cfg = config_loader.load_memory_config()
out = {"cfg": cfg}
if cfg is not None:
    seam_ani = memory.maybe_attach("ani")
    seam_off = memory.maybe_attach("guest")
    out["ani_backend"] = seam_ani.backend.name if seam_ani else None
    out["guest_attached"] = seam_off is not None
print(json.dumps(out))
"""


class TestConfigGate(unittest.TestCase):
    def _run(self, memory_toml: str | None, probe: str = _GATE_PROBE):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config").mkdir()
            if memory_toml is not None:
                (Path(tmp) / "config" / "memory.toml").write_text(memory_toml, encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("HEARTH_ROOT", "HEARTH_DATA")}
            env["HEARTH_DATA"] = tmp
            env["PYTHONPATH"] = str(REPO / "src")
            return subprocess.run([sys.executable, "-c", probe], capture_output=True,
                                  text=True, env=env, cwd=str(REPO))

    def test_absent_and_disabled_mean_none(self):
        for toml in (None, "[memory]\nenabled = false\n"):
            res = self._run(toml)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIsNone(json.loads(res.stdout)["cfg"])

    def test_enabled_defaults_and_per_companion_map(self):
        res = self._run(
            "[memory]\nenabled = true\n"
            "[memory.companions]\nguest = \"none\"\n"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(out["cfg"]["backend"], "floor")
        self.assertEqual(out["cfg"]["recall_limit"], 6)
        self.assertEqual(out["ani_backend"], "floor")
        self.assertFalse(out["guest_attached"])

    def test_unknown_backend_is_config_error(self):
        res = self._run("[memory]\nenabled = true\nbackend = \"warpdrive\"\n")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ConfigError", res.stderr)
        self.assertIn("memory.toml", res.stderr)


if __name__ == "__main__":
    unittest.main()
