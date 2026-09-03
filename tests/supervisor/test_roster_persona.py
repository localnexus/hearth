"""Roster wizard — the persona editor.

Auth and validation, editing a DATA character with a backup, copy-on-write for
a shipped character, and creating a new persona variant that then lists.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import aiohttp
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


def _test_wav(path: Path, seconds: float = 4.0, rate: int = 24_000) -> None:
    import wave as _wave

    with _wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


class RosterPersonaEditor(AioHTTPTestCase):
    """/admin/roster: exempt static shell, authed state, and the create-only
    preview-then-confirm onboarding transaction against a scratch DATA tree."""

    BEARER = {"Authorization": "Bearer test-bearer"}
    PERSONA = "## IDENTITY\n\nA test companion.\n\n## SOUL\n\nCalm and brief.\n"

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
        (self.root / "config").mkdir()
        (self.root / "config" / "memory.toml").write_text(
            "# operator comment that must survive\n"
            "[memory]\nenabled = true\nbackend = \"floor\"\n\n"
            "[memory.companions]\n# a comment inside the table\n",
            encoding="utf-8")
        self._patch = mock.patch.object(config_loader, "_DATA", self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # MEMORY_TOML is import-bound (unlike the call-time _DATA lookups), so
        # point it at the scratch copy too — every consumer reads one path.
        self._mem_patch = mock.patch.object(
            config_loader, "MEMORY_TOML", self.root / "config" / "memory.toml")
        self._mem_patch.start()
        self.addCleanup(self._mem_patch.stop)
        self.wav = self.root / "clip.wav"
        _test_wav(self.wav)

    async def asyncTearDown(self):
        await self.app["bot_child"].stop()
        self.app["bot_child"].close()
        await super().asyncTearDown()

    def _form(self, **over):
        fields = {"name": "zz-roster-test", "persona": self.PERSONA,
                  "voice_tag": "default", "license": "personal-use-only",
                  "source": "recorded by me for the test", "memory_tier": ""}
        fields.update(over)
        fd = aiohttp.FormData()
        for k, v in fields.items():
            if v is not None:
                fd.add_field(k, v)
        if over.get("_sample", True):
            fd.add_field("sample", self.wav.read_bytes(),
                         filename="clip.wav", content_type="audio/wav")
        fields.pop("_sample", None)
        return fd

    NEW_PERSONA = ("## IDENTITY\n\nAn edited test companion.\n\n"
                   "## SOUL\n\nBrighter now.\n")

    async def _create(self, name="zz-roster-test"):
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(name=name, yes="true"))
        self.assertEqual(resp.status, 200, await resp.text())

    async def test_persona_editor_auth_and_validation(self):
        self.assertEqual((await self.client.get(
            "/admin/roster/persona?character=x")).status, 401)
        self.assertEqual((await self.client.post(
            "/admin/roster/persona", json={})).status, 401)
        resp = await self.client.get("/admin/roster/persona?character=zz-nope",
                                     headers=self.BEARER)
        self.assertEqual(resp.status, 404)
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "example", "text": "## IDENTITY\n\nhalf only\n"})
        self.assertEqual(resp.status, 400)
        self.assertIn("SOUL", json.dumps(await resp.json()))
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "example", "persona": "../evil",
                  "text": self.NEW_PERSONA})
        self.assertEqual(resp.status, 400)

    async def test_persona_edit_data_character_with_backup(self):
        await self._create()
        target = self.root / "characters" / "zz-roster-test" / "persona.md"
        # Read shows the created text; preview writes nothing.
        resp = await self.client.get(
            "/admin/roster/persona?character=zz-roster-test", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(data["text"], self.PERSONA)
        self.assertEqual(data["root"], "data")
        body = {"character": "zz-roster-test", "text": self.NEW_PERSONA}
        resp = await self.client.post("/admin/roster/persona",
                                      headers=self.BEARER, json=body)
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertFalse(data["written"])
        self.assertEqual(target.read_text(), self.PERSONA)  # untouched
        # Confirm: written atomically, one .prev backup, effect time stated.
        resp = await self.client.post("/admin/roster/persona", headers=self.BEARER,
                                      json={**body, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["written"])
        self.assertEqual(target.read_text(), self.NEW_PERSONA)
        prev = target.with_name("persona.md.prev")
        self.assertEqual(prev.read_text(), self.PERSONA)
        self.assertIn("characters/zz-roster-test/persona.md.prev", data["backup"])
        self.assertIn("live-switch", data["effect"])

    async def test_persona_shipped_character_copies_on_write(self):
        # "example" resolves to the shipped root; editing must create a DATA
        # overlay and NEVER touch the shipped file.
        from hearth.config import config_loader

        shipped = config_loader._ROOT / "characters" / "example" / "persona.md"
        shipped_text = shipped.read_text()
        resp = await self.client.get(
            "/admin/roster/persona?character=example", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual(data["root"], "shipped")
        self.assertIn("DATA", data["editable_note"] or "")
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "example", "text": self.NEW_PERSONA, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["written"])
        self.assertIsNone(data["backup"])  # fresh overlay — nothing to back up
        overlay = self.root / "characters" / "example" / "persona.md"
        self.assertEqual(overlay.read_text(), self.NEW_PERSONA)
        self.assertEqual(shipped.read_text(), shipped_text)  # sacred
        # The overlay now shadows: a fresh GET reads it back as root=data.
        resp = await self.client.get(
            "/admin/roster/persona?character=example", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual((data["root"], data["text"]), ("data", self.NEW_PERSONA))

    async def test_persona_new_variant_created_and_listed(self):
        await self._create()
        resp = await self.client.post(
            "/admin/roster/persona", headers=self.BEARER,
            json={"character": "zz-roster-test", "persona": "bright",
                  "text": self.NEW_PERSONA, "yes": True})
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertIn("variant", data["action"])
        vfile = (self.root / "characters" / "zz-roster-test"
                 / "persona.bright.md")
        self.assertEqual(vfile.read_text(), self.NEW_PERSONA)
        # The switch enumeration picks the variant up at call time.
        resp = await self.client.get("/admin/roster/state", headers=self.BEARER)
        entry = next(c for c in (await resp.json())["characters"]
                     if c["name"] == "zz-roster-test")
        self.assertIn("bright", entry["personas"])

    # ── the editing half: add-a-voice ────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
