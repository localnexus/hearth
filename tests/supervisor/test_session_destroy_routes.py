"""Supervisor — destroy: the one hard verb.

Destroy is the only session verb that takes something away, and it is a SWEEP
rather than an unlink: the file and the session's memory (record, compaction
epochs, and the indexed facts behind them) go in one act. So these tests are
mostly about the seams where that could quietly stop being true —

* the preview touches nothing and says what would go AND what cannot be
  reached;
* the confirmation has to be the exact word the preview named (the session's
  name when it has one, its id otherwise), and a wrong word leaves everything
  where it was;
* memory goes FIRST, so a backend that fails leaves the file on disk and the
  whole act re-runnable — the opposite order would leave a person with the
  conversation gone and the facts extracted from it still banked;
* a sweep someone half-finished by hand (file gone, records still there) can
  be finished;
* a recall-only sitting — the one privacy tier destroy is the ONLY verb for —
  has no record, and the answer says `no-record` rather than pretending;
* and the exposure check: the panel has no audience concept in code, so the
  distinction destroy is offered on is "same machine, or the install said so".

The no-content-read contract holds here as everywhere: a sentinel rides in the
session's messages and may not appear in a plan, an answer, a refusal, or a
log line.

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
from hearth.session import session_store, verbs as verbs_mod

SENTINEL = "ZZ-SENTINEL-DESTROY-ZZ"
CHARACTER = "zz-destroy-test"
OTHER = "zz-destroy-other"


class _FakeBackend:
    """The one curation method destroy reaches through: forget(companion, id)."""

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


class _FakeGlue:
    def __init__(self, backend) -> None:  # noqa: ANN001 — None = companion "none"
        self._backend = backend

    def backend_name_for(self, companion):  # noqa: ANN001
        return self._backend.name if self._backend is not None else "none"

    def curation_backend(self, companion):  # noqa: ANN001
        return self._backend


class SessionDestroyRoutes(AioHTTPTestCase):
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
            memory=None,  # per-test: a _FakeGlue or None
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

        for attr in ("_DATA", "_ROOT"):
            patcher = mock.patch.object(config_loader, attr, self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.active(OTHER)  # the guard has to know whose shelf is live
        self.backend = _FakeBackend()
        self.app["deps"].memory = _FakeGlue(self.backend)

    # ── fixtures ────────────────────────────────────────────────────────────

    def write_session(self, sid, **over) -> Path:
        store = session_store.SessionStore(
            session_id=sid, model="m1", voice="v1", prompt_sha256="d",
            sessions_dir=self.sessions_dir, character=CHARACTER, held=True, **over)
        store.snapshot([{"role": "user", "content": SENTINEL}])
        return self.sessions_dir / f"{sid}.json"

    def record(self, stem) -> Path:
        path = self.records_dir / f"{stem}.json"
        path.write_text(
            '{"companion": "%s", "session_id": "%s", "started": "2026-09-07T09:00:00",'
            ' "ended": "2026-09-07T10:00:00", "name": "",'
            ' "messages": [{"role": "user", "content": "%s"}]}'
            % (CHARACTER, stem, SENTINEL), encoding="utf-8")
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

    async def destroy(self, **body):
        return await self.client.post("/admin/sessions/destroy",
                                      headers=self.BEARER, json=body)

    # ── the preview ─────────────────────────────────────────────────────────

    async def test_the_preview_touches_nothing_and_says_what_would_go(self):
        self.record("session-x")
        self.record("session-x.c2026.09.07")
        resp = await self.destroy(character=CHARACTER, session="session-x")
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertIs(data["destroyed"], False)
        self.assertEqual(data["confirm_with"], "session-x")
        self.assertIn("permanent", data["warning"])
        self.assertEqual(data["plan"]["file"], True)
        self.assertEqual(data["plan"]["memory"], {"records": 2, "backend": True})
        self.assertEqual(data["plan"]["cannot_reach"], list(verbs_mod.CANNOT_REACH))
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)
        self.assertTrue((self.records_dir / "session-x.json").is_file())
        self.assertEqual(self.backend.forgot, [])

    async def test_a_named_session_is_confirmed_by_its_name(self):
        self.write_session("session-n", name="the long walk")
        data = await (await self.destroy(character=CHARACTER,
                                         session="session-n")).json()
        self.assertEqual(data["confirm_with"], "the long walk")
        # …and the id is then NOT the word that works.
        resp = await self.destroy(character=CHARACTER, session="session-n",
                                  confirm="session-n")
        self.assertEqual(resp.status, 409, await resp.text())
        self.assertTrue((self.sessions_dir / "session-n.json").is_file())
        resp = await self.destroy(character=CHARACTER, session="session-n",
                                  confirm="the long walk")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertFalse((self.sessions_dir / "session-n.json").exists())

    async def test_a_wrong_confirmation_touches_nothing(self):
        self.record("session-x")
        for wrong in ("", "OK", "yes", "session-y", "SESSION-X"):
            with self.subTest(confirm=wrong):
                resp = await self.destroy(character=CHARACTER, session="session-x",
                                          confirm=wrong)
                self.assertEqual(resp.status, 409, await resp.text())
                self.assertEqual((await resp.json())["error"],
                                 "confirmation did not match")
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)
        self.assertTrue((self.records_dir / "session-x.json").is_file())
        self.assertEqual(self.backend.forgot, [])

    # ── the act ─────────────────────────────────────────────────────────────

    async def test_the_file_and_the_memory_go_in_one_act(self):
        self.record("session-x")
        self.record("session-x.c2026.09.07")
        other = self.record("session-y")  # another session's record: untouched
        resp = await self.destroy(character=CHARACTER, session="session-x",
                                  confirm="session-x")
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual(data, {
            "ok": True, "destroyed": True, "file": True,
            "memory": {"forgotten": True, "index": "excised"},
            "cannot_reach": list(verbs_mod.CANNOT_REACH),
        })
        self.assertFalse(self.session_path.exists())
        self.assertEqual(self.backend.forgot,
                         [(CHARACTER, "session-x"), (CHARACTER, "session-x.c2026.09.07")])
        self.assertFalse((self.records_dir / "session-x.json").exists())
        self.assertFalse((self.records_dir / "session-x.c2026.09.07.json").exists())
        self.assertTrue(other.is_file())

    async def test_leftover_facts_come_back_as_a_hint(self):
        self.record("session-x")
        self.backend.result = False  # facts banked before keyed retain
        data = await (await self.destroy(character=CHARACTER, session="session-x",
                                         confirm="session-x")).json()
        self.assertEqual(data["memory"]["index"], "leftover-facts")
        self.assertIn("rebuild --clean", data["memory"]["hint"])
        self.assertFalse(self.session_path.exists())

    async def test_a_failed_forget_keeps_the_file(self):
        self.record("session-x")
        self.backend.raise_on_forget = True
        resp = await self.destroy(character=CHARACTER, session="session-x",
                                  confirm="session-x")
        self.assertEqual(resp.status, 502, await resp.text())
        data = await resp.json()
        self.assertEqual(data["stage"], "memory")
        self.assertIs(data["destroyed"], False)
        self.assertEqual(self.session_path.read_bytes(), self.on_disk,
                         "memory first: a failed index update keeps everything")
        self.assertTrue((self.records_dir / "session-x.json").is_file())

    async def test_the_memory_lane_being_down_stops_the_whole_act(self):
        self.record("session-x")
        self.app["deps"].memory = None
        resp = await self.destroy(character=CHARACTER, session="session-x",
                                  confirm="session-x")
        self.assertEqual(resp.status, 409, await resp.text())
        self.assertEqual((await resp.json())["stage"], "memory")
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_a_sitting_that_banked_nothing_says_no_record(self):
        """A recall-only sitting is transcript-ephemeral and retains nothing,
        so destroy — the only verb it is offered — has one thing to do."""
        path = self.write_session("session-r", memory_mode="recall-only")
        preview = await (await self.destroy(character=CHARACTER,
                                            session="session-r")).json()
        self.assertEqual(preview["plan"]["memory"], {"records": 0, "backend": False})
        data = await (await self.destroy(character=CHARACTER, session="session-r",
                                         confirm="session-r")).json()
        self.assertEqual(data["memory"], {"forgotten": False, "index": "no-record"})
        self.assertIs(data["file"], True)
        self.assertFalse(path.exists())
        self.assertEqual(self.backend.forgot, [], "no record, no backend call")

    async def test_an_archived_session_is_destroyed_where_it_lies(self):
        self.record("session-x")
        moved = self.archive_dir / "session-x.json"
        self.archive_dir.mkdir(mode=0o700)
        self.session_path.rename(moved)
        # Without the flag the shelf is empty, and the answer says where it is
        # rather than half-sweeping the memory of a file it cannot see.
        resp = await self.destroy(character=CHARACTER, session="session-x",
                                  confirm="session-x")
        self.assertEqual(resp.status, 404, await resp.text())
        self.assertIn("archived", (await resp.json())["error"])
        self.assertTrue(moved.is_file())
        self.assertTrue((self.records_dir / "session-x.json").is_file())

        preview = await (await self.destroy(character=CHARACTER, session="session-x",
                                            archived=True)).json()
        self.assertIs(preview["plan"]["archived"], True)
        self.assertIs(preview["plan"]["file"], True)
        resp = await self.destroy(character=CHARACTER, session="session-x",
                                  archived=True, confirm="session-x")
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertIs((await resp.json())["file"], True)
        self.assertFalse(moved.exists())
        self.assertFalse((self.records_dir / "session-x.json").exists())

    async def test_a_half_done_sweep_can_be_finished(self):
        self.record("session-x")
        self.session_path.unlink()  # the file removed by hand, the record left
        preview = await (await self.destroy(character=CHARACTER,
                                            session="session-x")).json()
        self.assertIs(preview["plan"]["file"], False)
        self.assertEqual(preview["confirm_with"], "session-x")
        data = await (await self.destroy(character=CHARACTER, session="session-x",
                                         confirm="session-x")).json()
        self.assertIs(data["destroyed"], True)
        self.assertIs(data["file"], False)
        self.assertEqual(data["memory"]["index"], "excised")
        self.assertFalse((self.records_dir / "session-x.json").exists())

    # ── who may destroy ─────────────────────────────────────────────────────

    async def test_off_machine_is_refused_until_the_install_says_otherwise(self):
        with mock.patch.object(verbs_mod, "is_loopback_peer", lambda remote: False):
            resp = await self.destroy(character=CHARACTER, session="session-x")
            self.assertEqual(resp.status, 403, await resp.text())
            error = (await resp.json())["error"]
            self.assertIn("destroy_for_all", error)
            self.assertNotIn(self._tmp.name, error)
            # …and with the setting on, the same call is the ordinary preview.
            self.app["deps"].cfg["sessions"] = {"destroy_for_all": True}
            resp = await self.destroy(character=CHARACTER, session="session-x")
            self.assertEqual(resp.status, 200, await resp.text())
            self.assertIs((await resp.json())["destroyed"], False)
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_the_exposure_check_comes_after_the_shape_and_the_companion(self):
        with mock.patch.object(verbs_mod, "is_loopback_peer", lambda remote: False):
            for body, want in (({"character": CHARACTER}, 400),
                               ({"character": CHARACTER, "session": "../x"}, 400),
                               ({"character": "zz-nobody", "session": "session-x"}, 404),
                               ({"character": CHARACTER, "session": "session-x"}, 403)):
                with self.subTest(body=body):
                    resp = await self.destroy(**body)
                    self.assertEqual(resp.status, want, await resp.text())

    async def test_the_running_companions_shelf_is_read_only(self):
        self.record("session-x")
        self.active(CHARACTER)
        self.bot("running")
        resp = await self.destroy(character=CHARACTER, session="session-x",
                                  confirm="session-x")
        self.assertEqual(resp.status, 409, await resp.text())
        self.assertIn("stop the companion first", (await resp.json())["error"])
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)
        self.assertTrue((self.records_dir / "session-x.json").is_file())

    async def test_refusals(self):
        for label, body, want in (
                ("no session named", {"character": CHARACTER}, 400),
                ("no companion named", {"session": "session-x"}, 400),
                ("a traversal id", {"character": CHARACTER, "session": "../../x"}, 400),
                ("the hold marker",
                 {"character": CHARACTER, "session": ".hold-request"}, 400),
                ("no such companion",
                 {"character": "zz-nobody", "session": "session-x"}, 404),
                ("no such session",
                 {"character": CHARACTER, "session": "session-nope"}, 404)):
            with self.subTest(case=label):
                resp = await self.destroy(**body)
                self.assertEqual(resp.status, want, await resp.text())
                self.assertFalse((await resp.json())["ok"])
                self.assertNotIn(self._tmp.name, await resp.text())
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_a_body_that_is_not_json_at_all(self):
        resp = await self.client.post("/admin/sessions/destroy",
                                      headers=self.BEARER, data=b"<not json>")
        self.assertEqual(resp.status, 400)

    async def test_the_route_needs_the_bearer(self):
        resp = await self.client.post("/admin/sessions/destroy",
                                      json={"character": CHARACTER,
                                            "session": "session-x",
                                            "confirm": "session-x"})
        self.assertEqual(resp.status, 401)
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    # ── the contract ────────────────────────────────────────────────────────

    async def test_no_destroy_response_or_log_line_carries_a_word_of_it(self):
        self.record("session-x")
        logged = []
        from loguru import logger
        sink = logger.add(lambda m: logged.append(str(m)), level="DEBUG")
        try:
            bodies = {
                "preview": await (await self.destroy(
                    character=CHARACTER, session="session-x")).text(),
                "mismatch": await (await self.destroy(
                    character=CHARACTER, session="session-x", confirm="nope")).text(),
                "refusal": await (await self.destroy(
                    character=CHARACTER, session="session-nope")).text(),
                "destroyed": await (await self.destroy(
                    character=CHARACTER, session="session-x",
                    confirm="session-x")).text(),
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
        self.assertTrue(any("destroyed" in line and CHARACTER in line
                            for line in logged), "the one line says what happened")


if __name__ == "__main__":
    unittest.main()
