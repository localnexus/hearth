"""Curation pane — the forget verb.

Preview then confirm, a backend failure that still keeps the record, pre-keyed
leftovers getting the clean hint, the "none" backend with the lane down, and
input validation.

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


class CurationForget(AioHTTPTestCase):
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

    async def test_forget_preview_then_confirm(self):
        backend = _FakeCurationBackend()
        self.app["deps"].memory = _FakeGlue(backend)
        body = {"character": self.CHAR, "session": "sess-a"}
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json=body)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertFalse(data["forgotten"])
        self.assertIn('"yes": true', data["confirm"])
        self.assertEqual(data["preview"]["session_id"], "sess-a")
        self.assertTrue((self.records_dir / "sess-a.json").is_file())  # untouched
        self.assertEqual(backend.forgot, [])
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json={**body, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertTrue(data["forgotten"])
        self.assertEqual(data["index"], "excised")
        self.assertEqual(backend.forgot, [(self.CHAR, "sess-a")])
        self.assertFalse((self.records_dir / "sess-a.json").exists())
        self.assertTrue((self.records_dir / "sess-b.json").is_file())

    async def test_forget_backend_failure_keeps_record(self):
        backend = _FakeCurationBackend()
        backend.raise_on_forget = True
        self.app["deps"].memory = _FakeGlue(backend)
        resp = await self.client.post(
            "/admin/memory/forget", headers=self.BEARER,
            json={"character": self.CHAR, "session": "sess-a", "yes": True})
        self.assertEqual(resp.status, 502)
        self.assertIn("record kept", (await resp.json())["error"])
        self.assertTrue((self.records_dir / "sess-a.json").is_file())

    async def test_forget_pre_keyed_leftovers_get_the_clean_hint(self):
        backend = _FakeCurationBackend()
        backend.result = False  # facts stored before keyed retain
        self.app["deps"].memory = _FakeGlue(backend)
        resp = await self.client.post(
            "/admin/memory/forget", headers=self.BEARER,
            json={"character": self.CHAR, "session": "sess-a", "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertTrue(data["forgotten"])
        self.assertEqual(data["index"], "leftover-facts")
        self.assertIn("rebuild --clean", data["hint"])
        self.assertFalse((self.records_dir / "sess-a.json").exists())

    async def test_forget_none_backend_and_lane_down(self):
        # Companion mapped "none": record deleted, no index existed.
        self.app["deps"].memory = _FakeGlue(None)
        resp = await self.client.post(
            "/admin/memory/forget", headers=self.BEARER,
            json={"character": self.CHAR, "session": "sess-b", "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(data["index"], "none")
        self.assertFalse((self.records_dir / "sess-b.json").exists())
        # Lane down: preview still answers; the confirm refuses, names the CLI.
        self.app["deps"].memory = None
        body = {"character": self.CHAR, "session": "sess-a"}
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json=body)
        self.assertEqual(resp.status, 200)
        resp = await self.client.post("/admin/memory/forget", headers=self.BEARER,
                                      json={**body, "yes": True})
        self.assertEqual(resp.status, 409)
        self.assertIn("hearth.memory forget", (await resp.json())["error"])
        self.assertTrue((self.records_dir / "sess-a.json").is_file())

    async def test_forget_validation(self):
        self.app["deps"].memory = _FakeGlue(_FakeCurationBackend())
        cases = (
            ({"character": "zz-nope", "session": "sess-a"}, 404),
            ({"character": self.CHAR, "session": "../evil"}, 400),
            ({"character": self.CHAR, "session": "no-such"}, 404),
            (None, 400),  # non-JSON body
        )
        for body, want in cases:
            if body is None:
                resp = await self.client.post("/admin/memory/forget",
                                              headers=self.BEARER, data=b"not json")
            else:
                resp = await self.client.post("/admin/memory/forget",
                                              headers=self.BEARER, json=body)
            self.assertEqual(resp.status, want, str(body))
        # Nothing was deleted by any refused call.
        self.assertTrue((self.records_dir / "sess-a.json").is_file())
        self.assertTrue((self.records_dir / "sess-b.json").is_file())

if __name__ == "__main__":
    unittest.main()
