"""The fork route — branching a companion from the roster page.

What the Branch card offers, what it refuses, and the provenance it records.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
from hearth import supervisor
from hearth.supervisor.child import BotChild


GRACEFUL = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
    "while True: time.sleep(0.1)\n"
)


def _fake(src: str, **kw) -> BotChild:
    kw.setdefault("pattern", _NOMATCH)
    kw.setdefault("stop_grace_s", 5.0)
    kw.setdefault("term_grace_s", 1.0)
    return BotChild(argv=[_PY, "-c", src], **kw)


_NOMATCH = "zz-hearth-test-nomatch-zz"


_PY = sys.executable


class ForkRoute(AioHTTPTestCase):
    """/admin/roster/fork: the fork verb's web skin — preview writes nothing,
    confirm creates + enrolls + answers the desk rebuild command (replay is
    deliberately NOT run here, curation.py's posture), and validation errors
    come back as the CLI would word them. Also: /admin/memory/records carries
    the full-precision `ended` the juncture picker needs."""

    BEARER = {"Authorization": "Bearer test-bearer"}

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none", session=None, memory=None)
        supervisor.build_mount({"enabled": True,
                                "panel_url": "http://127.0.0.1:1",
                                "compact_watch": False})(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)
        from unittest import mock

        from hearth.config import config_loader

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        cdir = self.root / "characters" / "zz-fork-src"
        (cdir / "voices" / "main").mkdir(parents=True)
        (cdir / "memory" / "records").mkdir(parents=True)
        (cdir / "persona.md").write_text(
            "## IDENTITY\n\nA test companion.\n\n## SOUL\n\nCalm.\n",
            encoding="utf-8")
        (cdir / "voices" / "main" / "voice.toml").write_text(
            'tag = "main"\nref_wav = "sample.wav"\n', encoding="utf-8")
        (cdir / "voices" / "main" / "sample.wav").write_bytes(b"RIFF")
        for sid, ended in (("early", "2026-08-30T10:00:00"),
                           ("after", "2026-08-31T10:00:00")):
            (cdir / "memory" / "records" / f"{sid}.json").write_text(json.dumps(
                {"schema": 1, "kind": "memory-record", "companion": "zz-fork-src",
                 "session_id": sid, "started": "2026-08-30T09:00:00",
                 "ended": ended, "name": "", "persona": "default",
                 "messages": [{"role": "user", "content": "hello"}]}),
                encoding="utf-8")
        (self.root / "config").mkdir()
        mem_toml = self.root / "config" / "memory.toml"
        mem_toml.write_text(
            "[memory]\nenabled = true\nbackend = \"floor\"\n\n"
            "[memory.companions]\n", encoding="utf-8")
        for name, value in (("_DATA", self.root), ("MEMORY_TOML", mem_toml)):
            patcher = mock.patch.object(config_loader, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mem_toml = mem_toml

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    def _body(self, **over):
        body = {"character": "zz-fork-src", "as": "zz-fork-new",
                "until": "2026-08-30"}
        body.update(over)
        return body

    async def test_fork_needs_the_bearer(self):
        resp = await self.client.post("/admin/roster/fork", json=self._body())
        self.assertEqual(resp.status, 401)

    async def test_preview_reports_the_plan_and_writes_nothing(self):
        resp = await self.client.post("/admin/roster/fork", headers=self.BEARER,
                                      json=self._body())
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertTrue(d["ok"])
        self.assertFalse(d["created"])
        self.assertEqual([r["session_id"] for r in d["records"]], ["early"])
        self.assertEqual(d["left_behind"], 1)
        self.assertEqual(d["tier"], "floor")
        self.assertIn("yes", d["confirm"])
        self.assertFalse((self.root / "characters" / "zz-fork-new").exists())
        self.assertNotIn("zz-fork-new", self.mem_toml.read_text())

    async def test_confirm_creates_enrolls_and_names_no_replay(self):
        resp = await self.client.post("/admin/roster/fork", headers=self.BEARER,
                                      json=self._body(yes=True))
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertTrue(d["created"])
        fdir = self.root / "characters" / "zz-fork-new"
        self.assertTrue((fdir / "persona.md").is_file())
        copied = json.loads((fdir / "memory/records/early.json").read_text())
        self.assertEqual(copied["companion"], "zz-fork-new")
        self.assertEqual(copied["forked_from"]["companion"], "zz-fork-src")
        self.assertIn('zz-fork-new = "floor"', self.mem_toml.read_text())
        self.assertIn("no backend replay needed", d["next"])

    async def test_hindsight_tier_points_at_the_desk_rebuild(self):
        self.mem_toml.write_text(
            "[memory]\nenabled = true\nbackend = \"floor\"\n\n"
            "[memory.companions]\nzz-fork-src = \"hindsight\"\n",
            encoding="utf-8")
        resp = await self.client.post("/admin/roster/fork", headers=self.BEARER,
                                      json=self._body(yes=True))
        d = await resp.json()
        self.assertTrue(d["created"])
        self.assertIn("rebuild --character zz-fork-new", d["next"])
        self.assertIn('zz-fork-new = "hindsight"', self.mem_toml.read_text())

    async def test_validation_errors_answer_400(self):
        for over in ({"until": "whenever"}, {"as": "zz-fork-src"},
                     {"character": "zz-no-such"}):
            resp = await self.client.post("/admin/roster/fork",
                                          headers=self.BEARER,
                                          json=self._body(**over))
            self.assertEqual(resp.status, 400, over)
        self.assertFalse((self.root / "characters" / "zz-fork-new").exists())

    async def test_create_only_race_answers_409(self):
        (self.root / "characters" / "zz-fork-new").mkdir()
        resp = await self.client.post("/admin/roster/fork", headers=self.BEARER,
                                      json=self._body(yes=True))
        # plan() itself refuses an existing target — 400 from validation…
        self.assertEqual(resp.status, 400)
        # …and the execute-side re-check is covered by the CLI suite.

    async def test_records_listing_carries_full_precision_ended(self):
        resp = await self.client.get(
            "/admin/memory/records?character=zz-fork-src", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        recs = {r["session_id"]: r for r in (await resp.json())["records"]}
        self.assertEqual(recs["early"]["ended"], "2026-08-30T10:00:00")

if __name__ == "__main__":
    unittest.main()
