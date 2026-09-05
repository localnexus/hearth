"""The first-run surface: the sixth shell, the two facts, and the one write.

/admin/first-run is what waits behind the door ``python -m hearth.init`` opens
— the guided half of the first-run path. Pinned here, against a scratch DATA
tree built from the SHIPPED templates (so the placeholder id is the real one):

  * the shell is static chrome — served without the bearer, carrying no state;
  * /admin/first-run/state and /admin/state agree on the two facts (the
    placeholder id, "nothing said yet") and both flip when the disk does;
  * recording a model id writes the advertised string into the SELECTED
    model.toml by the bootstrap's own surgery (comments kept, .prev beside
    it), refuses an id the server does not advertise unless told yes, and
    refuses an unreachable server the same way;
  * a selection that still resolves to the shipped tree is copied-on-write;
  * the LM token the facade holds rides the probe and never a response.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth import supervisor
from hearth.config import config_loader
from hearth.supervisor import switch as switch_mod
from hearth.supervisor.child import BotChild

_PY = sys.executable
_NOMATCH = "zz-hearth-test-nomatch-zz"
GRACEFUL = ("import signal, sys, time\n"
            "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
            "while True: time.sleep(0.1)\n")
ADVERTISED = ["zz-model-a", "zz-model-b"]
LM_TOKEN = "zz-lm-token-never-answered"
TEMPLATE_COMMENT = "# The exact model id your inference server advertises, VERBATIM."


def _fake(src: str) -> BotChild:
    return BotChild(argv=[_PY, "-c", src], pattern=_NOMATCH,
                    stop_grace_s=5.0, term_grace_s=1.0)


class _Models(BaseHTTPRequestHandler):
    """A stand-in OpenAI-compatible server: /v1/models only."""
    seen_auth: list = []

    def do_GET(self):  # noqa: N802 — http.server's name
        _Models.seen_auth.append(self.headers.get("Authorization", ""))
        if self.path.rstrip("/").endswith("/models"):
            body = json.dumps({"data": [{"id": m} for m in ADVERTISED]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):  # quiet
        pass


class FirstRun(AioHTTPTestCase):
    BEARER = {"Authorization": "Bearer test-bearer"}

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Models)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.lm_url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer", cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url=self.lm_url, lm_token=LM_TOKEN, session=None, character="example")
        supervisor.build_mount({"enabled": True, "panel_url": "http://127.0.0.1:1",
                                "compact_watch": False})(app)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app["bot_child"].close()
        self.app["bot_child"] = _fake(GRACEFUL)
        _Models.seen_auth = []
        # A scratch DATA root holding exactly what the bootstrap copies: the
        # shipped templates, placeholder and all.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        shipped = config_loader._ROOT
        self.active = self.root / "config" / "active.toml"
        self.model = self.root / "config" / "models" / "example" / "model.toml"
        self.model.parent.mkdir(parents=True)
        shutil.copyfile(shipped / "config" / "active.toml.example", self.active)
        shutil.copyfile(shipped / "config" / "models" / "example" / "model.toml.example",
                        self.model)
        for target, attr, value in ((config_loader, "_DATA", self.root),
                                    (switch_mod, "active_path", lambda: self.active)):
            patch = mock.patch.object(target, attr, value)
            patch.start()
            self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def _state(self) -> dict:
        resp = await self.client.get("/admin/first-run/state", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        return await resp.json()

    async def _record(self, body) -> tuple:
        resp = await self.client.post("/admin/first-run/model", headers=self.BEARER,
                                      json=body)
        return resp.status, await resp.json()

    # ── the shell ────────────────────────────────────────────────────────────

    async def test_shell_is_static_chrome(self):
        resp = await self.client.get("/admin/first-run")   # no bearer
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("text/html", resp.headers["Content-Type"])
        self.assertIn("/admin/first-run/state", text, "the page fetches its facts")
        self.assertIn("HearthSwitchCard.mount(", text, "Start is the shared card")
        for baked in ("test-bearer", LM_TOKEN, "zz-model", self.lm_url):
            self.assertNotIn(baked, text, "no state baked into the shell")
        resp = await self.client.get("/admin/first-run", headers=self.BEARER)
        self.assertEqual(resp.status, 200)

    async def test_state_needs_the_bearer(self):
        for path in ("/admin/first-run/state",):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 401, path)
        resp = await self.client.post("/admin/first-run/model", json={"id": "x"})
        self.assertEqual(resp.status, 401)

    # ── the two facts ────────────────────────────────────────────────────────

    async def test_a_fresh_install_reads_as_first_run(self):
        st = await self._state()
        self.assertTrue(st["first_run"])
        self.assertTrue(st["needs_model"])
        self.assertTrue(st["fresh"])
        self.assertEqual(st["selection"]["character"], "example")
        self.assertEqual(st["model"], {"name": "example", "id": "your-model-id-here",
                                       "id_set": False})
        self.assertEqual(st["lm"], {"url": self.lm_url, "reachable": True,
                                    "models": ADVERTISED})
        self.assertEqual(st["bot"]["state"], "down")
        # The facade's LM token rode the probe …
        self.assertIn(f"Bearer {LM_TOKEN}", _Models.seen_auth)
        # … and never the answer.
        self.assertNotIn(LM_TOKEN, json.dumps(st))

    async def test_admin_state_carries_the_same_facts(self):
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["first_run"], {"needs_model": True, "fresh": True})
        self.assertNotIn("first_run", data["externals"], "a built-in, not a watch")

    async def test_the_facts_flip_with_the_disk(self):
        sessions = self.root / "characters" / "example" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "zz-session.json").write_text("{}", encoding="utf-8")
        st = await self._state()
        self.assertFalse(st["fresh"])
        self.assertTrue(st["first_run"], "the placeholder alone keeps the offer up")
        status, _ = await self._record({"id": "zz-model-a"})
        self.assertEqual(status, 200)
        st = await self._state()
        self.assertEqual((st["needs_model"], st["fresh"], st["first_run"]),
                         (False, False, False))
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual((await resp.json())["first_run"],
                         {"needs_model": False, "fresh": False})

    async def test_no_active_toml_is_still_an_answer(self):
        self.active.unlink()
        st = await self._state()
        self.assertIsNone(st["selection"])
        self.assertTrue(st["needs_model"])
        status, data = await self._record({"id": "zz-model-a"})
        self.assertEqual(status, 409)
        self.assertIn("hearth.init", data["error"])
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual((await resp.json())["first_run"]["needs_model"], True)

    # ── the one write ────────────────────────────────────────────────────────

    async def test_recording_an_advertised_id(self):
        before = self.model.read_text(encoding="utf-8")
        status, data = await self._record({"id": "zz-model-a"})
        self.assertEqual(status, 200, data)
        self.assertEqual((data["ok"], data["written"], data["model"], data["id"],
                          data["advertised"], data["target"], data["backup"]),
                         (True, True, "example", "zz-model-a", True, "in place",
                          "model.toml.prev"))
        self.assertIn("at Start", data["effect"])
        after = self.model.read_text(encoding="utf-8")
        self.assertIn('\nid = "zz-model-a"\n', after)
        self.assertNotIn("your-model-id-here", after)
        self.assertIn(TEMPLATE_COMMENT, after, "the surgery keeps the comments")
        self.assertEqual(len(after.splitlines()), len(before.splitlines()),
                         "one line changed, nothing added")
        self.assertEqual(self.model.with_name("model.toml.prev").read_text(encoding="utf-8"),
                         before)
        st = await self._state()
        self.assertEqual(st["model"], {"name": "example", "id": "zz-model-a", "id_set": True})
        # Again: nothing to do, honestly reported, file byte-identical.
        status, data = await self._record({"id": "zz-model-a"})
        self.assertEqual((status, data["ok"], data["written"]), (200, True, False))
        self.assertEqual(self.model.read_text(encoding="utf-8"), after)

    async def test_an_unadvertised_id_needs_yes(self):
        before = self.model.read_bytes()
        status, data = await self._record({"id": "zz-somewhere-else"})
        self.assertEqual(status, 409)
        self.assertIn("not among the ids", data["error"])
        self.assertEqual(data["advertised"], ADVERTISED)
        self.assertIn('"yes": true', data["confirm"])
        self.assertEqual(self.model.read_bytes(), before, "refused = untouched")
        status, data = await self._record({"id": "zz-somewhere-else", "yes": True})
        self.assertEqual((status, data["written"], data["advertised"]), (200, True, False))
        self.assertIn('id = "zz-somewhere-else"', self.model.read_text(encoding="utf-8"))

    async def test_an_unreachable_server_is_said_plainly(self):
        self.app["deps"].lm_base_url = "http://127.0.0.1:1/v1"
        st = await self._state()
        self.assertEqual(st["lm"], {"url": "http://127.0.0.1:1/v1", "reachable": False,
                                    "models": None})
        status, data = await self._record({"id": "zz-model-a"})
        self.assertEqual(status, 409)
        self.assertIn("did not answer", data["error"])
        self.assertIsNone(data["advertised"])
        status, data = await self._record({"id": "zz-model-a", "yes": True})
        self.assertEqual((status, data["written"]), (200, True))

    async def test_bad_bodies(self):
        for body in ({}, {"id": ""}, {"id": "a\nb"}, {"id": "x" * 201}):
            status, data = await self._record(body)
            self.assertEqual(status, 400, body)
            self.assertFalse(data["ok"])
        resp = await self.client.post("/admin/first-run/model", headers=self.BEARER,
                                      data=b"not json")
        self.assertEqual(resp.status, 400)

    async def test_a_shipped_model_file_is_copied_on_write(self):
        """The selection resolving into the engine tree (DATA ≠ ROOT, no copy
        yet): the id lands in a DATA copy and the shipped file is untouched."""
        shipped_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(shipped_tmp.cleanup)
        shipped = Path(shipped_tmp.name)
        src = shipped / "config" / "models" / "example" / "model.toml"
        src.parent.mkdir(parents=True)
        shutil.copyfile(self.model, src)
        self.model.unlink()   # DATA holds active.toml only
        with mock.patch.object(config_loader, "_ROOT", shipped):
            status, data = await self._record({"id": "zz-model-b"})
        self.assertEqual(status, 200, data)
        self.assertIn("copied to the data root", data["target"])
        self.assertIsNone(data["backup"], "a fresh copy has no previous generation")
        self.assertIn('id = "zz-model-b"', self.model.read_text(encoding="utf-8"))
        self.assertIn("your-model-id-here", src.read_text(encoding="utf-8"),
                      "the shipped file is never edited in place")


if __name__ == "__main__":
    unittest.main()
