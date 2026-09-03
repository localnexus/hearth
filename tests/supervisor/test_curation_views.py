"""Curation pane — the read-only views.

Bearer gating, the unauthed static shell, the lazy fact gauge, the overview
counts and lane flag, and the records view that shows a digest, never a
transcript.

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


class _FakeCurationBackend:
    name = "fakehs"

    def __init__(self) -> None:
        self.forgot: list[tuple[str, str]] = []
        self.raise_on_forget = False
        self.result: bool | None = True

    def forget(self, companion, session_id):  # noqa: ANN001
        if self.raise_on_forget:
            raise RuntimeError("backend down")
        self.forgot.append((companion, session_id))
        return self.result


class _FakeCountingBackend(_FakeCurationBackend):
    """A curation backend that also exposes the optional fact_count capability."""

    def __init__(self) -> None:
        super().__init__()
        self.counted: list[str] = []
        self.count_result: dict = {"facts": 7, "capped": False}
        self.raise_on_count = False

    def fact_count(self, companion):  # noqa: ANN001
        if self.raise_on_count:
            raise RuntimeError("backend down")
        self.counted.append(companion)
        return self.count_result


class _FakeGlue:
    """The two ServeMemory methods /admin/memory uses, nothing more."""

    def __init__(self, backend) -> None:  # noqa: ANN001 — None = companion "none"
        self._backend = backend

    def backend_name_for(self, companion):  # noqa: ANN001
        return self._backend.name if self._backend is not None else "none"

    def curation_backend(self, companion):  # noqa: ANN001
        return self._backend


class CurationRoutes(AioHTTPTestCase):
    """/admin/memory: bearer-gated digest views + preview-then-confirm forget,
    CLI-parity ordering (backend facts first — a failed index update keeps the
    record), against a scratch DATA tree and a fake facade memory glue."""

    BEARER = {"Authorization": "Bearer test-bearer"}
    CHAR = "zz-cur-test"

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none",
            session=None,
            memory=None,  # per-test: a _FakeGlue or None
        )
        mount = supervisor.build_mount({"enabled": True,
                                        "panel_url": "http://127.0.0.1:1",
                                        "compact_watch": False})
        mount(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Deterministic: never adopt a real desk bot into a test.
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)
        # Scratch DATA tree with one known character + two ended-session records.
        from unittest import mock

        from hearth.config import config_loader
        from hearth.memory import records as records_mod
        from hearth.memory.backend import SessionRecord

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        char_dir = root / "characters" / self.CHAR
        char_dir.mkdir(parents=True)
        (char_dir / "persona.md").write_text("test persona marker")
        self.records_dir = char_dir / "memory" / "records"
        for sid, ended in (("sess-a", "2026-08-30T09:00:00"),
                           ("sess-b", "2026-08-31T09:00:00")):
            records_mod.write_record(SessionRecord(
                companion=self.CHAR, session_id=sid,
                started="2026-08-30T08:00:00", ended=ended, name="",
                messages=[{"role": "user", "content": f"SECRET-USER-LINE {sid}"},
                          {"role": "assistant", "content": f"a reply in {sid}"}],
            ), self.records_dir)
        self._patch = mock.patch.object(config_loader, "_DATA", root)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def test_bearer_required(self):
        self.assertEqual((await self.client.get("/admin/memory")).status, 401)
        self.assertEqual(
            (await self.client.get("/admin/memory/records?character=x")).status, 401)
        self.assertEqual(
            (await self.client.get("/admin/memory/facts?character=x")).status, 401)
        self.assertEqual(
            (await self.client.post("/admin/memory/forget", json={})).status, 401)

    async def test_pane_shell_is_unauthed_static_chrome(self):
        resp = await self.client.get("/admin/memory/ui")  # no bearer
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("memory", text.lower())
        self.assertNotIn("test-bearer", text)
        self.assertNotIn(self.CHAR, text)  # chrome carries no names

    async def test_facts_lazy_gauge(self):
        backend = _FakeCountingBackend()
        self.app["deps"].memory = _FakeGlue(backend)
        url = f"/admin/memory/facts?character={self.CHAR}"
        resp = await self.client.get(url, headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual(data["facts"], 7)
        self.assertFalse(data["capped"])
        self.assertEqual(data["backend"], "fakehs")
        self.assertEqual(backend.counted, [self.CHAR])
        # Backend without the capability (the floor) → honest null + note.
        self.app["deps"].memory = _FakeGlue(_FakeCurationBackend())
        data = await (await self.client.get(url, headers=self.BEARER)).json()
        self.assertIsNone(data["facts"])
        self.assertIn("no fact index", data["note"])
        # Companion mapped "none" → null, backend named honestly.
        self.app["deps"].memory = _FakeGlue(None)
        data = await (await self.client.get(url, headers=self.BEARER)).json()
        self.assertIsNone(data["facts"])
        self.assertEqual(data["backend"], "none")
        # Lane down → null + the lane note; the view still answers 200.
        self.app["deps"].memory = None
        data = await (await self.client.get(url, headers=self.BEARER)).json()
        self.assertIsNone(data["facts"])
        self.assertIn("memory lane", data["note"])
        # Unknown character → 404; backend failure → 502 with the type name.
        resp = await self.client.get("/admin/memory/facts?character=zz-nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)
        failing = _FakeCountingBackend()
        failing.raise_on_count = True
        self.app["deps"].memory = _FakeGlue(failing)
        resp = await self.client.get(url, headers=self.BEARER)
        self.assertEqual(resp.status, 502)
        self.assertIn("RuntimeError", (await resp.json())["error"])

    async def test_overview_counts_and_lane_flag(self):
        backend = _FakeCurationBackend()
        self.app["deps"].memory = _FakeGlue(backend)
        resp = await self.client.get("/admin/memory", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertTrue(data["memory_lane"])
        row = next(c for c in data["companions"] if c["character"] == self.CHAR)
        self.assertEqual(row["records"], 2)
        self.assertEqual(row["backend"], "fakehs")
        # Views stay useful with the lane down — backend map honestly absent.
        self.app["deps"].memory = None
        data = await (await self.client.get("/admin/memory",
                                            headers=self.BEARER)).json()
        self.assertFalse(data["memory_lane"])
        row = next(c for c in data["companions"] if c["character"] == self.CHAR)
        self.assertIsNone(row["backend"])

    async def test_records_view_digest_not_transcript(self):
        resp = await self.client.get(f"/admin/memory/records?character={self.CHAR}",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual([r["session_id"] for r in data["records"]],
                         ["sess-b", "sess-a"])  # newest first
        for r in data["records"]:
            self.assertTrue(r["digest"])
            self.assertEqual(r["user_turns"], 1)
            self.assertNotIn("messages", r)  # the digest, never the transcript
        resp = await self.client.get("/admin/memory/records?character=zz-nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)

if __name__ == "__main__":
    unittest.main()
