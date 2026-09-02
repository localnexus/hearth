"""Memory-seam invariants (hearth/memory/).

 1. digest is deterministic, extractive, and blind to non-user/assistant roles
 2. canonical records round-trip atomically at 0600/0700, malformed files skip
 3. floor recall: newest-first, limit honored, provenance on every item
 4. augment: byte-identical with no items; dated block with items
 5. containment: a failing backend degrades to floor, never raises,
    and the canonical record is still written when the backend index fails
 6. the config gate: absent/disabled ⇒ None; enabled ⇒ defaults + per-companion
    map; "none" opts a companion out; unknown backend ⇒ ConfigError
    (subprocess-run with a scratch HEARTH_DATA — anchors resolve at import)
 7. intent-primed boot recall: capture writes a 0600 slot, boot steers the
    query + injects a dated line + consumes the slot; "none"/off/stale/broken
    all leave the boot byte-identical (the LLM is always mocked — no network)
 8. closure-gated capture: ONE extraction call answers {closure, topic}; the
    JSON parser is hostile (think-tags, prose, bad types, over-long topics) and
    capture writes a slot ONLY when a topic was stated — a bare close writes
    nothing, and no closure writes nothing
 9. the facade-lane glue (serve/memory_glue.py): session keys + hint
    sanitization, open → append → checkpoint → close (record named
    "facade <channel>", checkpoint removed), the idle sweep at 5 min voice /
    480 min chat, orphan finalization, the closure-close staleness guard,
    client-declared identity, the internal-request bypass, and containment —
    all offline, no sockets, no sleeps, an injected clock instead of waiting
10. the hindsight sidecar survives its own child (incident 2026-08-30): the
    child's stdout+stderr land in a 0600 logfile in a 0700 dir and the pipe is
    drained for life, an oversized log rotates to .1 at spawn, a dead child is
    respawned exactly ONCE (a second death raises), and close() after a death
    is quiet — all against a stub runner, no hindsight install, no network

Run:  .venv/bin/python -m unittest tests/test_memory.py
"""

from __future__ import annotations

import asyncio
import hashlib
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

from hearth import memory as seam_mod  # noqa: E402
from hearth.memory import MemorySeam, maybe_attach  # noqa: E402
from hearth.memory.backend import MemoryItem, SessionRecord, digest_record  # noqa: E402
from hearth.memory import intent as intent_mod  # noqa: E402
from hearth.memory import records as records_mod  # noqa: E402
from hearth.memory import __main__ as memory_cli  # noqa: E402
from hearth.memory.floor import FloorBackend  # noqa: E402
from hearth.serve import app as serve_app  # noqa: E402
from hearth.serve import memory_glue as glue_mod  # noqa: E402


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
                 mock.patch.object(
                     intent_mod, "_ollama_chat",
                     return_value='{"closure": true, "topic": "the tea ceremony"}') as llm:
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
        """A deliberate close that named no topic: the model says so, and the
        slot stays absent — she was told goodbye, not what to pick up."""
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat",
                                   return_value='{"closure": true, "topic": "none"}'):
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


class _CurationDocsApi:
    """The SDK's low-level DocumentsApi stand-in — its delete is async-only,
    which is exactly what the adapter's sync bridge exists to cross."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    async def delete_document(self, bank_id: str, document_id: str) -> None:
        self.calls.append((bank_id, document_id))
        if self._fail is not None:
            raise self._fail


class _CurationClient:
    """Hindsight SDK stand-in for the keyed-store/forget/clear surface."""

    def __init__(self, docs: _CurationDocsApi | None = None) -> None:
        self.retains: list[dict] = []
        self.deleted_banks: list[str] = []
        self.documents = docs if docs is not None else _CurationDocsApi()

    def retain(self, **kwargs) -> None:  # noqa: ANN003
        self.retains.append(kwargs)

    def delete_bank(self, bank_id: str) -> None:
        self.deleted_banks.append(bank_id)

    def close(self) -> None:
        pass


class _NotFound(Exception):
    status = 404  # the generated ApiException carries HTTP status here


class TestHindsightCuration(unittest.TestCase):
    """Record-level curation (D1/D2/D4): the store is session-keyed and
    replacing, forget cascade-deletes one session's document, clear drops the
    bank. Client-level fakes only — no hindsight install, no sidecar spawn."""

    def _backend(self, client):
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "python": sys.executable,
                              "llm_model": "m"})
        b._client = client  # bound directly: _ensure sees a live client, no spawn
        return b

    def test_store_is_keyed_replacing_and_dated(self):
        from datetime import datetime
        client = _CurationClient()
        rec = _record("session-a", "2026-09-01T10:30:00+02:00")
        self._backend(client).store("testchar", rec)
        (kw,) = client.retains
        self.assertEqual(kw["bank_id"], "testchar")
        self.assertEqual(kw["document_id"], "session-a")
        self.assertEqual(kw["update_mode"], "replace")
        self.assertEqual(kw["timestamp"],
                         datetime.fromisoformat("2026-09-01T10:30:00+02:00"))

    def test_store_with_unparsable_ended_still_stores_undated(self):
        client = _CurationClient()
        self._backend(client).store("testchar", _record("session-b", "not-a-date"))
        (kw,) = client.retains
        self.assertEqual(kw["document_id"], "session-b")
        self.assertIsNone(kw["timestamp"])

    def test_forget_cascade_deletes_the_session_document(self):
        docs = _CurationDocsApi()
        b = self._backend(_CurationClient(docs))
        self.assertTrue(b.forget("testchar", "session-a"))
        self.assertEqual(docs.calls, [("testchar", "session-a")])

    def test_forget_unkeyed_session_reports_false(self):
        # Facts stored before keyed retain have no document — the server 404s
        # and the verb must say "not excised", never pretend.
        b = self._backend(_CurationClient(_CurationDocsApi(fail=_NotFound())))
        self.assertFalse(b.forget("testchar", "pre-keying-session"))

    def test_forget_transport_errors_still_raise(self):
        b = self._backend(_CurationClient(_CurationDocsApi(fail=RuntimeError("boom"))))
        with self.assertRaises(RuntimeError):
            b.forget("testchar", "session-a")

    def test_clear_deletes_the_bank(self):
        client = _CurationClient()
        self._backend(client).clear("testchar")
        self.assertEqual(client.deleted_banks, ["testchar"])

    def test_floor_curation_is_trivially_complete(self):
        f = FloorBackend()
        self.assertTrue(f.forget("testchar", "anything"))
        f.clear("testchar")  # no-op, must not raise


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


class TestCurationCLI(unittest.TestCase):
    """forget --session / rebuild --clean (record-level curation, D2/D3/D4):
    the confirm gate previews without deleting, backend-first ordering keeps a
    failed forget re-runnable, and --clean wipes exactly once before replay."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for sid, ended in (("session-1", "2026-08-30T10:00:00"),
                           ("session-2", "2026-08-31T10:00:00")):
            records_mod.write_record(_record(sid, ended), directory=self.dir)
        patcher = mock.patch.object(records_mod, "records_dir", lambda c: self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _with_seam(self, backend):
        import types
        seam = types.SimpleNamespace(backend=backend, close=lambda: None) \
            if backend is not None else None
        return mock.patch.object(seam_mod, "maybe_attach", lambda c: seam)

    def test_forget_without_yes_previews_and_deletes_nothing(self):
        def _boom(c):  # the seam must not even attach pre-confirm
            raise AssertionError("maybe_attach called before --yes")
        with mock.patch.object(seam_mod, "maybe_attach", _boom):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=False)
        self.assertEqual(rc, 1)
        self.assertTrue((self.dir / "session-1.json").is_file())

    def test_forget_yes_excises_and_deletes(self):
        backend = _CLIBackend(forget_result=True)
        with self._with_seam(backend):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 0)
        self.assertEqual(backend.forgets, [("testchar", "session-1")])
        self.assertFalse((self.dir / "session-1.json").exists())
        self.assertTrue((self.dir / "session-2.json").is_file())  # only the named one

    def test_forget_unkeyed_deletes_record_but_signals_rebuild(self):
        backend = _CLIBackend(forget_result=False)
        with self._with_seam(backend):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 1)  # not fully excised — the operator must know
        self.assertFalse((self.dir / "session-1.json").exists())

    def test_forget_backend_failure_keeps_the_record(self):
        backend = _CLIBackend(forget_raises=RuntimeError("bank down"))
        with self._with_seam(backend):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 1)
        self.assertTrue((self.dir / "session-1.json").is_file())  # re-runnable

    def test_forget_memory_disabled_still_deletes_the_record(self):
        with self._with_seam(None):
            rc = memory_cli._cmd_forget("testchar", "session-1", yes=True)
        self.assertEqual(rc, 0)
        self.assertFalse((self.dir / "session-1.json").exists())

    def test_forget_unknown_session_is_an_error(self):
        with self._with_seam(_CLIBackend()):
            rc = memory_cli._cmd_forget("testchar", "no-such-session", yes=True)
        self.assertEqual(rc, 1)

    def test_rebuild_clean_without_yes_wipes_nothing(self):
        backend = _CLIBackend()
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar", clean=True, yes=False)
        self.assertEqual(rc, 1)
        self.assertEqual(backend.clears, [])
        self.assertEqual(backend.stored, [])

    def test_rebuild_clean_yes_wipes_once_then_replays_oldest_first(self):
        backend = _CLIBackend()
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar", clean=True, yes=True)
        self.assertEqual(rc, 0)
        self.assertEqual(backend.clears, ["testchar"])
        self.assertEqual(backend.stored, ["session-1", "session-2"])

    def test_rebuild_plain_is_unchanged_additive_replay(self):
        backend = _CLIBackend()
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar")
        self.assertEqual(rc, 0)
        self.assertEqual(backend.clears, [])
        self.assertEqual(backend.stored, ["session-1", "session-2"])

    def test_rebuild_clean_failed_wipe_replays_nothing(self):
        backend = _CLIBackend(clear_raises=RuntimeError("bank down"))
        with self._with_seam(backend):
            rc = memory_cli._cmd_rebuild("testchar", clean=True, yes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(backend.stored, [])


class TestClosureDetection(unittest.TestCase):
    """The ONE extraction call, parsed hostilely. The transport is mocked in
    every test — these make zero network calls."""

    CFG = {"llm_provider": "ollama", "llm_model": "testmodel", "llm_url": ""}
    MSGS = [{"role": "user", "content": "goodnight"},
            {"role": "assistant", "content": "sleep well"}]

    def _detect(self, answer):
        with mock.patch.object(intent_mod, "_ollama_chat", return_value=answer) as llm:
            got = intent_mod.detect_closure_and_topic(self.MSGS, self.CFG)
        self.assertEqual(llm.call_count, 1)  # one call answers both questions
        return got

    def test_good_json(self):
        self.assertEqual(self._detect('{"closure": true, "topic": "the tea ceremony"}'),
                         (True, "the tea ceremony"))
        self.assertEqual(self._detect('{"closure": false, "topic": null}'), (False, None))
        self.assertEqual(self._detect('{"closure": true, "topic": null}'), (True, None))
        self.assertEqual(self._detect('{"closure": false, "topic": "the tea ceremony"}'),
                         (False, "the tea ceremony"))

    def test_think_tags_fences_and_commentary_are_scanned_past(self):
        noisy = ("<think>they said goodnight</think>\n"
                 "```json\n"
                 '{"closure": true, "topic": "the tea ceremony"}\n'
                 "```\n"
                 "Hope that helps.")
        self.assertEqual(self._detect(noisy), (True, "the tea ceremony"))

    def test_a_brace_inside_the_topic_does_not_end_the_scan(self):
        self.assertEqual(self._detect('{"closure": true, "topic": "the {tea} ceremony"}'),
                         (True, "the {tea} ceremony"))

    def test_malformed_answers_conclude_nothing(self):
        for answer in ("", "none", "yes, they said goodbye", "{not json}",
                       '{"closure": "yes", "topic": "x"}', '["closure"]',
                       '{"topic": "the tea ceremony"}'):
            self.assertEqual(self._detect(answer), (False, None), answer[:28])

    def test_an_unusable_topic_is_dropped_but_the_closure_verdict_survives(self):
        # A rambling or mistyped topic says nothing about whether the user
        # actually said goodbye — the two answers fail independently.
        self.assertEqual(self._detect(json.dumps({"closure": True, "topic": "x" * 500})),
                         (True, None))
        self.assertEqual(self._detect('{"closure": true, "topic": 42}'), (True, None))
        self.assertEqual(self._detect('{"closure": true, "topic": "none"}'), (True, None))

    def test_no_seat_never_reaches_the_transport(self):
        with mock.patch.object(intent_mod, "_ollama_chat") as llm:
            self.assertEqual(
                intent_mod.detect_closure_and_topic(self.MSGS, {"llm_provider": "openai"}),
                (False, None))
            self.assertEqual(
                intent_mod.detect_closure_and_topic(self.MSGS, {"llm_provider": "ollama"}),
                (False, None))
        llm.assert_not_called()

    def test_transport_failure_concludes_nothing(self):
        with mock.patch.object(intent_mod, "_ollama_chat", side_effect=OSError("refused")):
            self.assertEqual(intent_mod.detect_closure_and_topic(self.MSGS, self.CFG),
                             (False, None))


class TestCaptureGating(unittest.TestCase):
    """capture()'s three outcomes: only a STATED topic keeps anything."""

    CFG = {"llm_provider": "ollama", "llm_model": "testmodel", "llm_url": ""}
    MSGS = [{"role": "user", "content": "goodnight — next time the tea ceremony"},
            {"role": "assistant", "content": "I'd like that."}]

    def _capture(self, answer):
        with tempfile.TemporaryDirectory() as tmp:
            slot = Path(tmp) / "intent.json"
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(intent_mod, "_ollama_chat", return_value=answer):
                topic = intent_mod.capture("testchar", self.MSGS, "s1", self.CFG)
            return topic, slot.exists()

    def test_stated_topic_writes_the_slot(self):
        self.assertEqual(self._capture('{"closure": true, "topic": "the tea ceremony"}'),
                         ("the tea ceremony", True))

    def test_topic_without_closure_still_writes(self):
        # A plan named mid-conversation is still the plan.
        self.assertEqual(self._capture('{"closure": false, "topic": "the tea ceremony"}'),
                         ("the tea ceremony", True))

    def test_closure_without_a_topic_writes_nothing(self):
        self.assertEqual(self._capture('{"closure": true, "topic": null}'), (None, False))

    def test_no_closure_writes_nothing(self):
        # The gate the facade lane needs: an idle timeout is not a goodbye.
        self.assertEqual(self._capture('{"closure": false, "topic": null}'), (None, False))


class _StubBackend:
    """A backend that records what it was handed and nothing else."""

    name = "stub"

    def __init__(self) -> None:
        self.stored: list = []
        self.consolidated = 0
        self.closed = 0

    def recall(self, companion, query, limit):  # noqa: ANN001
        return []

    def store(self, companion, record):  # noqa: ANN001
        self.stored.append(record)

    def consolidate(self, companion):  # noqa: ANN001
        self.consolidated += 1

    def close(self):
        self.closed += 1


class TestServeGlue(unittest.TestCase):
    """The facade-lane session manager (serve/memory_glue.py).

    Fully offline: a stub backend, a scratch data tree, and an INJECTED clock so
    the idle sweep is tested without waiting on one.
    """

    KEY = ("testchar", "chat", "")

    def _cfg(self, **serve_over) -> dict:
        serve = {"enabled": True, "idle_close_voice": 5,
                 "idle_close_chat": 480, "checkpoint": True}
        serve.update(serve_over)
        return {"recall_limit": 3, "backend": "stub", "companions": {}, "serve": serve,
                "intent": {"enabled": False, "expiry_days": 14, "llm_provider": "ollama",
                           "llm_model": "testmodel", "llm_url": "", "companions": {}}}

    def _env(self, tmp: Path, backend=None):
        """Point the glue at a scratch tree; returns (checkpoint root, records, backend)."""
        root, recs = tmp / "characters", tmp / "records"
        backend = backend if backend is not None else _StubBackend()
        for patcher in (
            mock.patch.object(glue_mod, "_checkpoint_root", return_value=root),
            mock.patch.object(glue_mod, "_build_backend", return_value=backend),
            mock.patch.object(records_mod, "records_dir", return_value=recs),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return root, recs, backend

    def test_open_append_checkpoint_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)
            checkpoint = root / "testchar" / "memory" / "checkpoints" / "serve-chat.json"

            async def scenario():
                base = "SYSTEM PROMPT"
                # nothing recalled ⇒ the instruction is byte-identical
                self.assertEqual(
                    await glue.instruction("testchar", "default", None, None, base), base)
                self.assertIn(self.KEY, glue._sessions)
                # a later turn of the same conversation reuses the entry
                self.assertEqual(
                    await glue.instruction("testchar", "default", "chat", "", base), base)
                self.assertEqual(len(glue._sessions), 1)

                glue.note_exchange("testchar", "chat", "", "hello", "hi there")
                await glue.drain()
                self.assertTrue(checkpoint.is_file())
                self.assertEqual(stat.S_IMODE(checkpoint.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(checkpoint.parent.stat().st_mode), 0o700)
                self.assertEqual(list(checkpoint.parent.glob("*.tmp")), [])  # atomic
                data = json.loads(checkpoint.read_text(encoding="utf-8"))
                self.assertEqual(data["companion"], "testchar")
                self.assertEqual(data["channel"], "chat")
                self.assertEqual(data["persona"], "default")
                self.assertTrue(data["started"])
                self.assertTrue(data["session_id"].startswith("serve-chat-"))
                self.assertEqual(data["turns"],
                                 [{"role": "user", "content": "hello"},
                                  {"role": "assistant", "content": "hi there"}])

                self.assertEqual(backend.closed, 0)  # the seam is dropped, never closed
                await glue.close_session(self.KEY)
                self.assertFalse(checkpoint.exists())  # the transient is gone
                self.assertEqual(glue._sessions, {})
                await glue.stop()

            asyncio.run(scenario())
            got = list(records_mod.iter_records("testchar", recs))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].name, "facade chat")
            self.assertEqual(got[0].persona, "default")
            self.assertTrue(got[0].session_id.startswith("serve-chat-"))
            self.assertEqual(got[0].messages,
                             [{"role": "user", "content": "hello"},
                              {"role": "assistant", "content": "hi there"}])
            self.assertEqual(len(backend.stored), 1)
            self.assertEqual(backend.closed, 1)  # exactly once, at facade shutdown

    def test_session_hint_subdivides_the_channel_and_dirty_hints_are_hashed(self):
        dirty = "../../etc/passwd"
        digest = hashlib.sha256(dirty.encode("utf-8")).hexdigest()[:12]
        self.assertEqual(glue_mod.session_key("c", "voice", "walk-1"), ("c", "voice", "walk-1"))
        self.assertEqual(glue_mod.session_key("c", "smoke-signal", None), ("c", "chat", ""))
        self.assertEqual(glue_mod.session_key("c", "chat", dirty)[2], digest)
        self.assertEqual(glue_mod.sanitize_hint("x" * 65),
                         hashlib.sha256(("x" * 65).encode("utf-8")).hexdigest()[:12])

        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "thread-7", "P")
                await glue.instruction("testchar", "default", "chat", dirty, "P")
                self.assertEqual(sorted(glue._sessions),
                                 sorted([("testchar", "chat", "thread-7"),
                                         ("testchar", "chat", digest)]))
                glue.note_exchange("testchar", "chat", "thread-7", "a", "b")
                glue.note_exchange("testchar", "chat", dirty, "c", "d")
                await glue.drain()
                names = sorted(p.name for p in
                               (root / "testchar" / "memory" / "checkpoints").glob("*.json"))
                self.assertEqual(names, sorted([f"serve-chat-{digest}.json",
                                                "serve-chat-thread-7.json"]))
                await glue.stop()

            asyncio.run(scenario())
            ids = [r.session_id for r in records_mod.iter_records("testchar", recs)]
            self.assertEqual(len(ids), 2)
            self.assertTrue(any(i.startswith(f"serve-chat-{digest}-") for i in ids))
            self.assertTrue(any(i.startswith("serve-chat-thread-7-") for i in ids))

    def test_idle_sweep_closes_voice_at_five_minutes_and_chat_at_eight_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            now = [0.0]
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: now[0])
            voice, chat = ("testchar", "voice", ""), ("testchar", "chat", "")

            async def scenario():
                await glue.instruction("testchar", "default", "voice", "", "P")
                await glue.instruction("testchar", "default", "chat", "", "P")
                glue.note_exchange("testchar", "voice", "", "walking", "with you")
                glue.note_exchange("testchar", "chat", "", "desk line", "desk reply")
                await glue.drain()

                now[0] = 4 * 60.0
                await glue.sweep()
                self.assertEqual(sorted(glue._sessions), sorted([voice, chat]))

                now[0] = 5 * 60.0                     # idle_close_voice
                await glue.sweep()
                self.assertEqual(list(glue._sessions), [chat])

                now[0] = 479 * 60.0
                await glue.sweep()
                self.assertEqual(list(glue._sessions), [chat])

                now[0] = 480 * 60.0                   # idle_close_chat (the fallback)
                await glue.sweep()
                self.assertEqual(glue._sessions, {})
                await glue.stop()

            asyncio.run(scenario())
            self.assertEqual(sorted(r.name for r in records_mod.iter_records("testchar", recs)),
                             ["facade chat", "facade voice"])

    def test_orphan_checkpoint_becomes_a_record_stamped_at_the_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            checkpoints = root / "testchar" / "memory" / "checkpoints"
            checkpoints.mkdir(parents=True)
            path = checkpoints / "serve-voice.json"
            path.write_text(json.dumps({
                "schema": 1, "kind": "memory-checkpoint", "companion": "testchar",
                "persona": "default", "channel": "voice",
                "started": "2026-08-30T09:00:00",
                "session_id": "serve-voice-20260830T090000",
                "turns": [{"role": "user", "content": "still walking"},
                          {"role": "assistant", "content": "I am here"}],
            }), encoding="utf-8")
            crashed = time.mktime((2026, 8, 30, 9, 42, 0, 0, 0, -1))
            os.utime(path, (crashed, crashed))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.start()   # the orphan pass is scheduled, not awaited
                await glue.drain()
                await glue.stop()

            asyncio.run(scenario())
            self.assertFalse(path.exists())
            got = list(records_mod.iter_records("testchar", recs))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].session_id, "serve-voice-20260830T090000")
            self.assertEqual(got[0].name, "facade voice")
            self.assertEqual(got[0].started, "2026-08-30T09:00:00")
            # ended is when the facade DIED, not when it came back
            self.assertEqual(got[0].ended[:19], "2026-08-30T09:42:00")
            self.assertEqual(len(got[0].messages), 2)

    def test_deliberate_closure_closes_the_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "", "P")
                with mock.patch.object(glue_mod.intent_mod, "detect_closure_and_topic",
                                       return_value=(True, None)) as detect:
                    glue.note_exchange("testchar", "chat", "", "hello", "hi")
                    await glue.drain()
                    detect.assert_not_called()  # an opening exchange is never a goodbye
                    self.assertIn(self.KEY, glue._sessions)

                    glue.note_exchange("testchar", "chat", "", "goodnight", "sleep well")
                    await glue.drain()
                    self.assertEqual(detect.call_count, 1)
                self.assertEqual(glue._sessions, {})
                await glue.stop()

            asyncio.run(scenario())
            got = list(records_mod.iter_records("testchar", recs))
            self.assertEqual(len(got), 1)
            self.assertEqual(len(got[0].messages), 4)  # the whole conversation, verbatim

    def test_closure_prefilter_and_the_newer_exchange_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "", "P")
                await glue.instruction("testchar", "default", "voice", "", "P")
                glue.note_exchange("testchar", "chat", "", "hello", "hi")
                glue.note_exchange("testchar", "voice", "", "hello", "hi")
                with mock.patch.object(glue_mod.intent_mod, "detect_closure_and_topic",
                                       return_value=(True, None)) as detect:
                    glue.note_exchange("testchar", "chat", "", "what about tomorrow?", "sure")
                    glue.note_exchange("testchar", "chat", "", "x" * 400, "long one")
                    glue.note_exchange("testchar", "voice", "", "bye", "bye")
                    await glue.drain()
                    # a question, an essay, and the voice channel: none reach the seat
                    detect.assert_not_called()
                    self.assertIn(self.KEY, glue._sessions)

                    # the guard: the conversation moved on while the seat was asked
                    session = glue._sessions[self.KEY]
                    stale = session.seq
                    session.seq += 1
                    await glue._closure_check(self.KEY, session, stale)
                    self.assertIn(self.KEY, glue._sessions)  # skipped — it continued
                    await glue._closure_check(self.KEY, session, session.seq)
                    self.assertNotIn(self.KEY, glue._sessions)  # current ⇒ closed
                await glue.stop()

            asyncio.run(scenario())

    def test_companion_mapped_to_none_gets_no_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            cfg = self._cfg()
            cfg["companions"] = {"testchar": "none"}
            glue = glue_mod.ServeMemory(cfg, clock=lambda: 0.0)

            async def scenario():
                self.assertEqual(
                    await glue.instruction("testchar", "default", "chat", "", "SYSTEM PROMPT"),
                    "SYSTEM PROMPT")
                self.assertEqual(glue._sessions, {})
                glue.note_exchange("testchar", "chat", "", "hello", "hi")  # a no-op
                await glue.drain()
                await glue.stop()

            asyncio.run(scenario())
            self.assertFalse(recs.exists())
            self.assertEqual(backend.stored, [])

    def test_checkpoint_false_keeps_the_lane_off_disk_until_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp))
            glue = glue_mod.ServeMemory(self._cfg(checkpoint=False), clock=lambda: 0.0)

            async def scenario():
                await glue.instruction("testchar", "default", "chat", "", "P")
                glue.note_exchange("testchar", "chat", "", "hello", "hi")
                await glue.drain()
                self.assertFalse((root / "testchar").exists())
                await glue.stop()

            asyncio.run(scenario())
            self.assertEqual(len(list(records_mod.iter_records("testchar", recs))), 1)

    def test_a_failing_backend_never_breaks_a_turn_or_a_close(self):
        """Containment: recall, store, consolidate and close all
        raise — the turn is still answered and the canonical record still
        lands."""
        with tempfile.TemporaryDirectory() as tmp:
            root, recs, backend = self._env(Path(tmp), backend=_BoomBackend())
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)
            checkpoint = root / "testchar" / "memory" / "checkpoints" / "serve-chat.json"

            async def scenario():
                base = "SYSTEM PROMPT"
                self.assertEqual(
                    await glue.instruction("testchar", "default", "chat", "", base), base)
                glue.note_exchange("testchar", "chat", "", "hello", "hi")
                await glue.drain()
                await glue.close_session(self.KEY)  # store + consolidate raise
                self.assertFalse(checkpoint.exists())
                await glue.stop()                   # backend.close raises

            asyncio.run(scenario())
            self.assertEqual(len(list(records_mod.iter_records("testchar", recs))), 1)


# ── the facade seam: app.py driven directly, no sockets ──────────────────────

class _FakeUpstream:
    """One non-streaming LLM reply, without a server."""

    def __init__(self, reply: str) -> None:
        self.status = 200
        self._reply = reply

    async def json(self):
        return {"choices": [{"message": {"content": self._reply}}]}

    def release(self):
        pass


class _FakeSession:
    def __init__(self, reply: str = "hi there") -> None:
        self.posts: list = []
        self._reply = reply

    async def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.posts.append(json)
        return _FakeUpstream(self._reply)


class _RecordingMemory:
    """Stands in for ServeMemory at the app seam (the glue itself is covered
    above): records what the facade asked, and returns a MARKED instruction so
    the swap is visible in the upstream payload."""

    def __init__(self) -> None:
        self.opened: list = []
        self.exchanges: list = []
        self.cues: list = []

    async def instruction(self, companion, persona, channel, hint, base, cue=""):  # noqa: ANN001
        self.opened.append((companion, persona, channel, hint, base))
        self.cues.append(cue)
        return base + "\n\n[MEMORY]"

    def note_exchange(self, companion, channel, hint, user_text, reply_text):  # noqa: ANN001
        self.exchanges.append((companion, channel, hint, user_text, reply_text))


class _FakeRequest:
    def __init__(self, body: dict, headers=None, deps=None) -> None:
        self._body = body
        self.headers = dict(headers or {})
        self.app = {"deps": deps}

    async def json(self):
        return self._body


class TestFacadeIdentityAndMemory(unittest.TestCase):
    """serve/app.py's chat + models + speech seams, driven directly."""

    def _deps(self, memory=None, characters=None):
        return serve_app.FacadeDeps(
            system_instruction="BASE PROMPT", model_id="m", temperature=0.7,
            reasoning_effort="", character="base", ref_wav="/dev/null", tts_model="t",
            lm_base_url="http://127.0.0.1:1/v1", lm_token="x", bearer="b",
            cfg={"tts_model": "t"}, tap=None, model_name="mdl", persona="default",
            characters=dict(characters or {}), memory=memory, session=_FakeSession(),
        )

    def _chat(self, deps, body, headers=None):
        request = _FakeRequest(body, headers, deps)
        with mock.patch.object(serve_app.tts_prep, "live_llm_temperature", return_value=0.7):
            asyncio.run(serve_app._chat(request))
        return deps.session.posts[-1]

    def test_no_memory_sends_the_plain_instruction(self):
        deps = self._deps()
        out = self._chat(deps, {"messages": [{"role": "user", "content": "hello"}]})
        self.assertEqual(out["messages"][0]["content"], "BASE PROMPT")

    def test_client_declared_character_is_honored_and_junk_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "persona.md"
            persona.write_text("x", encoding="utf-8")

            def fake_persona_path(name, variant=None):  # noqa: ANN001
                return persona if name == "guest" else Path(tmp) / "missing.md"

            deps = self._deps(characters={"guest": "guest-a"})
            with mock.patch.object(serve_app.config_loader, "persona_path",
                                   side_effect=fake_persona_path), \
                 mock.patch.object(serve_app.config_loader, "compose_system_instruction",
                                   return_value="GUEST PROMPT") as compose:
                out = self._chat(deps, {"model": "guest",
                                        "messages": [{"role": "user", "content": "hello"}]})
                self.assertEqual(out["messages"][0]["content"], "GUEST PROMPT")
                self._chat(deps, {"model": "guest",
                                  "messages": [{"role": "user", "content": "again"}]})
                compose.assert_called_once_with("mdl", "guest")  # cached per companion

                for junk in ("not-a-character", "../etc/passwd", "qwen3-coder:30b", ""):
                    out = self._chat(deps, {"model": junk,
                                            "messages": [{"role": "user", "content": "hi"}]})
                    self.assertEqual(out["messages"][0]["content"], "BASE PROMPT", junk)

    def test_memory_swaps_the_instruction_and_is_fed_the_exchange(self):
        memory = _RecordingMemory()
        deps = self._deps(memory=memory)
        out = self._chat(deps, {"messages": [{"role": "user", "content": "hello"}]},
                         {"X-Hearth-Channel": "voice", "X-Hearth-Session": "walk-1"})
        self.assertEqual(out["messages"][0]["content"], "BASE PROMPT\n\n[MEMORY]")
        self.assertEqual(memory.opened,
                         [("base", "default", "voice", "walk-1", "BASE PROMPT")])
        self.assertEqual(memory.exchanges,
                         [("base", "voice", "walk-1", "hello", "hi there")])

    def test_memory_follows_the_declared_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "persona.md"
            persona.write_text("x", encoding="utf-8")
            memory = _RecordingMemory()
            deps = self._deps(memory=memory, characters={"guest": "guest-a"})
            with mock.patch.object(serve_app.config_loader, "persona_path",
                                   return_value=persona), \
                 mock.patch.object(serve_app.config_loader, "compose_system_instruction",
                                   return_value="GUEST PROMPT"):
                self._chat(deps, {"model": "guest",
                                  "messages": [{"role": "user", "content": "hello"}]})
            self.assertEqual(memory.opened[0][0], "guest")
            self.assertEqual(memory.opened[0][4], "GUEST PROMPT")
            self.assertEqual(memory.exchanges[0][0], "guest")

    def test_internal_requests_bypass_persona_and_memory(self):
        memory = _RecordingMemory()
        deps = self._deps(memory=memory)
        out = self._chat(deps, {"messages": [{"role": "system", "content": "SUMMARIZE"},
                                             {"role": "user", "content": "transcript"}]},
                         {"X-Hearth-Internal": "task"})
        self.assertEqual(out["messages"][0]["content"], "SUMMARIZE")  # its own prompt kept
        self.assertEqual(memory.opened, [])
        self.assertEqual(memory.exchanges, [])

    def test_models_lists_the_resolved_identity_plus_the_roster(self):
        deps = self._deps(characters={"guest": "guest-a", "base": "base-a"})
        resp = asyncio.run(serve_app._models(_FakeRequest({}, {}, deps)))
        ids = [row["id"] for row in json.loads(resp.body)["data"]]
        self.assertEqual(ids, ["base", "guest"])  # deduped, resolved identity first

    def test_declared_voice_bundle_is_used_only_for_roster_characters(self):
        deps = self._deps(characters={"guest": "guest-a"})
        with mock.patch.object(serve_app.config_loader, "load_voice",
                               return_value={"ref_wav": "/clip/guest.wav",
                                             "model_repo": "repo/guest"}) as load:
            self.assertIs(serve_app._voice_deps(deps, {"voice": "stranger"}), deps)
            load.assert_not_called()
            routed = serve_app._voice_deps(deps, {"voice": "guest"})
            self.assertEqual((routed.ref_wav, routed.tts_model),
                             ("/clip/guest.wav", "repo/guest"))
            self.assertEqual((deps.ref_wav, deps.tts_model), ("/dev/null", "t"))
            serve_app._voice_deps(deps, {"model": "guest"})
            load.assert_called_once_with("guest", "guest-a")  # cached per character


# ── the config gate: anchors resolve at import ⇒ subprocess (test_data_root shape) ──

_GATE_PROBE = """
import json
from hearth.config import config_loader
from hearth import memory
cfg = config_loader.load_memory_config()
out = {"cfg": cfg}
if cfg is not None:
    seam_ex = memory.maybe_attach("example")
    seam_off = memory.maybe_attach("guest")
    out["example_backend"] = seam_ex.backend.name if seam_ex else None
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
        self.assertEqual(out["example_backend"], "floor")
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

    def test_serve_defaults_off_and_normalized(self):
        """Absent [memory.serve] ⇒ normalized, disabled — the facade lane ships
        dark. Present ⇒ its boundaries are honored."""
        res = self._run("[memory]\nenabled = true\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        serve = json.loads(res.stdout)["cfg"]["serve"]
        self.assertFalse(serve["enabled"])
        self.assertEqual(serve["idle_close_voice"], 5)
        self.assertEqual(serve["idle_close_chat"], 480)
        self.assertTrue(serve["checkpoint"])

        res = self._run(
            "[memory]\nenabled = true\n"
            "[memory.serve]\nenabled = true\nidle_close_voice = 3\n"
            "idle_close_chat = 60\ncheckpoint = false\n"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        serve = json.loads(res.stdout)["cfg"]["serve"]
        self.assertTrue(serve["enabled"])
        self.assertEqual(serve["idle_close_voice"], 3)
        self.assertEqual(serve["idle_close_chat"], 60)
        self.assertFalse(serve["checkpoint"])

    def test_unknown_backend_is_config_error(self):
        res = self._run("[memory]\nenabled = true\nbackend = \"warpdrive\"\n")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ConfigError", res.stderr)
        self.assertIn("memory.toml", res.stderr)


class _NSText:
    def __init__(self, text):  # noqa: ANN001
        self.text = text


class _BoostClient:
    """recall + list_memories double for the recent-boost path (no SDK here)."""

    def __init__(self, results, recent, fail_recent=False):  # noqa: ANN001
        self._results, self._recent, self._fail = results, recent, fail_recent

    def recall(self, bank_id, query):  # noqa: ANN001, ARG002
        r = type("R", (), {})()
        r.results = self._results
        return r

    def list_memories(self, bank_id, limit, offset=0):  # noqa: ANN001, ARG002
        if self._fail:
            raise RuntimeError("boom")
        r = type("R", (), {})()
        r.items = self._recent[:limit]
        return r

    def close(self):
        pass


class TestHindsightRecentBoost(unittest.TestCase):
    """The last-session slot (finding 2026-09-01): newest valid facts append
    past semantic rank — deduped, invalid-filtered, capped, contained."""

    def _backend(self, client, boost=2):  # noqa: ANN001
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "llm_model": "m", "recent_boost": boost})
        b._ensure = lambda: None
        b._client = client
        return b

    def test_append_dedupe_invalid_filter_and_cap(self):
        sem = [_NSText("old fact A"), _NSText("old fact B")]
        recent = [
            {"text": "old fact A", "state": "valid", "date": "2026-09-01T10:00:00+00:00"},
            {"text": "fresh fact", "state": "valid", "date": "2026-09-01T10:22:31+00:00"},
            {"text": "retracted", "state": "invalidated", "date": "2026-09-01T09:00:00+00:00"},
            {"text": "second fresh", "state": "valid", "date": "2026-08-31T23:00:00+00:00"},
            {"text": "third fresh", "state": "valid", "date": "2026-08-30T01:00:00+00:00"},
        ]
        items = self._backend(_BoostClient(sem, recent)).recall("c", "q", 6)
        self.assertEqual([i.text for i in items],
                         ["old fact A", "old fact B", "fresh fact", "second fresh"])
        self.assertEqual(items[2].when, "2026-09-01")
        self.assertEqual(items[2].source_session, "hindsight/c/recent")

    def test_boost_failure_costs_only_the_boost(self):
        b = self._backend(_BoostClient([_NSText("only")], [], fail_recent=True))
        self.assertEqual([i.text for i in b.recall("c", "q", 6)], ["only"])

    def test_boost_zero_never_calls_list_memories(self):
        calls = []

        class _C(_BoostClient):
            def list_memories(self, **kw):  # noqa: ANN003
                calls.append(1)
                raise AssertionError("recent_boost=0 must not list")

        items = self._backend(_C([_NSText("x")], []), boost=0).recall("c", "q", 6)
        self.assertEqual([i.text for i in items], ["x"])
        self.assertEqual(calls, [])



class _CueBackend:
    """Standing query recalls the open set; a 'knight' cue surfaces more."""

    name = "cuespy"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.fail_on_cue = False

    def recall(self, companion, query, limit):  # noqa: ANN001
        self.queries.append(query)
        targeted = "knight" in query.lower()
        if targeted and self.fail_on_cue:
            raise RuntimeError("bank down")
        vanilla = MemoryItem(text="Favorite ice cream is vanilla",
                             source_session="s1", when="2026-08-30")
        if targeted:
            return [MemoryItem(text="Favorite show as a kid: Knight Rider",
                               source_session="s9", when="2026-09-01"),
                    MemoryItem(text="Watched it on a wood-panel TV",
                               source_session="s9", when="2026-09-01"),
                    vanilla][:limit]
        return [vanilla]

    def store(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def consolidate(self, *a, **k):  # noqa: ANN002, ANN003
        pass

    def close(self):
        pass


class TestFacadeCuePassthrough(unittest.TestCase):
    """app.py hands the user's LAST line to the glue as the recall cue —
    extracted BEFORE the instruction call (the lane (b) reorder)."""

    def test_chat_passes_the_users_last_line_as_the_cue(self):
        memory = _RecordingMemory()
        helper = TestFacadeIdentityAndMemory("test_memory_follows_the_declared_companion")
        deps = helper._deps(memory=memory)
        helper._chat(deps,
            {"messages": [{"role": "user", "content": "first line"},
                          {"role": "assistant", "content": "sure"},
                          {"role": "user", "content": "what was my favorite show?"}]})
        self.assertEqual(memory.cues, ["what was my favorite show?"])
        # and the composed messages still open with the augmented system layer
        sent = deps.session.posts[-1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("[MEMORY]", sent[0]["content"])
        self.assertEqual([m["role"] for m in sent[1:]],
                         ["user", "assistant", "user"])


class TestPerTurnRecall(unittest.TestCase):
    """Design lane (b): augment_turn = open block + labeled targeted extras,
    every guard falling back to the open composition byte-identically."""

    CUE = "hey, what was my favorite show as a kid? knight rider, maybe?"

    def _seam(self, backend, tmp: Path, **per_turn) -> MemorySeam:
        cfg = {"recall_limit": 3,
               "per_turn": {"enabled": True, "limit": 3, "min_cue_chars": 12,
                            **per_turn}}
        seam = MemorySeam("testchar", "default", backend, cfg)
        seam._floor = FloorBackend(tmp / "floor")
        return seam

    def test_extras_ride_a_labeled_line_and_dedupe_against_the_open_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            opened = seam.augment("BASE")
            self.assertIn("vanilla", opened)
            out = seam.augment_turn("BASE", "  hey,  what was my favorite show"
                                            " as a kid? knight rider, maybe?  ")
            self.assertIn(seam_mod._TURN_HEADER, out)
            self.assertIn("Knight Rider", out)
            self.assertIn("wood-panel", out)
            self.assertEqual(out.count("vanilla"), 1)        # deduped, never repeated
            self.assertTrue(out.startswith("BASE"))
            self.assertEqual(backend.queries[-1], self.CUE)  # the cue verbatim, normalized

    def test_guards_serve_the_open_composition_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            opened = seam.augment("BASE")
            self.assertEqual(seam.augment_turn("BASE", "hi"), opened)       # short cue
            self.assertEqual(seam.augment_turn("BASE", ""), opened)         # no cue
            # a cue whose answers all dedupe away ⇒ no label, same string
            dup = seam.augment_turn("BASE", "tell me about vanilla ice cream")
            self.assertEqual(dup, opened)
            self.assertNotIn(seam_mod._TURN_HEADER, dup)
            # gate off ⇒ the backend never even sees a turn query
            off = self._seam(_CueBackend(), Path(tmp), enabled=False)
            off_open = off.augment("BASE")
            self.assertEqual(off.augment_turn("BASE", self.CUE), off_open)
            self.assertEqual(len(off.backend.queries), 1)    # the open query only

    def test_per_turn_limit_caps_the_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            seam = self._seam(_CueBackend(), Path(tmp), limit=1)
            seam.augment("BASE")
            out = seam.augment_turn("BASE", self.CUE)
            self.assertIn("Knight Rider", out)
            self.assertNotIn("wood-panel", out)

    def test_turn_failure_costs_only_the_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            seam = self._seam(backend, Path(tmp))
            opened = seam.augment("BASE")
            backend.fail_on_cue = True
            self.assertEqual(seam.augment_turn("BASE", self.CUE), opened)   # no raise

    def test_intent_line_rides_every_composition_but_consumes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            seam = self._seam(_CueBackend(), Path(tmp))
            slot = Path(tmp) / "intent.json"
            slot.write_text("{}", encoding="utf-8")
            seam._intent = {"text": "the walk debrief",
                            "stated_at": "2026-08-31", "path": slot}
            opened = seam.augment("BASE")
            self.assertIn("you agreed to pick up the walk debrief", opened)
            self.assertFalse(slot.exists())                  # consumed at augment
            out = seam.augment_turn("BASE", self.CUE)
            self.assertEqual(out.count("you agreed to pick up"), 1)


class TestServeGluePerTurn(unittest.TestCase):
    """The chat-lane glue over the seam: cue upgrades, one-slot cache, channel
    scope, and containment — offline, same harness shape as TestServeGlue."""

    def _cfg(self) -> dict:
        return {"recall_limit": 3, "backend": "stub", "companions": {},
                "per_turn": {"enabled": True, "limit": 3, "min_cue_chars": 12},
                "serve": {"enabled": True, "idle_close_voice": 5,
                          "idle_close_chat": 480, "checkpoint": True},
                "intent": {"enabled": False, "expiry_days": 14,
                           "llm_provider": "ollama", "llm_model": "testmodel",
                           "llm_url": "", "companions": {}}}

    def _env(self, tmp: Path, backend):
        root, recs = tmp / "characters", tmp / "records"
        for patcher in (
            mock.patch.object(glue_mod, "_checkpoint_root", return_value=root),
            mock.patch.object(glue_mod, "_build_backend", return_value=backend),
            mock.patch.object(records_mod, "records_dir", return_value=recs),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cue_upgrades_caches_scopes_and_contains(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CueBackend()
            self._env(Path(tmp), backend)
            glue = glue_mod.ServeMemory(self._cfg(), clock=lambda: 0.0)

            async def scenario():
                base = "SYSTEM PROMPT"
                cue = "what was my favorite show as a kid? knight rider?"
                # the OPENING request already benefits: the cue rides call one
                first = await glue.instruction("testchar", "default", "chat", "",
                                               base, cue=cue)
                self.assertIn("Knight Rider", first)
                self.assertIn(seam_mod._TURN_HEADER, first)
                calls = len(backend.queries)
                # the same cue again ⇒ the one-slot cache answers, no new recall
                again = await glue.instruction("testchar", "default", "chat", "",
                                               base, cue=cue)
                self.assertEqual(again, first)
                self.assertEqual(len(backend.queries), calls)
                # a short cue serves the cached OPEN instruction
                open_str = await glue.instruction("testchar", "default", "chat", "",
                                                  base, cue="hi")
                self.assertNotIn("Knight Rider", open_str)
                self.assertIn("vanilla", open_str)
                # the voice channel is out of scope this stroke: no turn query
                voice_calls = len(backend.queries)
                voiced = await glue.instruction("testchar", "default", "voice", "",
                                                base, cue=cue)
                self.assertNotIn("Knight Rider", voiced)
                self.assertEqual(len(backend.queries), voice_calls + 1)  # its OPEN only
                # a failing targeted recall costs only the extras
                backend.fail_on_cue = True
                degraded = await glue.instruction(
                    "testchar", "default", "chat", "", base,
                    cue="knight rider, once more with feeling")
                self.assertEqual(degraded, open_str)
                await glue.stop()

            asyncio.run(scenario())


class _SpyBackend:
    """Counts store/consolidate calls (and recalls nothing)."""

    name = "spy"

    def __init__(self) -> None:
        self.stored: list = []
        self.consolidated = 0

    def recall(self, companion, query, limit):  # noqa: ANN001
        return []

    def store(self, companion, record):  # noqa: ANN001
        self.stored.append(record)

    def consolidate(self, companion):  # noqa: ANN001
        self.consolidated += 1

    def close(self):
        pass


class TestSessionMode(unittest.TestCase):
    """Per-session memory mode (off · recall-only · full): recall and retention
    are independent axes. recall-only must recall (and intent-inject) exactly
    like full while leaving ZERO durable memory artifacts — no record, no
    backend index, no intent capture, and a preserved (not consumed) slot."""

    MSGS = [{"role": "user", "content": "a private errand"},
            {"role": "assistant", "content": "understood"}]

    INTENT_CFG = {"recall_limit": 3,
                  "intent": {"enabled": True, "expiry_days": 14,
                             "llm_provider": "ollama", "llm_model": "testmodel",
                             "llm_url": "", "companions": {}}}

    def test_default_retains(self):
        # The serve glue constructs MemorySeam positionally with four args —
        # the default MUST stay retain=True or the facade lane goes silent.
        seam = MemorySeam("testchar", "default", _SpyBackend(), {})
        self.assertTrue(seam.retain)

    def test_mode_validation(self):
        with self.assertRaises(ValueError):
            maybe_attach("testchar", mode="ephemeral")

    def test_maybe_attach_modes(self):
        from hearth.config import config_loader
        cfg = {"backend": "floor", "companions": {}}
        with mock.patch.object(config_loader, "load_memory_config", return_value=cfg):
            self.assertIsNone(maybe_attach("testchar", mode="off"))
            ro = maybe_attach("testchar", mode="recall-only")
            self.assertIsNotNone(ro)
            self.assertFalse(ro.retain)
            self.assertTrue(maybe_attach("testchar", mode="full").retain)

    def test_recall_only_retains_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            spy = _SpyBackend()
            seam = MemorySeam("testchar", "default", spy, self.INTENT_CFG, retain=False)
            seam._floor = FloorBackend(d)
            with mock.patch.object(records_mod, "records_dir", return_value=d), \
                 mock.patch.object(intent_mod, "_ollama_chat") as llm:
                status = seam.on_session_end(self.MSGS, store=None)
            self.assertEqual(status, "recall-only session — nothing retained")
            self.assertEqual(list(records_mod.iter_records("testchar", d)), [])
            self.assertEqual(spy.stored, [])
            self.assertEqual(spy.consolidated, 0)
            self.assertEqual(llm.call_count, 0)   # intent capture suppressed too

    def test_recall_only_recalls_and_preserves_the_intent_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, slot = Path(tmp), Path(tmp) / "intent.json"
            records_mod.write_record(_record("s1", "2026-08-29T09:00:00"), d)
            intent_mod.write_slot("testchar", "the tea ceremony", "s0", path=slot)
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                seam = MemorySeam("testchar", "default", FloorBackend(d),
                                  self.INTENT_CFG, retain=False)
                seam._floor = FloorBackend(d)
                out = seam.augment("SYSTEM PROMPT")
            self.assertIn("## MEMORY — from earlier conversations", out)  # recall is live
            self.assertIn("the tea ceremony", out)     # she opens aware of the plan…
            self.assertTrue(slot.is_file())            # …but the slot is peeked, not popped
            # The next FULL boot still gets to consume it.
            with mock.patch.object(intent_mod, "intent_path", return_value=slot):
                full = MemorySeam("testchar", "default", FloorBackend(d), self.INTENT_CFG)
                full._floor = FloorBackend(d)
                out_full = full.augment("SYSTEM PROMPT")
            self.assertIn("the tea ceremony", out_full)
            self.assertFalse(slot.is_file())


if __name__ == "__main__":
    unittest.main()
