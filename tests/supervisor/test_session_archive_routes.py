"""Supervisor — archive / unarchive, and the live-session guard they introduce.

Archive is the soft verb: the file MOVES into `sessions/<c>/.archive/` and can
move back. So the tests are about what stays true across the move — the bytes,
the shelf's two answers, and the fact that nothing is ever deleted or
overwritten — rather than about a return value.

The guard is the other half, and it is coarse on purpose: while a companion is
up, its WHOLE shelf is read-only, because the supervisor cannot know which one
session file the running bot holds. So the matrix is the running companion's
shelf (409), another companion's shelf (200), and an `active.toml` that cannot
be read at all (409 — fail closed, because "which companion is live" is exactly
the thing that just became unknowable). Destroy and rename will reuse the same
helper, so this is where its behaviour is pinned.

The no-content-read contract holds here too: a sentinel rides in the session's
messages and may not appear in an archive, an unarchive, an archived shelf, a
refusal, or a log line.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
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
from hearth.session import session_store, verbs as verbs_mod

SENTINEL = "ZZ-SENTINEL-ARCHIVE-ZZ"
CHARACTER = "zz-archive-test"
OTHER = "zz-archive-other"


class SessionArchiveRoutes(AioHTTPTestCase):
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
        store = session_store.SessionStore(
            session_id="session-x", model="m1", voice="v1", prompt_sha256="d",
            sessions_dir=self.sessions_dir, character=CHARACTER, held=True)
        store.snapshot([{"role": "user", "content": SENTINEL}])
        self.session_path = self.sessions_dir / "session-x.json"
        self.on_disk = self.session_path.read_bytes()
        self.archive_dir = self.sessions_dir / verbs_mod.ARCHIVE_DIR

        for attr in ("_DATA", "_ROOT"):
            patcher = mock.patch.object(config_loader, attr, self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        # The dev tree has no config/active.toml, so by default the guard would
        # fail closed. Every test but the fail-closed one says who is active.
        self.active(OTHER)

    def active(self, character):
        """Point the guard's `active.toml` read at a companion."""
        patcher = mock.patch.object(
            config_loader, "load_active_selection",
            lambda: {"character": character, "model": "m1", "voice": "v1",
                     "persona": "default"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def bot(self, state):
        """Make the child report a process state — the guard's only truth."""
        patcher = mock.patch.object(
            self.app["bot_child"], "status",
            lambda: {"state": state, "pid": None, "managed": False,
                     "uptime_s": None, "last_exit": None})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def post(self, verb, **body):
        return await self.client.post(f"/admin/sessions/{verb}",
                                      headers=self.BEARER, json=body)

    async def shelf(self, query=""):
        resp = await self.client.get(
            f"/admin/sessions?character={CHARACTER}{query}", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        return (await resp.json())["sessions"]

    # ── the round trip ──────────────────────────────────────────────────────

    async def test_archive_takes_it_off_the_shelf_and_unarchive_brings_it_back(self):
        resp = await self.post("archive", character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.json(),
                         {"ok": True, "session_id": "session-x", "archived": True})
        self.assertFalse(self.session_path.exists())
        moved = self.archive_dir / "session-x.json"
        self.assertEqual(moved.read_bytes(), self.on_disk, "the bytes are untouched")
        self.assertEqual(oct(self.archive_dir.stat().st_mode)[-3:], "700")

        self.assertEqual(await self.shelf(), [], "the default shelf hides it")
        [row] = await self.shelf("&archived=1")
        self.assertEqual(row["session_id"], "session-x")
        self.assertIs(row["archived"], True)
        self.assertEqual(row["turns"], 1)
        self.assertGreater(row["est_tokens"], 0)

        resp = await self.post("unarchive", character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.json(),
                         {"ok": True, "session_id": "session-x", "archived": False})
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)
        self.assertFalse(moved.exists())
        [row] = await self.shelf()
        self.assertIs(row["archived"], False)

    async def test_archived_all_shows_both_shelves(self):
        session_store.SessionStore(
            session_id="session-y", model="m1", voice="v1", prompt_sha256="d",
            sessions_dir=self.sessions_dir, character=CHARACTER, held=True,
        ).snapshot([{"role": "user", "content": "another"}])
        await self.post("archive", character=CHARACTER, session="session-x")
        self.assertEqual([r["session_id"] for r in await self.shelf()], ["session-y"])
        self.assertEqual([r["session_id"] for r in await self.shelf("&archived=1")],
                         ["session-x"])
        both = {r["session_id"]: r["archived"] for r in await self.shelf("&archived=all")}
        self.assertEqual(both, {"session-x": True, "session-y": False})

    async def test_archiving_twice_is_already_and_not_an_error(self):
        await self.post("archive", character=CHARACTER, session="session-x")
        resp = await self.post("archive", character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.json(), {"ok": True, "session_id": "session-x",
                                             "archived": True, "already": True})
        # …and the mirror: unarchiving a session already on the shelf.
        await self.post("unarchive", character=CHARACTER, session="session-x")
        resp = await self.post("unarchive", character=CHARACTER, session="session-x")
        self.assertEqual(await resp.json(), {"ok": True, "session_id": "session-x",
                                             "archived": False, "already": True})
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_a_name_on_both_shelves_is_refused_and_nothing_is_overwritten(self):
        self.archive_dir.mkdir(mode=0o700)
        older = self.archive_dir / "session-x.json"
        older.write_text('{"messages": []}', encoding="utf-8")
        resp = await self.post("archive", character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 409, await resp.text())
        self.assertIn("already archived", (await resp.json())["error"])
        resp = await self.post("unarchive", character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 409, await resp.text())
        self.assertIn("already on the shelf", (await resp.json())["error"])
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)
        self.assertEqual(older.read_text(encoding="utf-8"), '{"messages": []}')

    async def test_refusals(self):
        cases = [
            ("a traversal id", {"character": CHARACTER, "session": "../../x"}, 400),
            ("the hold marker", {"character": CHARACTER, "session": ".hold-request"}, 400),
            ("no session named", {"character": CHARACTER}, 400),
            ("no companion named", {"session": "session-x"}, 400),
            ("no such companion", {"character": "zz-nobody", "session": "session-x"}, 404),
            ("no such session", {"character": CHARACTER, "session": "session-nope"}, 404),
        ]
        for verb in ("archive", "unarchive"):
            for label, body, want in cases:
                with self.subTest(verb=verb, case=label):
                    resp = await self.client.post(f"/admin/sessions/{verb}",
                                                  headers=self.BEARER, json=body)
                    self.assertEqual(resp.status, want, await resp.text())
                    text = await resp.text()
                    self.assertFalse((await resp.json())["ok"])
                    self.assertNotIn(self._tmp.name, text,
                                     "a refusal must not map the disk")
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_a_body_that_is_not_json_at_all(self):
        resp = await self.client.post("/admin/sessions/archive",
                                      headers=self.BEARER, data=b"<not json>")
        self.assertEqual(resp.status, 400)

    # ── the live-session guard ──────────────────────────────────────────────

    async def test_the_running_companions_shelf_is_read_only(self):
        self.active(CHARACTER)
        for state in ("running", "starting", "stopping"):
            with self.subTest(state=state):
                with mock.patch.object(
                        self.app["bot_child"], "status",
                        lambda s=state: {"state": s, "pid": 1, "managed": True}):
                    resp = await self.post("archive", character=CHARACTER,
                                           session="session-x")
                    self.assertEqual(resp.status, 409, await resp.text())
                    error = (await resp.json())["error"]
                    self.assertIn(f"{CHARACTER} is running", error)
                    self.assertIn("stop the companion first", error)
                    resp = await self.post("unarchive", character=CHARACTER,
                                           session="session-x")
                    self.assertEqual(resp.status, 409)
        self.assertEqual(self.session_path.read_bytes(), self.on_disk,
                         "nothing moved while the companion was up")

    async def test_another_companions_shelf_stays_open_while_one_is_running(self):
        self.active(OTHER)
        self.bot("running")
        resp = await self.post("archive", character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertTrue((self.archive_dir / "session-x.json").is_file())

    async def test_a_bot_that_is_down_guards_nothing(self):
        self.active(CHARACTER)
        self.bot("down")
        resp = await self.post("archive", character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 200, await resp.text())

    async def test_an_unreadable_active_toml_fails_closed(self):
        def _boom():
            raise config_loader.ConfigError("missing config file")

        with mock.patch.object(config_loader, "load_active_selection", _boom):
            for verb in ("archive", "unarchive"):
                with self.subTest(verb=verb):
                    resp = await self.post(verb, character=CHARACTER,
                                           session="session-x")
                    self.assertEqual(resp.status, 409, await resp.text())
                    error = (await resp.json())["error"]
                    self.assertIn("could not be read", error)
                    self.assertNotIn(self._tmp.name, error)
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_the_guard_is_the_first_thing_a_bad_id_still_beats(self):
        """Shape before state: a malformed id is a 400 whatever the bot is
        doing, so the guard never has to answer for a request that could not
        have been valid anyway."""
        self.active(CHARACTER)
        self.bot("running")
        resp = await self.post("archive", character=CHARACTER, session="../x")
        self.assertEqual(resp.status, 400, await resp.text())

    # ── the door ────────────────────────────────────────────────────────────

    async def test_both_routes_need_the_bearer(self):
        for verb in ("archive", "unarchive"):
            resp = await self.client.post(f"/admin/sessions/{verb}",
                                          json={"character": CHARACTER,
                                                "session": "session-x"})
            self.assertEqual(resp.status, 401, verb)

    # ── the contract ────────────────────────────────────────────────────────

    async def test_no_archive_response_or_log_line_carries_a_word_of_it(self):
        logged = []
        from loguru import logger
        sink = logger.add(lambda m: logged.append(str(m)), level="DEBUG")
        try:
            bodies = {
                "archive": await (await self.post(
                    "archive", character=CHARACTER, session="session-x")).text(),
                "archived shelf": json.dumps(await self.shelf("&archived=all")),
                "already": await (await self.post(
                    "archive", character=CHARACTER, session="session-x")).text(),
                "refusal": await (await self.post(
                    "archive", character=CHARACTER, session="session-nope")).text(),
                "unarchive": await (await self.post(
                    "unarchive", character=CHARACTER, session="session-x")).text(),
            }
        finally:
            logger.remove(sink)
        for label, body in bodies.items():
            with self.subTest(response=label):
                self.assertNotIn(SENTINEL, body, f"{label} must never carry content")
                self.assertNotIn(self._tmp.name, body, f"{label} must not name a path")
        for line in logged:
            self.assertNotIn(SENTINEL, line, "no log line may carry content")
            self.assertNotIn(str(self.session_path), line, "no verb logs a path")


if __name__ == "__main__":
    unittest.main()
