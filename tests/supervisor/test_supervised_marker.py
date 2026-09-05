"""Supervisor — the supervised-child marker (bind-WARNING tidy, 2026-09-05).

A bot the facade spawns used to try an in-process /v1 attach on the port its
own parent holds, and log the inevitable "address in use" at WARNING on every
supervised start. The supervisor now stamps HEARTH_SUPERVISED=1 into the child's
environment and serve.maybe_attach steps aside at INFO under it. The WARNING is
kept for the true collision: a desk-side start beside a running facade.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth import serve
from hearth.supervisor.child import BotChild

_PY = sys.executable
_NOMATCH = "zz-hearth-test-nomatch-zz"

# The child reports the marker (and one overlay value) and exits; stdout is the
# supervisor's bot log, which is where the assertion reads it back.
REPORT = (
    "import os\n"
    "print('marker=' + repr(os.environ.get('HEARTH_SUPERVISED')))\n"
    "print('overlay=' + repr(os.environ.get('ZZ_TEST_OVERLAY')))\n"
)


class ChildEnvCarriesTheMarker(unittest.IsolatedAsyncioTestCase):
    async def test_spawned_child_sees_marker_and_overlay(self):
        self.assertEqual(serve.SUPERVISED_ENV, "HEARTH_SUPERVISED")
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "logs" / "bot.log"
            c = BotChild(argv=[_PY, "-c", REPORT], env_overlay={"ZZ_TEST_OVERLAY": "yes"},
                         log_path=log, pattern=_NOMATCH, stop_grace_s=2.0, term_grace_s=1.0)
            res = await c.start()
            self.assertTrue(res["ok"], res)
            for _ in range(50):  # the child exits on its own; the reaper records it
                if c.state == "down":
                    break
                await asyncio.sleep(0.1)
            c.close()
            text = log.read_text(encoding="utf-8")
        self.assertIn("marker='1'", text)
        self.assertIn("overlay='yes'", text)  # the marker is added, not substituted
        self.assertNotIn(serve.SUPERVISED_ENV, os.environ,
                         "the marker belongs to the child's env, never the parent's")


class MaybeAttachStepsAside(unittest.IsolatedAsyncioTestCase):
    CFG = {"host": "127.0.0.1", "port": 1, "token_source": "config/serve-token"}

    async def test_supervised_child_does_not_bind(self):
        from hearth.serve import app as serve_app
        with mock.patch.dict(os.environ, {serve.SUPERVISED_ENV: "1"}), \
                mock.patch.object(serve.config_loader, "load_serve_config", return_value=self.CFG), \
                mock.patch.object(serve_app, "start", side_effect=AssertionError("must not bind")):
            self.assertIsNone(await serve.maybe_attach(object(), "http://x/v1", ""))

    async def test_gate_off_still_wins_first(self):
        with mock.patch.dict(os.environ, {serve.SUPERVISED_ENV: "1"}), \
                mock.patch.object(serve.config_loader, "load_serve_config", return_value=None):
            self.assertIsNone(await serve.maybe_attach(object()))

    async def test_desk_start_still_attaches(self):
        """No marker ⇒ the real path: start() is reached (here: stubbed)."""
        from hearth.serve import app as serve_app
        env = {k: v for k, v in os.environ.items() if k != serve.SUPERVISED_ENV}
        sentinel = object()

        async def fake_start(active, cfg, url, tok):
            return sentinel

        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(serve.config_loader, "load_serve_config", return_value=self.CFG), \
                mock.patch.object(serve_app, "start", side_effect=fake_start):
            self.assertIs(await serve.maybe_attach(object()), sentinel)


if __name__ == "__main__":
    unittest.main()
