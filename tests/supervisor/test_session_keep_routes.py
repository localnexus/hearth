"""Supervisor — POST /admin/sessions/keep: the shelf's own verb for an unkept
leftover an unclean death left behind.

Same front half as archive/unarchive (character + session out of the body,
the character known, the live-session guard), then `session_store.keep_orphan`
promotes the file — `held` and `retain` both flip true, in place. An id that
already names a kept session is not an unkept orphan, so it answers the same
404 an id that never existed would. An optional `title` goes through the same
gate rename's title form uses, so a title that gate refuses is a 400 and the
promotion has already happened by then.

The no-content-read contract holds here too: a sentinel rides in the session's
messages and may not appear in a response body or a log line.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth import supervisor
from hearth.config import config_loader
from hearth.session import session_store

SENTINEL = "ZZ-SENTINEL-KEEP-ZZ"
CHARACTER = "zz-keep-test"
OTHER = "zz-keep-other"


class SessionKeepRoutes(AioHTTPTestCase):
    BEARER = {"Authorization": "Bearer test-bearer"}

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none",
            session=None,
        )
        supervisor.build_mount({
            "enabled": True, "panel_url": "http://127.0.0.1:1",
            "compact_watch": False,
        })(app)

        async def _open(app_):
            app_["deps"].session = aiohttp.ClientSession()

        async def _close(app_):
            await app_["deps"].session.close()

        app.on_startup.append(_open)
        app.on_cleanup.append(_close)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app["bot_child"].close()  # never adopt a real desk bot into a test
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for name in (CHARACTER, OTHER):
            char_dir = self.root / "characters" / name
            char_dir.mkdir(parents=True)
            (char_dir / "persona.md").write_text("test persona marker", encoding="utf-8")
            (char_dir / "voices" / "v1").mkdir(parents=True)
            (char_dir / "voices" / "v1" / "voice.toml").write_text("", encoding="utf-8")
        self.sessions_dir = self.root / "characters" / CHARACTER / "sessions"

        # An unkept orphan: retain False, held False (the default) — a working
        # file an unclean death left behind.
        self.orphan = session_store.SessionStore(
            session_id="session-orphan", model="m1", voice="v1", prompt_sha256="d",
            sessions_dir=self.sessions_dir, character=CHARACTER)
        self.orphan.snapshot([{"role": "user", "content": SENTINEL}])
        self.orphan_path = self.sessions_dir / "session-orphan.json"

        # An already-kept session — not an unkept orphan, so keep answers 404.
        session_store.SessionStore(
            session_id="session-kept", model="m1", voice="v1", prompt_sha256="d",
            sessions_dir=self.sessions_dir, character=CHARACTER, held=True,
        ).snapshot([{"role": "user", "content": "already kept"}])
        self.kept_path = self.sessions_dir / "session-kept.json"

        for attr in ("_DATA", "_ROOT"):
            patcher = mock.patch.object(config_loader, attr, self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        # The dev tree has no config/active.toml, so by default the guard would
        # fail closed. Every test but the guard one says who is active.
        self.active(OTHER)

    def active(self, character):
        patcher = mock.patch.object(
            config_loader, "load_active_selection",
            lambda: {"character": character, "model": "m1", "voice": "v1",
                     "persona": "default"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def bot(self, state):
        patcher = mock.patch.object(
            self.app["bot_child"], "status",
            lambda: {"state": state, "pid": None, "managed": False,
                     "uptime_s": None, "last_exit": None})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def keep(self, **body):
        return await self.client.post("/admin/sessions/keep",
                                      headers=self.BEARER, json=body)

    # ── the round trip ────────────────────────────────────────────────────

    async def test_an_unkept_leftover_is_promoted_onto_the_shelf(self):
        resp = await self.keep(character=CHARACTER, session="session-orphan",
                               title="a kept name")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.json(),
                         {"ok": True, "session_id": "session-orphan",
                          "title": "a kept name"})
        import json as jsonlib
        data = jsonlib.loads(self.orphan_path.read_text(encoding="utf-8"))
        self.assertIs(data["held"], True)
        self.assertIs(data["retain"], True)
        self.assertEqual(data["title"], "a kept name")

    async def test_keeping_without_a_title_still_promotes_it(self):
        resp = await self.keep(character=CHARACTER, session="session-orphan")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.json(),
                         {"ok": True, "session_id": "session-orphan", "title": None})
        import json as jsonlib
        data = jsonlib.loads(self.orphan_path.read_text(encoding="utf-8"))
        self.assertIs(data["held"], True)
        self.assertIs(data["retain"], True)

    async def test_an_already_kept_session_answers_404(self):
        resp = await self.keep(character=CHARACTER, session="session-kept")
        self.assertEqual(resp.status, 404, await resp.text())
        self.assertEqual((await resp.json())["error"],
                         "no unkept conversation with that id")

    async def test_no_such_session_answers_404(self):
        resp = await self.keep(character=CHARACTER, session="session-nope")
        self.assertEqual(resp.status, 404, await resp.text())
        self.assertEqual((await resp.json())["error"],
                         "no unkept conversation with that id")

    async def test_a_bad_title_is_refused_400(self):
        resp = await self.keep(character=CHARACTER, session="session-orphan",
                               title="x" * 200)
        self.assertEqual(resp.status, 400, await resp.text())
        self.assertFalse((await resp.json())["ok"])
        # The keep already happened; the title gate is the only thing refused.
        import json as jsonlib
        data = jsonlib.loads(self.orphan_path.read_text(encoding="utf-8"))
        self.assertIs(data["held"], True)
        self.assertIs(data["retain"], True)

    async def test_shape_refusals(self):
        cases = [
            ("no session named", {"character": CHARACTER}, 400),
            ("no companion named", {"session": "session-orphan"}, 400),
            ("a traversal id", {"character": CHARACTER, "session": "../../x"}, 400),
            ("no such companion", {"character": "zz-nobody", "session": "session-orphan"}, 404),
        ]
        for label, body, want in cases:
            with self.subTest(case=label):
                resp = await self.keep(**body)
                self.assertEqual(resp.status, want, await resp.text())
                self.assertFalse((await resp.json())["ok"])

    async def test_the_running_companions_shelf_is_read_only(self):
        self.active(CHARACTER)
        self.bot("running")
        resp = await self.keep(character=CHARACTER, session="session-orphan")
        self.assertEqual(resp.status, 409, await resp.text())
        error = (await resp.json())["error"]
        self.assertIn(f"{CHARACTER} is running", error)
        self.assertIn("stop the companion first", error)
        self.assertFalse(
            self.orphan_path.read_text(encoding="utf-8").__contains__('"held": true'),
            "nothing promoted while the companion was up")

    async def test_a_body_that_is_not_json_at_all(self):
        resp = await self.client.post("/admin/sessions/keep",
                                      headers=self.BEARER, data=b"<not json>")
        self.assertEqual(resp.status, 400)

    # ── the contract ──────────────────────────────────────────────────────

    async def test_no_response_or_log_line_carries_a_word_of_it(self):
        logged = []
        from loguru import logger
        sink = logger.add(lambda m: logged.append(str(m)), level="DEBUG")
        try:
            bodies = {
                "kept": await (await self.keep(
                    character=CHARACTER, session="session-orphan",
                    title="a kept name")).text(),
                "refusal": await (await self.keep(
                    character=CHARACTER, session="session-nope")).text(),
            }
        finally:
            logger.remove(sink)
        for label, body in bodies.items():
            with self.subTest(response=label):
                self.assertNotIn(SENTINEL, body, f"{label} must never carry content")
                self.assertNotIn(self._tmp.name, body, "no response maps the disk")
        for line in logged:
            self.assertNotIn(SENTINEL, line, "no log line may carry content")


if __name__ == "__main__":
    unittest.main()
