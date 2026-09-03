"""Roster wizard — adding a voice to an existing character.

Validation, preview-then-create with provenance appended, landing a shipped
character's new voice in DATA, and the rollback that removes only the voice
directory when the clip is garbage.

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


class RosterAddVoice(AioHTTPTestCase):
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

    async def _create(self, name="zz-roster-test"):
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(name=name, yes="true"))
        self.assertEqual(resp.status, 200, await resp.text())

    def _voice_form(self, **over):
        fields = {"character": "zz-roster-test", "voice_tag": "second",
                  "license": "personal-use-only",
                  "source": "recorded by me for the test"}
        fields.update(over)
        fd = aiohttp.FormData()
        for k, v in fields.items():
            if v is not None:
                fd.add_field(k, v)
        if over.get("_sample", True):
            fd.add_field("sample", self.wav.read_bytes(),
                         filename="clip.wav", content_type="audio/wav")
        return fd

    async def test_add_voice_validation(self):
        self.assertEqual((await self.client.post("/admin/roster/voice")).status, 401)
        await self._create()
        cases = (
            ({"character": "zz-nope"}, "unknown character"),
            ({"voice_tag": "bad/tag"}, "invalid voice tag"),
            ({"voice_tag": "default"}, "already exists"),  # per-tag create-only
            ({"source": ""}, "source attestation"),
        )
        for over, want in cases:
            resp = await self.client.post("/admin/roster/voice",
                                          headers=self.BEARER,
                                          data=self._voice_form(**over))
            self.assertEqual(resp.status, 400, want)
            self.assertIn(want, json.dumps(await resp.json()))

    async def test_add_voice_preview_then_create_appends_provenance(self):
        await self._create()
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form())
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertFalse(data["created"])
        vdir = (self.root / "characters" / "zz-roster-test" / "voices" / "second")
        self.assertFalse(vdir.exists())  # preview persisted nothing
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form(yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["created"])
        self.assertIn("verified", data["loader"])
        self.assertIn("switch pickers", data["next"])
        self.assertTrue((vdir / "voice.toml").is_file())
        self.assertTrue((vdir / "sample.wav").is_file())
        # Provenance appended to the character's ONE record, both tags present.
        vs = (self.root / "characters" / "zz-roster-test" / "VOICE-SOURCE.md")
        text = vs.read_text()
        self.assertIn("default", text)
        self.assertIn("## second — added", text)
        # And the enumeration sees the new tag immediately.
        resp = await self.client.get("/admin/roster/state", headers=self.BEARER)
        entry = next(c for c in (await resp.json())["characters"]
                     if c["name"] == "zz-roster-test")
        self.assertEqual(sorted(entry["voices"]), ["default", "second"])
        # Create-only per tag: a repeat is refused with the bundle intact.
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form(yes="true"))
        self.assertEqual(resp.status, 400)
        self.assertTrue((vdir / "voice.toml").is_file())

    async def test_add_voice_to_shipped_character_lands_in_data(self):
        # example's persona lives in the shipped root; the new bundle must land
        # under DATA (voice_dir's per-voice lookup), shipped tree untouched.
        resp = await self.client.post(
            "/admin/roster/voice", headers=self.BEARER,
            data=self._voice_form(character="example", voice_tag="zz-extra",
                                  yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        vdir = self.root / "characters" / "example" / "voices" / "zz-extra"
        self.assertTrue((vdir / "voice.toml").is_file())
        # A DATA-side provenance record was started for the new clip.
        self.assertTrue((self.root / "characters" / "example"
                         / "VOICE-SOURCE.md").is_file())
        from hearth.config import config_loader
        self.assertFalse((config_loader._ROOT / "characters" / "example"
                          / "voices" / "zz-extra").exists())

    async def test_add_voice_garbage_clip_rolls_back_voice_dir_only(self):
        await self._create()
        self.wav.write_bytes(b"this is not audio at all")
        resp = await self.client.post("/admin/roster/voice", headers=self.BEARER,
                                      data=self._voice_form(yes="true"))
        self.assertEqual(resp.status, 422)
        cdir = self.root / "characters" / "zz-roster-test"
        self.assertFalse((cdir / "voices" / "second").exists())  # rolled back
        self.assertTrue((cdir / "persona.md").is_file())  # character untouched
        self.assertTrue((cdir / "voices" / "default" / "voice.toml").is_file())

if __name__ == "__main__":
    unittest.main()
