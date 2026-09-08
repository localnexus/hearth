"""Supervisor — rename: the title anyone may change, the id almost nobody may.

Two verbs share one route, and the whole design is in the difference between
them. A **title** is a label on the file's own metadata: nothing outside the
file reads it, so changing it can break nothing and is offered freely. An
**id** is a key — the memory record and its compaction epochs are filed under
it, a queued compaction names it, the hold marker may be holding it — so
renaming the FILE is offered only when nothing else knows the session by that
name, and refused with the list of what does.

So these tests pin the seams where that could stop being true:

* exactly one of the two fields, because the route must never guess which act
  was meant;
* the title form writes the field and the shelf reads it back;
* the id form moves the file and the shelf shows the new id;
* a single memory record is enough to refuse the id form, and the refusal
  carries the reasons the panel is meant to show;
* the guard, the bearer, and the fence still hold;
* and the no-content-read contract: this is the one verb that PARSES a session
  file, so a sentinel in the messages gets its own check in every answer, every
  refusal and every log line.

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

SENTINEL = "ZZ-SENTINEL-RENAME-ZZ"
CHARACTER = "zz-rename-test"
OTHER = "zz-rename-other"


class SessionRenameRoutes(AioHTTPTestCase):
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
            memory=None,
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
        self.records_dir = self.root / "characters" / CHARACTER / "memory" / "records"
        self.records_dir.mkdir(parents=True)
        self.session_path = self.write_session("session-x")
        self.on_disk = self.session_path.read_bytes()
        self.archive_dir = self.sessions_dir / verbs_mod.ARCHIVE_DIR

        for attr in ("_DATA", "_ROOT", "DATA_DIR"):
            patcher = mock.patch.object(config_loader, attr, self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.active(OTHER)  # the guard has to know whose shelf is live

    # ── fixtures ────────────────────────────────────────────────────────────

    def write_session(self, sid, **over) -> Path:
        store = session_store.SessionStore(
            session_id=sid, model="m1", voice="v1", prompt_sha256="d",
            sessions_dir=self.sessions_dir, character=CHARACTER, held=True, **over)
        store.snapshot([{"role": "user", "content": SENTINEL}])
        return self.sessions_dir / f"{sid}.json"

    def record(self, stem) -> Path:
        path = self.records_dir / f"{stem}.json"
        path.write_text("{}", encoding="utf-8")
        return path

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

    async def rename(self, **body):
        return await self.client.post("/admin/sessions/rename",
                                      headers=self.BEARER, json=body)

    async def shelf(self, query=""):
        resp = await self.client.get(f"/admin/sessions?character={CHARACTER}{query}",
                                     headers=self.BEARER)
        return (await resp.json())["sessions"]

    def on_file(self, sid="session-x"):
        return json.loads((self.sessions_dir / f"{sid}.json").read_text(
            encoding="utf-8"))

    # ── the title: the ordinary rename ──────────────────────────────────────

    async def test_a_title_lands_and_the_shelf_shows_it(self):
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 title="  the long walk  ")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.json(),
                         {"ok": True, "session_id": "session-x",
                          "title": "the long walk"})
        self.assertEqual(self.on_file()["title"], "the long walk")
        rows = await self.shelf()
        self.assertEqual([r["title"] for r in rows], ["the long walk"])
        self.assertEqual(rows[0]["session_id"], "session-x")

    async def test_an_empty_title_removes_it(self):
        await self.rename(character=CHARACTER, session="session-x", title="t")
        resp = await self.rename(character=CHARACTER, session="session-x", title="")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertIsNone((await resp.json())["title"])
        self.assertNotIn("title", self.on_file())
        self.assertEqual((await self.shelf())[0]["title"], None)
        self.assertEqual(self.session_path.read_bytes(), self.on_disk,
                         "an untitled file is byte-identical to what it was")

    async def test_the_conversation_is_untouched_by_a_title(self):
        await self.rename(character=CHARACTER, session="session-x",
                          title="the long walk")
        self.assertEqual(self.on_file()["messages"],
                         json.loads(self.on_disk.decode("utf-8"))["messages"])
        self.assertEqual((await self.shelf())[0]["turns"], 1)

    async def test_a_title_a_person_may_not_type(self):
        for bad in ("x" * 121, "two\nlines", "bell\x07"):
            with self.subTest(title=bad):
                resp = await self.rename(character=CHARACTER, session="session-x",
                                         title=bad)
                self.assertEqual(resp.status, 400, await resp.text())
                self.assertFalse((await resp.json())["ok"])
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_a_referenced_session_may_still_be_titled(self):
        """The whole point of the title being the default rename: it is free
        even when everything in the world points at the id."""
        self.record("session-x")
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 title="the long walk")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertTrue((self.records_dir / "session-x.json").is_file())

    async def test_an_archived_session_is_titled_where_it_lies(self):
        self.archive_dir.mkdir(mode=0o700)
        self.session_path.rename(self.archive_dir / "session-x.json")
        resp = await self.rename(character=CHARACTER, session="session-x", title="t")
        self.assertEqual(resp.status, 404, await resp.text())
        self.assertIn("archived", (await resp.json())["error"])
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 archived=True, title="t")
        self.assertEqual(resp.status, 200, await resp.text())
        rows = await self.shelf("&archived=1")
        self.assertEqual([(r["session_id"], r["title"], r["archived"]) for r in rows],
                         [("session-x", "t", True)])

    # ── the id: the rename that has to ask permission ───────────────────────

    async def test_an_unreferenced_session_may_change_its_id(self):
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 new_id="session-z")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.json(),
                         {"ok": True, "session_id": "session-z",
                          "previous_id": "session-x"})
        self.assertFalse(self.session_path.exists())
        self.assertEqual((self.sessions_dir / "session-z.json").read_bytes(),
                         self.on_disk, "a move, never a rewrite")
        self.assertEqual([r["session_id"] for r in await self.shelf()], ["session-z"])

    async def test_a_memory_record_refuses_the_id_and_says_why(self):
        self.record("session-x")
        self.record("session-x.c2026.09.07")
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 new_id="session-z")
        self.assertEqual(resp.status, 409, await resp.text())
        data = await resp.json()
        self.assertEqual(data["error"],
                         "this session is referenced — rename its title instead")
        self.assertEqual(data["references"],
                         ["a memory record (and 1 compaction epoch)"])
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)
        self.assertFalse((self.sessions_dir / "session-z.json").exists())

    async def test_a_parked_compaction_refuses_the_id(self):
        queue = self.root / "ops" / "compact-queue"
        queue.mkdir(parents=True)
        (queue / f"{CHARACTER}.session-x.request").write_text("{}", encoding="utf-8")
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 new_id="session-z")
        self.assertEqual(resp.status, 409, await resp.text())
        self.assertEqual((await resp.json())["references"],
                         ["a parked compaction request"])

    async def test_a_taken_id_is_refused_and_neither_file_moves(self):
        self.write_session("session-z")
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 new_id="session-z")
        self.assertEqual(resp.status, 409, await resp.text())
        self.assertIn("already exists", (await resp.json())["error"])
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_an_archived_session_is_renamed_inside_the_archive(self):
        self.archive_dir.mkdir(mode=0o700)
        self.session_path.rename(self.archive_dir / "session-x.json")
        resp = await self.rename(character=CHARACTER, session="session-x",
                                 archived=True, new_id="session-z")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertTrue((self.archive_dir / "session-z.json").is_file())
        self.assertFalse((self.sessions_dir / "session-z.json").exists())

    # ── the shape of the ask ────────────────────────────────────────────────

    async def test_exactly_one_of_the_two_fields(self):
        for label, body in (
                ("neither", {"character": CHARACTER, "session": "session-x"}),
                ("both", {"character": CHARACTER, "session": "session-x",
                          "title": "t", "new_id": "session-z"})):
            with self.subTest(case=label):
                resp = await self.rename(**body)
                self.assertEqual(resp.status, 400, await resp.text())
                self.assertIn("exactly one", (await resp.json())["error"])
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_refusals(self):
        for label, body, want in (
                ("no session named", {"character": CHARACTER, "title": "t"}, 400),
                ("no companion named", {"session": "session-x", "title": "t"}, 400),
                ("a traversal id",
                 {"character": CHARACTER, "session": "../../x", "title": "t"}, 400),
                ("a traversal destination",
                 {"character": CHARACTER, "session": "session-x",
                  "new_id": "../../x"}, 400),
                ("no such companion",
                 {"character": "zz-nobody", "session": "session-x", "title": "t"}, 404),
                ("no such session",
                 {"character": CHARACTER, "session": "session-nope", "title": "t"}, 404)):
            with self.subTest(case=label):
                resp = await self.rename(**body)
                self.assertEqual(resp.status, want, await resp.text())
                self.assertFalse((await resp.json())["ok"])
                self.assertNotIn(self._tmp.name, await resp.text())
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_a_body_that_is_not_json_at_all(self):
        resp = await self.client.post("/admin/sessions/rename",
                                      headers=self.BEARER, data=b"<not json>")
        self.assertEqual(resp.status, 400)

    async def test_the_running_companions_shelf_is_read_only(self):
        self.active(CHARACTER)
        self.bot("running")
        for body in ({"character": CHARACTER, "session": "session-x", "title": "t"},
                     {"character": CHARACTER, "session": "session-x",
                      "new_id": "session-z"}):
            with self.subTest(form=sorted(body)):
                resp = await self.rename(**body)
                self.assertEqual(resp.status, 409, await resp.text())
                self.assertIn("stop the companion first", (await resp.json())["error"])
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_the_route_needs_the_bearer(self):
        resp = await self.client.post("/admin/sessions/rename",
                                      json={"character": CHARACTER,
                                            "session": "session-x", "title": "t"})
        self.assertEqual(resp.status, 401)
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    # ── the contract ────────────────────────────────────────────────────────

    async def test_no_rename_response_or_log_line_carries_a_word_of_it(self):
        """Rename is the one verb that opens a session file, so this is the
        check that matters most here."""
        self.record("session-x")
        self.write_session("session-y")  # unreferenced: the successful rename
        logged = []
        from loguru import logger
        sink = logger.add(lambda m: logged.append(str(m)), level="DEBUG")
        try:
            bodies = {
                "titled": await (await self.rename(
                    character=CHARACTER, session="session-x",
                    title="the long walk")).text(),
                "untitled": await (await self.rename(
                    character=CHARACTER, session="session-x", title="")).text(),
                "bad title": await (await self.rename(
                    character=CHARACTER, session="session-x",
                    title="x" * 200)).text(),
                "id refused": await (await self.rename(
                    character=CHARACTER, session="session-x",
                    new_id="session-z")).text(),
                "shape refusal": await (await self.rename(
                    character=CHARACTER, session="session-x")).text(),
                "renamed": await (await self.rename(
                    character=CHARACTER, session="session-y",
                    new_id="session-w")).text(),
                "shelf": await (await self.client.get(
                    f"/admin/sessions?character={CHARACTER}",
                    headers=self.BEARER)).text(),
            }
        finally:
            logger.remove(sink)
        for label, body in bodies.items():
            with self.subTest(response=label):
                self.assertNotIn(SENTINEL, body, f"{label} must never carry content")
                self.assertNotIn(self._tmp.name, body, "no response maps the disk")
        for line in logged:
            self.assertNotIn(SENTINEL, line, "no log line may carry content")
            self.assertNotIn(str(self.session_path), line, "no verb logs a path")


if __name__ == "__main__":
    unittest.main()
