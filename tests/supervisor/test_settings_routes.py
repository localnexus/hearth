"""Generated settings forms — /admin/settings.

The overview with strict-check verdicts, the schema contract served verbatim,
server-side redaction of secret fields, the preview-then-confirm single-key
set with comment-preserving line surgery, and the fifth unauthed shell.

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
from hearth.config import settings_registry as sr
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


class SettingsRoutes(AioHTTPTestCase):
    """/admin/settings: the generated settings surface (schema-driven step 2)
    against a fully synthetic install — scratch DATA *and* engine roots, so
    discovery never sweeps the real trees and no live file content can reach
    a payload assertion."""

    BEARER = {"Authorization": "Bearer test-bearer"}
    MEMORY_TOML = (
        "# operator header comment that must survive\n"
        "[memory]\n"
        "enabled = true\n"
        'backend = "floor"\n'
        "recall_limit = 6  # recalled items\n"
        "\n"
        "[memory.hindsight]\n"
        'llm_api_key = "zz-secret-value"\n'
        "\n"
        "[memory.hindsight.env]\n"
        'HS_FLAG = "zz-env-secret"\n'
        "\n"
        "[memory.companions]\n"
        "# a comment inside the table\n"
        'zz-a = "floor"\n'
    )

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
        base = Path(self._tmp.name)
        self.data = base / "data"
        self.eng = base / "engine"
        (self.data / "config" / "models" / "zz-m").mkdir(parents=True)
        (self.eng / "config").mkdir(parents=True)
        (self.data / "config" / "memory.toml").write_text(
            self.MEMORY_TOML, encoding="utf-8")
        (self.data / "config" / "active.toml").write_text(
            'character = "zz-a"\nmodel = "zz-m"\nvoice = "zz-v"\n',
            encoding="utf-8")
        (self.data / "config" / "overrides.toml").write_text(
            "[tts]\ntemperature = 0.8\n", encoding="utf-8")
        (self.data / "config" / "models" / "zz-m" / "model.toml").write_text(
            'id = "zz-model"\ntemperature = 0.7\nreasoning_effort = "none"\n',
            encoding="utf-8")
        # A SHIPPED file with no data-root copy — the copy-on-write target.
        (self.eng / "config" / "vad.toml").write_text(
            "# shipped calibration\n[live]\nconfidence = 0.7\n",
            encoding="utf-8")
        cfg = self.data / "config"
        for name, value in (("_DATA", self.data), ("DATA_DIR", self.data),
                            ("_ROOT", self.eng), ("CONFIG_DIR", cfg),
                            ("ACTIVE_TOML", cfg / "active.toml"),
                            ("MEMORY_TOML", cfg / "memory.toml"),
                            ("SERVE_TOML", cfg / "serve.toml"),
                            ("OPENCLAW_TOML", cfg / "openclaw.toml")):
            p = mock.patch.object(config_loader, name, value)
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    async def _set(self, file, key, value, yes=False):
        body = {"file": file, "key": key, "value": value}
        if yes:
            body["yes"] = True
        return await self.client.post("/admin/settings/set", json=body,
                                      headers=self.BEARER)

    async def test_shell_unauthed_and_routes_authed(self):
        resp = await self.client.get("/admin/settings/ui")  # no bearer
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertNotIn("test-bearer", text)
        self.assertNotIn("zz-", text)  # pure chrome — no install facts baked in
        for path in ("/admin/settings", "/admin/settings/schema",
                     "/admin/settings/file?file=x"):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 401, path)
        resp = await self.client.post("/admin/settings/set", json={})
        self.assertEqual(resp.status, 401)

    async def test_overview_and_schema(self):
        resp = await self.client.get("/admin/settings", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        kinds = {k["kind"]: k for k in (await resp.json())["kinds"]}
        self.assertEqual(kinds.keys(), sr.REGISTRY.keys())  # total coverage
        mem = kinds["memory"]
        self.assertTrue(mem["writable"])
        self.assertEqual(mem["files"][0]["file"], "DATA/config/memory.toml")
        self.assertIn(mem["files"][0]["verdict"], ("ok", "warn"))
        self.assertFalse(kinds["active"]["writable"])
        self.assertIn("/admin/switch", kinds["active"]["pointer"])
        self.assertIn("65000", kinds["overrides"]["pointer"])
        self.assertEqual(kinds["serve"]["files"], [])  # absent → no instances

        resp = await self.client.get("/admin/settings/schema", headers=self.BEARER)
        schema = (await resp.json())["schema"]
        props = schema["memory"]["schema"]["$defs"]["_MemHindsight"]["properties"]
        self.assertTrue(props["llm_api_key"]["x-hearth"]["secret"])
        self.assertEqual(
            schema["memory"]["schema"]["properties"]["enabled"]["x-hearth"]["effect"],
            "bot+facade")

    async def test_file_values_redact_secrets(self):
        resp = await self.client.get(
            "/admin/settings/file?file=DATA/config/memory.toml",
            headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        raw = await resp.text()
        self.assertNotIn("zz-secret-value", raw)   # the whole payload is clean
        self.assertNotIn("zz-env-secret", raw)
        d = json.loads(raw)
        self.assertTrue(d["values"]["enabled"])
        self.assertEqual(d["values"]["hindsight"]["llm_api_key"], "•••")
        self.assertEqual(d["values"]["hindsight"]["env"],
                         {"HS_FLAG": "•••"})  # keys stay visible
        resp = await self.client.get("/admin/settings/file?file=nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)

    async def test_set_preview_then_confirm_preserves_comments(self):
        import tomllib

        path = self.data / "config" / "memory.toml"
        label = "DATA/config/memory.toml"
        before = path.read_text(encoding="utf-8")
        resp = await self._set(label, "recall_limit", 4)
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertFalse(d["written"])
        self.assertEqual((d["old"], d["new"]), (6, 4))
        self.assertEqual(d["effect"]["effect"], "bot+facade")
        self.assertEqual(before, path.read_text(encoding="utf-8"))  # preview wrote nothing

        resp = await self._set(label, "recall_limit", 4, yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertTrue(d["written"])
        text = path.read_text(encoding="utf-8")
        self.assertIn("# operator header comment that must survive", text)
        self.assertIn("# recalled items", text)      # the trailing comment too
        parsed = tomllib.loads(text)["memory"]
        self.assertEqual(parsed["recall_limit"], 4)
        self.assertEqual(parsed["backend"], "floor")  # neighbors untouched
        prev = path.with_name(path.name + ".prev")
        self.assertEqual(prev.read_text(encoding="utf-8"), before)

    async def test_set_new_key_and_map_entry(self):
        import tomllib

        label = "DATA/config/memory.toml"
        # A key whose sub-table has no section header yet: appended fresh.
        resp = await self._set(label, "per_turn.enabled", True, yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        # A new entry in an existing map table: inserted under its header.
        resp = await self._set(label, "companions.zz-new", "hindsight", yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        text = (self.data / "config" / "memory.toml").read_text(encoding="utf-8")
        self.assertIn("# a comment inside the table", text)
        parsed = tomllib.loads(text)["memory"]
        self.assertTrue(parsed["per_turn"]["enabled"])
        self.assertEqual(parsed["companions"],
                         {"zz-a": "floor", "zz-new": "hindsight"})

    async def test_set_refusals_are_honest_pointers(self):
        label = "DATA/config/memory.toml"
        resp = await self._set("DATA/config/active.toml", "character", "zz-b")
        self.assertEqual(resp.status, 409)
        self.assertIn("/admin/switch", (await resp.json())["error"])
        resp = await self._set("DATA/config/overrides.toml", "tts.temperature", 0.9)
        self.assertEqual(resp.status, 409)
        self.assertIn("panel", (await resp.json())["error"])
        resp = await self._set(label, "hindsight.llm_api_key", "x")
        self.assertEqual(resp.status, 409)
        self.assertIn("secret", (await resp.json())["error"])
        resp = await self._set(label, "hindsight", "x")     # a table, not a key
        self.assertEqual(resp.status, 400)
        resp = await self._set(label, "companions", "x")    # a map needs an entry
        self.assertEqual(resp.status, 400)
        resp = await self._set(label, "no_such_key", "x")
        self.assertEqual(resp.status, 404)
        resp = await self._set(label, "recall_limit", "hi")  # type refused
        self.assertEqual(resp.status, 422)
        resp = await self._set("DATA/config/models/zz-m/model.toml",
                               "temperature", 9.9)           # range refused
        self.assertEqual(resp.status, 422)
        self.assertTrue((await resp.json())["detail"])
        resp = await self.client.post("/admin/settings/set",
                                      json={"file": label, "key": "enabled"},
                                      headers=self.BEARER)
        self.assertEqual(resp.status, 400)                   # value required

    async def test_shipped_file_copies_on_write_into_data(self):
        import tomllib

        shipped = self.eng / "config" / "vad.toml"
        before = shipped.read_text(encoding="utf-8")
        resp = await self._set("ROOT/config/vad.toml", "live.confidence", 0.5,
                               yes=True)
        self.assertEqual(resp.status, 200, await resp.text())
        d = await resp.json()
        self.assertIn("data root", d["target"])
        self.assertEqual(before, shipped.read_text(encoding="utf-8"))  # untouched
        copy = self.data / "config" / "vad.toml"
        self.assertIn("# shipped calibration", copy.read_text(encoding="utf-8"))
        self.assertEqual(
            tomllib.loads(copy.read_text(encoding="utf-8"))["live"]["confidence"],
            0.5)
        # The shipped label now answers with a pointer at the shadowing copy.
        resp = await self._set("ROOT/config/vad.toml", "live.confidence", 0.6,
                               yes=True)
        self.assertEqual(resp.status, 409)
        self.assertIn("shadows", (await resp.json())["error"])

if __name__ == "__main__":
    unittest.main()
