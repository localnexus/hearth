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
 7. intent-primed boot recall: capture writes a 0600 slot, boot steers the
    query + injects a dated line + consumes the slot; "none"/off/stale/broken
    all leave the boot byte-identical (the LLM is always mocked — no network)
 8. the hindsight sidecar survives its own child (incident 2026-08-30): the
    child's stdout+stderr land in a 0600 logfile in a 0700 dir and the pipe is
    drained for life, an oversized log rotates to .1 at spawn, a dead child is
    respawned exactly ONCE (a second death raises), and close() after a death
    is quiet — all against a stub runner, no hindsight install, no network

Run:  .venv/bin/python -m unittest tests/test_memory.py
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import MemorySeam, maybe_attach  # noqa: E402
from hearth.memory.backend import MemoryItem, SessionRecord, digest_record  # noqa: E402
from hearth.memory import intent as intent_mod  # noqa: E402
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


class _QueryBackend:
    """Records the recall query the seam composed (and recalls nothing)."""

    name = "queryspy"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def recall(self, companion, query, limit):  # noqa: ANN001
        self.queries.append(query)
        return []

    def store(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def consolidate(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def close(self):
        pass


class TestIntentSlot(unittest.TestCase):
    """Intent-primed boot recall. The LLM transport is mocked in every test —
    these must make zero network calls and must not depend on today's date."""

    MSGS = [{"role": "user", "content": "next time let's talk about the tea ceremony"},
            {"role": "assistant", "content": "I'd like that."}]

    def _cfg(self, enabled: bool = True, **over) -> dict:
        cfg = {"recall_limit": 3}
        if enabled or over:
            cfg["intent"] = {"enabled": enabled, "expiry_days": 14,
                             "llm_provider": "ollama", "llm_model": "testmodel",
                             "llm_url": "", "companions": {}}
            cfg["intent"].update(over)
        return cfg

    def _seam(self, tmp: Path, cfg: dict, backend=None) -> MemorySeam:
        backend = backend if backend is not None else FloorBackend(tmp)
        seam = MemorySeam("testchar", "default", backend, cfg)
        seam._floor = FloorBackend(tmp)
        return seam

    def test_capture_then_boot_steers_injects_and_consumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat",
                                   return_value="the tea ceremony") as llm:
                status = self._seam(d, self._cfg()).on_session_end(self.MSGS, store=None)
                self.assertIn("record kept", status)
                self.assertEqual(llm.call_count, 1)

                # the slot: shape + the records' own permission class
                self.assertEqual(stat.S_IMODE(slot.stat().st_mode), 0o600)
                data = json.loads(slot.read_text(encoding="utf-8"))
                self.assertEqual(data["text"], "the tea ceremony")
                self.assertTrue(data["source_session"])
                stated_day = data["stated_at"][:10]

                # next boot: query steered, dated line injected, slot consumed
                spy = _QueryBackend()
                out = self._seam(d, self._cfg(), spy).augment("SYSTEM PROMPT")
            self.assertIn("the tea ceremony", spy.queries[0])          # steered
            self.assertTrue(spy.queries[0].startswith(                  # …and standing
                "the user's life, preferences, and recent conversations"))
            self.assertIn("## MEMORY — from earlier conversations", out)
            self.assertIn(f"On {stated_day} you agreed to pick up the tea ceremony next time.",
                          out)
            self.assertFalse(slot.exists())  # consume-once

    def test_answer_none_writes_no_slot_and_boot_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat", return_value="none"):
                self._seam(d, self._cfg()).on_session_end(self.MSGS, store=None)
                self.assertFalse(slot.exists())
                out = self._seam(d, self._cfg()).augment("SYSTEM PROMPT")
                # baseline AFTER the close, so both see the same record set
                baseline = self._seam(d, self._cfg(False)).augment("SYSTEM PROMPT")
            self.assertEqual(out, baseline)

    def test_default_off_never_calls_the_llm(self):
        """No [memory.intent] ⇒ the transport is never reached and both hooks
        behave exactly as they did before the feature existed."""
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat") as llm:
                status = self._seam(d, {"recall_limit": 3}).on_session_end(
                    self.MSGS, store=None)
                out = self._seam(d, {"recall_limit": 3}).augment("SYSTEM PROMPT")
                # the pre-feature seam, same state: byte-identical composition
                baseline = MemorySeam("testchar", "default", FloorBackend(d),
                                      {"recall_limit": 3}).augment("SYSTEM PROMPT")
            llm.assert_not_called()
            self.assertIn("record kept", status)
            self.assertEqual(out, baseline)
            self.assertFalse(slot.exists())

    def test_disabled_ignores_an_existing_slot_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                intent_mod.write_slot("testchar", "the tea ceremony", "s1")
                out = self._seam(d, {"recall_limit": 3}).augment("SYSTEM PROMPT")
            self.assertEqual(out, "SYSTEM PROMPT")
            self.assertTrue(slot.exists())  # re-enabling must find the plan intact

    def test_stale_slot_is_skipped_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            slot.write_text(json.dumps({
                "schema": 1, "kind": "memory-intent", "text": "the tea ceremony",
                "stated_at": "2020-01-01T00:00:00", "source_session": "old",
            }), encoding="utf-8")
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                out = self._seam(d, self._cfg()).augment("SYSTEM PROMPT")
            self.assertEqual(out, "SYSTEM PROMPT")
            self.assertNotIn("pick up", out)
            self.assertFalse(slot.exists())

    def test_transport_failure_leaves_close_normal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat",
                                   side_effect=OSError("connection refused")):
                status = self._seam(d, self._cfg()).on_session_end(self.MSGS, store=None)
            self.assertIn("record kept", status)
            self.assertEqual(len(list(records_mod.iter_records("testchar", d))), 1)
            self.assertFalse(slot.exists())

    def test_malformed_slot_is_removed_and_boot_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            baseline = self._seam(d, self._cfg(False)).augment("SYSTEM PROMPT")
            slot.write_text("{not json", encoding="utf-8")
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                out = self._seam(d, self._cfg()).augment("SYSTEM PROMPT")
            self.assertEqual(out, baseline)
            self.assertFalse(slot.exists())

    def test_parser_is_conservative(self):
        for answer in ("", "  ", "none", "None.", "NONE", "x" * 500):
            self.assertIsNone(intent_mod.parse_answer(answer), answer[:20])
        self.assertEqual(intent_mod.parse_answer('  "the tea ceremony"\nbecause…'),
                         "the tea ceremony")
        self.assertEqual(intent_mod.parse_answer("<think>hmm</think> the tea ceremony"),
                         "the tea ceremony")


class _FakeClient:
    """Stands in for hindsight_client.Hindsight — the SDK is not installed here
    (and must never be needed to test the adapter's process plumbing)."""

    def __init__(self, url: str | None) -> None:
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


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
                                  "runner": str(stub), "llm_model": "m",
                                  "log_file": str(Path(tmp) / "logs" / "sidecar.log")})
            b._start_sidecar()
            proc = b._proc
            try:
                self.assertEqual(b._url, "http://127.0.0.1:59999")
                self.assertIsNone(proc.poll())  # still running until close
                # Own process group (start_new_session): the operator's Ctrl+C
                # must never reach the sidecar (run-observed 2026-08-30 — the
                # terminal's SIGINT killed it before the close-time store).
                self.assertNotEqual(os.getpgid(proc.pid), os.getpgid(os.getpid()))
            finally:
                b.close()
            self.assertIsNotNone(proc.poll())   # terminated by close
            self.assertIsNone(b._proc)

    def test_sidecar_requires_python_path(self):
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "llm_model": "m"})
        with self.assertRaises(ValueError):
            b._start_sidecar()

    def test_call_pins_one_persistent_thread_in_async_context(self):
        """Regression (run-observed 2026-08-30, first in-bot store): the SDK
        caches an aiohttp ClientSession on the first call's event loop, so all
        async-context calls must share ONE persistent worker thread — per-call
        threads leave the session on a dead loop (RuntimeError on call #2)."""
        from hearth.memory.backend_hindsight import HindsightBackend

        b = HindsightBackend({"mode": "sidecar", "llm_model": "m"})
        idents: list[int] = []

        async def scenario():
            idents.append(b._call(threading.get_ident))
            idents.append(b._call(threading.get_ident))

        asyncio.run(scenario())
        self.assertEqual(idents[0], idents[1])            # same worker thread
        self.assertNotEqual(idents[0], threading.get_ident())  # not the caller's
        b.close()  # shuts the pool with no client/proc — must not raise
        # sync context (CLI rebuild) goes straight through on the caller's thread
        self.assertEqual(b._call(threading.get_ident), threading.get_ident())

    # ── the 2026-08-30 incident: a child that died blind and undrained ───────

    _NOISY = (
        "import sys, time\n"
        "print('startup noise', flush=True)\n"
        "print('HINDSIGHT_URL=http://127.0.0.1:59999', flush=True)\n"
        "print('post-handshake stdout line', flush=True)\n"
        "sys.stderr.write('stderr complaint\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )
    _DIES = "import sys\nsys.exit(3)\n"

    def _stub(self, tmp: Path, name: str, src: str) -> Path:
        path = tmp / f"{name}.py"
        path.write_text(src, encoding="utf-8")
        return path

    def _backend(self, tmp: Path, runner: Path, log: Path):
        from hearth.memory.backend_hindsight import HindsightBackend
        return HindsightBackend({"mode": "sidecar", "python": sys.executable,
                                 "runner": str(runner), "llm_model": "m",
                                 "log_file": str(log)})

    def _wait_for(self, log: Path, needle: str, timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        text = ""
        while time.monotonic() < deadline:
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            if needle in text:
                return text
            time.sleep(0.05)
        return text

    def test_child_stdout_and_stderr_land_in_the_logfile_at_0600(self):
        """Both holes from the incident, in one run: stderr no longer goes to
        DEVNULL, and stdout keeps being drained after the handshake line."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            b._start_sidecar()
            try:
                text = self._wait_for(log, "stderr complaint")
            finally:
                b.close()
            self.assertIn("startup noise", text)              # pre-handshake stdout
            self.assertIn("post-handshake stdout line", text)  # the drain thread
            self.assertIn("stderr complaint", text)            # stderr, not DEVNULL
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)
            self.assertIsNone(b._log)                          # handle released by close()

    def test_oversized_log_rotates_to_dot_one_at_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            log.parent.mkdir(parents=True)
            log.write_text("x" * (5 * 1024 * 1024 + 1), encoding="utf-8")
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            b._start_sidecar()
            b.close()
            rotated = log.with_name(log.name + ".1")
            self.assertTrue(rotated.is_file())
            self.assertGreater(rotated.stat().st_size, 5 * 1024 * 1024)
            self.assertLess(log.stat().st_size, 4096)          # a fresh generation

    def test_dead_sidecar_respawns_once_and_a_second_death_raises(self):
        """The store at session close used to die on ClientConnectorError when
        the child was gone (run-observed). _ensure now notices and respawns —
        once. The SDK is absent here, so _new_client is the seam."""
        from hearth.memory import backend_hindsight as hs
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            clients: list[_FakeClient] = []
            b._new_client = lambda: clients.append(_FakeClient(b._url)) or clients[-1]

            b._ensure()
            first = b._proc
            self.assertEqual(len(clients), 1)
            b._ensure()                       # alive: no respawn, no new client
            self.assertIs(b._proc, first)
            self.assertEqual(len(clients), 1)

            first.kill()
            first.wait()
            with mock.patch.object(hs.logger, "warning") as warn:
                b._ensure()
            observed = [c.args[1] for c in warn.call_args_list
                        if "died (rc=" in str(c.args[0])]
            self.assertEqual(observed, [first.returncode])     # the old rc was named
            self.assertIsNot(b._proc, first)                   # exactly one respawn
            self.assertIsNone(b._proc.poll())
            self.assertEqual(len(clients), 2)
            self.assertTrue(clients[0].closed)                 # stale client retired

            # a sidecar that cannot come back propagates instead of looping
            b._cfg["runner"] = str(self._stub(tmp, "dies", self._DIES))
            b._proc.kill()
            b._proc.wait()
            with self.assertRaises(RuntimeError):
                b._ensure()
            b.close()

    def test_close_after_child_death_skips_terminate_and_resets(self):
        from hearth.memory import backend_hindsight as hs
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "logs" / "hindsight-sidecar.log"
            b = self._backend(tmp, self._stub(tmp, "noisy", self._NOISY), log)
            b._start_sidecar()
            proc = b._proc
            proc.kill()
            proc.wait()
            with mock.patch.object(hs.logger, "warning") as warn:
                b.close()                                      # must not raise
            self.assertTrue(any("already exited" in str(c.args[0])
                                for c in warn.call_args_list))
            self.assertIsNone(b._proc)
            self.assertIsNone(b._client)
            self.assertIsNone(b._url)
            self.assertIsNone(b._log)
            self.assertFalse(b._drain.is_alive() if b._drain else False)


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

    def test_intent_defaults_off_and_inherit_hindsight_llm(self):
        """Absent [memory.intent] ⇒ normalized, disabled. Present ⇒ its LLM
        settings fall back to the extraction model [memory.hindsight] names."""
        res = self._run("[memory]\nenabled = true\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        intent = json.loads(res.stdout)["cfg"]["intent"]
        self.assertFalse(intent["enabled"])
        self.assertEqual(intent["expiry_days"], 14)
        self.assertEqual(intent["companions"], {})

        res = self._run(
            "[memory]\nenabled = true\n"
            "[memory.intent]\nenabled = true\nexpiry_days = 7\n"
            "[memory.intent.companions]\nguest = false\n"
            "[memory.hindsight]\nllm_provider = \"ollama\"\nllm_model = \"qwen3-coder:30b\"\n"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        intent = json.loads(res.stdout)["cfg"]["intent"]
        self.assertTrue(intent["enabled"])
        self.assertEqual(intent["expiry_days"], 7)
        self.assertEqual(intent["llm_provider"], "ollama")
        self.assertEqual(intent["llm_model"], "qwen3-coder:30b")
        self.assertEqual(intent["companions"], {"guest": False})

    def test_unknown_backend_is_config_error(self):
        res = self._run("[memory]\nenabled = true\nbackend = \"warpdrive\"\n")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ConfigError", res.stderr)
        self.assertIn("memory.toml", res.stderr)


if __name__ == "__main__":
    unittest.main()
