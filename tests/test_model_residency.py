"""test_model_residency.py — a live session owns its model's residency.

Headless: the residency probe and the CLI are injected, so nothing here talks
to a model server or loads a model. Pins:

  1. llama-server (and its aliases) → skipped, no probe, no load;
  2. LM Studio, model already resident → nothing run;
  3. LM Studio, model absent → the CLI is run with the id pinned as identifier,
     the second probe confirms, output lands in a 0600 log;
  4. probe unreachable → a note, no CLI, never raises;
  5. no CLI found → a note, never raises;
  6. a load that runs but does not take is reported as failed, not resident;
  7. is_lmstudio agrees with the engine-probe dispatch about who owns the model.

Run:  .venv/bin/python -m unittest tests.test_model_residency
"""

from __future__ import annotations

import asyncio
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from hearth.control import engine_probe_llamaserver as probe_mod
from hearth.pipeline import model_residency as mr

_PY = sys.executable
_ID = "zz-model-key"


def _probe(seq):
    """A residency probe that answers from a list, one call at a time."""
    answers = list(seq)
    calls = []

    async def probe(provider, base_url, token):
        calls.append(provider)
        return answers.pop(0) if answers else answers_last(answers)
    def answers_last(_):
        return None
    probe.calls = calls
    return probe


class Residency(unittest.IsolatedAsyncioTestCase):

    async def test_llama_server_steps_aside(self):
        for alias in ("llama-server", "LLAMASERVER", "llama.cpp"):
            probe = _probe([[_ID]])
            rec = await mr.ensure_resident(alias, "http://x/v1", "", _ID, probe=probe,
                                           lms_path=lambda: "/nonexistent/lms", say=lambda s: None)
            self.assertEqual(rec["action"], "skipped", alias)
            self.assertEqual(probe.calls, [])

    async def test_resident_already_means_no_load(self):
        probe = _probe([[_ID, "zz-other"]])
        ran = []
        rec = await mr.ensure_resident("lmstudio", "http://x/v1", "t", _ID, probe=probe,
                                       lms_path=lambda: (ran.append(1), "/x/lms")[1],
                                       say=lambda s: None)
        self.assertEqual(rec["action"], "resident")
        self.assertEqual(ran, [])

    async def test_absent_model_is_loaded_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The stand-in CLI records its argv where the test can read it.
            marker = Path(tmp) / "argv.txt"
            cli = Path(tmp) / "lms"
            cli.write_text("#!/bin/sh\nprintf '%s ' \"$@\" > " + str(marker) +
                           "\necho load-output\n")
            cli.chmod(0o755)
            probe = _probe([[], [_ID]])
            said = []
            rec = await mr.ensure_resident("lmstudio", "http://x/v1", "t", _ID,
                                           log_dir=Path(tmp) / "logs", probe=probe,
                                           lms_path=lambda: str(cli), say=said.append)
            self.assertEqual(rec["action"], "loaded")
            self.assertTrue(rec["ok"])
            argv = marker.read_text().split()
            self.assertEqual(argv[:2], ["load", _ID])
            self.assertIn("--identifier", argv)
            self.assertEqual(argv[argv.index("--identifier") + 1], _ID)
            log = Path(tmp) / "logs" / "model-load.log"
            self.assertIn("load-output", log.read_text())
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
            self.assertTrue(any("resident after" in s for s in said), said)
            self.assertEqual(len(probe.calls), 2)

    async def test_unreachable_server_is_a_note_not_a_crash(self):
        probe = _probe([None])
        said = []
        rec = await mr.ensure_resident("lmstudio", "http://x/v1", "t", _ID, probe=probe,
                                       lms_path=lambda: "/x/lms", say=said.append)
        self.assertEqual(rec["action"], "unreachable")
        self.assertTrue(said and "did not answer" in said[0])

    async def test_missing_cli_is_a_note_not_a_crash(self):
        probe = _probe([[]])
        said = []
        rec = await mr.ensure_resident("lmstudio", "http://x/v1", "t", _ID, probe=probe,
                                       lms_path=lambda: None, say=said.append)
        self.assertEqual(rec["action"], "no-cli")
        self.assertTrue(said and "LMS_BIN" in said[0])

    async def test_load_that_does_not_take_is_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "lms"
            cli.write_text("#!/bin/sh\nexit 7\n")
            cli.chmod(0o755)
            probe = _probe([[], []])
            said = []
            rec = await mr.ensure_resident("lmstudio", "http://x/v1", "t", _ID,
                                           log_dir=Path(tmp) / "logs", probe=probe,
                                           lms_path=lambda: str(cli), say=said.append)
            self.assertEqual(rec["action"], "failed")
            self.assertFalse(rec["ok"])
            self.assertTrue(any("exit 7" in s for s in said), said)

    def test_provider_reading_matches_the_engine_probe(self):
        for alias in probe_mod._LLAMASERVER_ALIASES:
            self.assertFalse(mr.is_lmstudio(alias), alias)
        for other in ("lmstudio", "LM Studio", "", None, "something-else"):
            self.assertTrue(mr.is_lmstudio(other), other)

    def test_find_lms_honours_the_env_override(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "lms"; cli.write_text("#!/bin/sh\n"); cli.chmod(0o755)
            old = os.environ.get("LMS_BIN")
            try:
                os.environ["LMS_BIN"] = str(cli)
                self.assertEqual(mr.find_lms(), str(cli))
                os.environ["LMS_BIN"] = str(Path(tmp) / "missing")
                self.assertIsNone(mr.find_lms())
            finally:
                if old is None:
                    os.environ.pop("LMS_BIN", None)
                else:
                    os.environ["LMS_BIN"] = old


if __name__ == "__main__":
    unittest.main()
