"""Supervisor — a session file out (reveal, download), in (deposit), aside (archive).

Download hands back the bytes on disk, unchanged and unparsed; reveal hands a
path to the Finder and nothing to the caller. The no-content-read proof is the
same shape the shelf test uses: a sentinel string is written into a session's
messages, and it must appear in exactly ONE place — the download body — and in
no other response and no log line.

Deposit is the way back in, and it is tested for the two things it promises. It
WRITES a real session: the file it leaves behind is one the store's own load()
reads and list_sessions lists, marked held and stamped as a deposit. And it
never REPLACES one: two deposits in the same second are two sessions, because
the id is minted here and never taken from the upload. The sentinel rides in on
a deposited file too, so the same silence is checked on the way in — and on the
way aside, since S4's archive/unarchive answer in the same loop.

Archive also gives S2's `archived=` flag its first real file: reveal and
download are pointed at a session that has actually been moved into `.archive/`
rather than at one written there by hand.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
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

SENTINEL = "ZZ-SENTINEL-WHAT-WAS-SAID-ZZ"
CHARACTER = "zz-file-test"


class SessionFileRoutes(AioHTTPTestCase):
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
        char_dir = self.root / "characters" / CHARACTER
        char_dir.mkdir(parents=True)
        (char_dir / "persona.md").write_text("test persona marker", encoding="utf-8")
        (char_dir / "voices" / "v1").mkdir(parents=True)
        (char_dir / "voices" / "v1" / "voice.toml").write_text("", encoding="utf-8")
        self.sessions_dir = char_dir / "sessions"
        store = session_store.SessionStore(
            session_id="session-x", model="m1", voice="v1", prompt_sha256="d",
            sessions_dir=self.sessions_dir, character=CHARACTER)
        store.snapshot([{"role": "user", "content": SENTINEL}])
        self.session_path = self.sessions_dir / "session-x.json"
        self.on_disk = self.session_path.read_bytes()

        for attr in ("_DATA", "_ROOT"):
            patcher = mock.patch.object(config_loader, attr, self.root)
            patcher.start()
            self.addCleanup(patcher.stop)

    def a_session(self, **over):
        """A well-formed session file as it would arrive from someone's disk."""
        payload = {
            "schema": 2, "model": "m1", "voice": "v1", "persona": "default",
            "started": "2026-09-07T09:00:00", "updated": "2026-09-07T09:30:00",
            "held": False, "character": CHARACTER,
            "messages": [{"role": "system", "content": "a smuggled prompt"},
                         {"role": "user", "content": SENTINEL},
                         {"role": "assistant", "content": "said back"}],
        }
        payload.update(over)
        return payload

    async def deposit_json(self, **over):
        return await self.client.post(
            "/admin/sessions/deposit", headers=self.BEARER,
            json={"character": over.pop("character_field", CHARACTER),
                  "session": self.a_session(**over)})

    async def deposit_multipart(self, session=None, character=CHARACTER):
        form = aiohttp.FormData()
        form.add_field("character", character)
        form.add_field("file",
                       json.dumps(session if session is not None else self.a_session()),
                       filename="session.json", content_type="application/json")
        return await self.client.post("/admin/sessions/deposit",
                                      headers=self.BEARER, data=form)

    # ── download ────────────────────────────────────────────────────────────

    async def test_download_streams_the_file_byte_identical(self):
        resp = await self.client.get(
            f"/admin/sessions/file?character={CHARACTER}&session=session-x",
            headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.read(), self.on_disk)
        self.assertEqual(resp.headers["Content-Type"], "application/json")
        self.assertEqual(resp.headers["Content-Disposition"],
                         f'attachment; filename="{CHARACTER}-session-x.json"')

    async def test_download_accepts_the_json_suffix_and_the_archive(self):
        resp = await self.client.get(
            f"/admin/sessions/file?character={CHARACTER}&session=session-x.json",
            headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        arch = self.sessions_dir / verbs_mod.ARCHIVE_DIR
        arch.mkdir()
        (arch / "session-old.json").write_text('{"messages": []}', encoding="utf-8")
        resp = await self.client.get(
            f"/admin/sessions/file?character={CHARACTER}&session=session-old&archived=1",
            headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())

    async def test_download_refusals(self):
        cases = [
            (f"?character={CHARACTER}&session=..%2F..%2Fetc%2Fpasswd", 400),
            (f"?character={CHARACTER}&session=session-nope", 404),
            (f"?character={CHARACTER}", 400),
            ("?character=zz-nobody&session=session-x", 404),
        ]
        for query, want in cases:
            with self.subTest(query=query):
                resp = await self.client.get("/admin/sessions/file" + query,
                                             headers=self.BEARER)
                self.assertEqual(resp.status, want, await resp.text())
                body = await resp.text()
                self.assertNotIn(self._tmp.name, body, "a refusal must not map the disk")

    async def test_both_routes_need_the_bearer(self):
        resp = await self.client.get(
            f"/admin/sessions/file?character={CHARACTER}&session=session-x")
        self.assertEqual(resp.status, 401)
        resp = await self.client.post("/admin/sessions/reveal",
                                      json={"character": CHARACTER, "session": "session-x"})
        self.assertEqual(resp.status, 401)

    # ── reveal ──────────────────────────────────────────────────────────────

    async def test_reveal_is_refused_off_machine(self):
        with mock.patch.object(verbs_mod, "is_loopback_peer", lambda remote: False):
            resp = await self.client.post(
                "/admin/sessions/reveal", headers=self.BEARER,
                json={"character": CHARACTER, "session": "session-x"})
        self.assertEqual(resp.status, 409)
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertIn("same machine", data["error"])
        self.assertIn("download", data["error"])

    async def test_reveal_runs_the_fixed_argv_on_this_machine(self):
        seen = {}

        def _argv(path):
            seen["path"] = Path(path)
            return [sys.executable, "-c", ""]

        with mock.patch.object(verbs_mod, "reveal_argv", _argv):
            resp = await self.client.post(
                "/admin/sessions/reveal", headers=self.BEARER,
                json={"character": CHARACTER, "session": "session-x"})
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual(data, {"ok": True, "session_id": "session-x"})
        self.assertEqual(seen["path"], self.session_path.resolve())

    async def test_reveal_refusals(self):
        for body, want in (({"character": CHARACTER, "session": "../x"}, 400),
                           ({"character": CHARACTER, "session": "session-nope"}, 404),
                           ({"character": CHARACTER}, 400),
                           ({"character": "zz-nobody", "session": "session-x"}, 404)):
            with self.subTest(body=body):
                resp = await self.client.post("/admin/sessions/reveal",
                                              headers=self.BEARER, json=body)
                self.assertEqual(resp.status, want, await resp.text())
                self.assertNotIn(self._tmp.name, await resp.text())

    async def test_download_reaches_a_really_archived_session(self):
        """S4 gives the archived= flag a real file to hit: archive the session
        through the verb, and the download still finds it — byte-identical."""
        verbs_mod.archive_session(CHARACTER, "session-x")
        resp = await self.client.get(
            f"/admin/sessions/file?character={CHARACTER}&session=session-x",
            headers=self.BEARER)
        self.assertEqual(resp.status, 404, "…and only where it now is")
        resp = await self.client.get(
            f"/admin/sessions/file?character={CHARACTER}&session=session-x&archived=1",
            headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(await resp.read(), self.on_disk)

    async def test_reveal_reaches_a_really_archived_session(self):
        verbs_mod.archive_session(CHARACTER, "session-x")
        seen = {}

        def _argv(path):
            seen["path"] = Path(path)
            return [sys.executable, "-c", ""]

        with mock.patch.object(verbs_mod, "reveal_argv", _argv):
            resp = await self.client.post(
                "/admin/sessions/reveal", headers=self.BEARER,
                json={"character": CHARACTER, "session": "session-x",
                      "archived": True})
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(seen["path"].parent.name, verbs_mod.ARCHIVE_DIR)

    async def test_reveal_says_so_where_there_is_no_finder(self):
        with mock.patch.object(verbs_mod, "reveal_argv", lambda path: []):
            resp = await self.client.post(
                "/admin/sessions/reveal", headers=self.BEARER,
                json={"character": CHARACTER, "session": "session-x"})
        self.assertEqual(resp.status, 501)


    # ── deposit ─────────────────────────────────────────────────────────────

    async def test_a_multipart_deposit_becomes_a_session_on_the_shelf(self):
        resp = await self.deposit_multipart()
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["character"], CHARACTER)
        self.assertEqual(data["turns"], 1)
        self.assertEqual(data["dropped_system_messages"], 1)
        written = self.sessions_dir / f"{data['session_id']}.json"
        on_disk = session_store.load(written)  # the store reads its own back
        self.assertEqual(on_disk["schema"], session_store.SCHEMA)
        self.assertIs(on_disk["held"], True)
        self.assertEqual(on_disk["origin"], "deposit")
        self.assertNotIn("system", [m["role"] for m in on_disk["messages"]])
        self.assertEqual(oct(written.stat().st_mode)[-3:], "600")
        [meta] = [m for m in session_store.list_sessions(self.sessions_dir)
                  if m.session_id == data["session_id"]]
        self.assertEqual(meta.origin, "deposit")
        self.assertTrue(meta.held)
        self.assertEqual(meta.turns, 1)

    async def test_a_json_body_deposit_lands_the_same_way(self):
        resp = await self.deposit_json()
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        on_disk = session_store.load(self.sessions_dir / f"{data['session_id']}.json")
        self.assertEqual(on_disk["character"], CHARACTER)
        self.assertEqual(on_disk["voice"], "v1")

    async def test_a_deposit_never_replaces_a_session(self):
        first = await (await self.deposit_json()).json()
        second = await (await self.deposit_multipart()).json()
        self.assertNotEqual(first["session_id"], second["session_id"])
        for sid in (first["session_id"], second["session_id"]):
            self.assertTrue((self.sessions_dir / f"{sid}.json").is_file())
        # …and the session that was already there is untouched.
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_the_upload_cannot_choose_its_own_id(self):
        resp = await self.deposit_json(session_id="session-x", name="session-x")
        data = await resp.json()
        self.assertNotEqual(data["session_id"], "session-x")
        self.assertEqual(self.session_path.read_bytes(), self.on_disk)

    async def test_a_file_over_the_cap_is_refused_before_it_is_parsed(self):
        with mock.patch.object(verbs_mod, "MAX_DEPOSIT_BYTES", 64):
            resp = await self.deposit_multipart()
            self.assertEqual(resp.status, 413, await resp.text())
            resp = await self.deposit_json()
            self.assertEqual(resp.status, 413, await resp.text())
        self.assertEqual(list(self.sessions_dir.glob("session-*.json")),
                         [self.session_path])

    async def test_deposit_refusals(self):
        cases = [
            ("no session in the body", {"character": CHARACTER}, 400, "no session"),
            ("a session that is not an object", {"character": CHARACTER,
                                                 "session": "hello"}, 400,
             "not a session file"),
            ("another companion's", {"character": CHARACTER,
                                     "session": self.a_session(character="zz-other")},
             400, "another companion"),
            ("a voice this one has not", {"character": CHARACTER,
                                          "session": self.a_session(voice="v-nope")},
             400, "voice"),
            ("no such companion", {"character": "zz-nobody",
                                   "session": self.a_session()}, 404, "unknown"),
            ("no companion named", {"session": self.a_session()}, 400, "character"),
        ]
        for label, body, want, needle in cases:
            with self.subTest(case=label):
                resp = await self.client.post("/admin/sessions/deposit",
                                              headers=self.BEARER, json=body)
                self.assertEqual(resp.status, want, await resp.text())
                text = await resp.text()
                self.assertIn(needle, text)
                self.assertNotIn(self._tmp.name, text, "a refusal must not map the disk")

    async def test_a_body_that_is_not_json_at_all(self):
        resp = await self.client.post(
            "/admin/sessions/deposit", headers=self.BEARER, data=b"<not json>")
        self.assertEqual(resp.status, 400)
        self.assertIn("JSON", await resp.text())

    # ── the contract: content is read by nothing but the download ───────────

    async def test_no_route_but_the_download_carries_a_word_of_it(self):
        """The sentinel lives in the session's messages. It may appear in the
        download body and NOWHERE else — not in the shelf, not in reveal, not
        in a refusal, not in destroy's plan or its answer, and not in a log
        line. Destroy runs LAST here, because it is the verb that ends the
        file."""
        logged = []
        from hearth.supervisor.routes import sessions as _s
        with mock.patch.object(verbs_mod, "reveal_argv",
                               lambda path: [sys.executable, "-c", ""]), \
                mock.patch.object(_s, "REVEAL_TIMEOUT_S", 10.0):
            from loguru import logger
            sink = logger.add(lambda m: logged.append(str(m)), level="DEBUG")
            try:
                shelf = await self.client.get(f"/admin/sessions?character={CHARACTER}",
                                              headers=self.BEARER)
                shelf_body = await shelf.text()
                reveal = await self.client.post(
                    "/admin/sessions/reveal", headers=self.BEARER,
                    json={"character": CHARACTER, "session": "session-x"})
                reveal_body = await reveal.text()
                missing = await self.client.get(
                    f"/admin/sessions/file?character={CHARACTER}&session=session-nope",
                    headers=self.BEARER)
                missing_body = await missing.text()
                download = await self.client.get(
                    f"/admin/sessions/file?character={CHARACTER}&session=session-x",
                    headers=self.BEARER)
                download_body = await download.text()
                deposit = await self.deposit_multipart()
                deposit_body = await deposit.text()
                rejected = await self.client.post(
                    "/admin/sessions/deposit", headers=self.BEARER,
                    json={"character": CHARACTER,
                          "session": self.a_session(voice="v-nope")})
                rejected_body = await rejected.text()
                with mock.patch.object(
                        config_loader, "load_active_selection",
                        lambda: {"character": "zz-someone-else", "model": "m1",
                                 "voice": "v1", "persona": "default"}):
                    archive = await self.client.post(
                        "/admin/sessions/archive", headers=self.BEARER,
                        json={"character": CHARACTER, "session": "session-x"})
                    archive_body = await archive.text()
                    archived_shelf = await self.client.get(
                        f"/admin/sessions?character={CHARACTER}&archived=all",
                        headers=self.BEARER)
                    archived_shelf_body = await archived_shelf.text()
                    unarchive = await self.client.post(
                        "/admin/sessions/unarchive", headers=self.BEARER,
                        json={"character": CHARACTER, "session": "session-x"})
                    unarchive_body = await unarchive.text()
                    destroy_plan = await self.client.post(
                        "/admin/sessions/destroy", headers=self.BEARER,
                        json={"character": CHARACTER, "session": "session-x"})
                    destroy_plan_body = await destroy_plan.text()
                    destroyed = await self.client.post(
                        "/admin/sessions/destroy", headers=self.BEARER,
                        json={"character": CHARACTER, "session": "session-x",
                              "confirm": "session-x"})
                    destroyed_body = await destroyed.text()
            finally:
                logger.remove(sink)
        self.assertEqual(shelf.status, 200)
        self.assertEqual(json.loads(shelf_body)["sessions"][0]["turns"], 1)
        self.assertEqual(deposit.status, 200, deposit_body)
        self.assertEqual(archive.status, 200, archive_body)
        self.assertEqual(unarchive.status, 200, unarchive_body)
        self.assertEqual(destroy_plan.status, 200, destroy_plan_body)
        self.assertEqual(destroyed.status, 200, destroyed_body)
        self.assertFalse(self.session_path.exists(), "destroy is the hard verb")
        for label, body in (("shelf", shelf_body), ("reveal", reveal_body),
                            ("refusal", missing_body), ("deposit", deposit_body),
                            ("deposit refusal", rejected_body),
                            ("archive", archive_body),
                            ("archived shelf", archived_shelf_body),
                            ("unarchive", unarchive_body),
                            ("destroy plan", destroy_plan_body),
                            ("destroyed", destroyed_body)):
            with self.subTest(response=label):
                self.assertNotIn(SENTINEL, body, f"{label} must never carry content")
        self.assertIn(SENTINEL, download_body, "the download IS the content")
        for line in logged:
            self.assertNotIn(SENTINEL, line, "no log line may carry content")
            self.assertNotIn(str(self.session_path), line, "no verb logs a path")


if __name__ == "__main__":
    unittest.main()
