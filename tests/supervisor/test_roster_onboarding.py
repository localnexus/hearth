"""Roster wizard — onboarding a new companion.

The unauthed shell, the roster listing, the full preview-then-create
transaction, the validation matrix, honest refusals for a garbage or short
clip, and onboarding with no memory.toml present.

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


class RosterOnboarding(AioHTTPTestCase):
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

    async def test_shell_is_unauthed_static_chrome(self):
        resp = await self.client.get("/admin/roster")  # no bearer
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("roster", text.lower())
        self.assertNotIn("test-bearer", text)
        # data + verbs stay behind the door
        self.assertEqual((await self.client.get("/admin/roster/state")).status, 401)
        self.assertEqual((await self.client.post("/admin/roster/onboard")).status, 401)

    async def test_state_lists_roster(self):
        resp = await self.client.get("/admin/roster/state", headers=self.BEARER)
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        names = [c["name"] for c in data["characters"]]
        self.assertIn("example", names)  # the shipped root is enumerated too
        self.assertTrue(data["memory_enabled"])
        ex = next(c for c in data["characters"] if c["name"] == "example")
        self.assertEqual(ex["memory_backend"], "floor")

    async def test_preview_then_create_full_transaction(self):
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(memory_tier="hindsight"))
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["created"])
        self.assertEqual(data["clip"]["channels"], 1)
        self.assertFalse((self.root / "characters" / "zz-roster-test").exists())

        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(memory_tier="hindsight",
                                                      yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["created"])
        self.assertIn("verified", data["loader"])
        self.assertIn("/admin/launch", data["next"])
        cdir = self.root / "characters" / "zz-roster-test"
        for rel in ("persona.md", "VOICE-SOURCE.md",
                    "voices/default/voice.toml", "voices/default/sample.wav"):
            self.assertTrue((cdir / rel).is_file(), rel)
        # The generated descriptor passes the registry's strict check.
        import tomllib

        from hearth.config import settings_registry as reg
        parsed = tomllib.loads((cdir / "voices/default/voice.toml").read_text())
        errors, _warnings = reg.strict_check("voice", parsed)
        self.assertEqual(errors, [])
        self.assertEqual(parsed["license"], "personal-use-only")
        # Tier entry landed under [memory.companions], comments preserved.
        mem_text = (self.root / "config" / "memory.toml").read_text()
        self.assertIn('zz-roster-test = "hindsight"', mem_text)
        self.assertIn("operator comment that must survive", mem_text)
        self.assertIn("a comment inside the table", mem_text)
        self.assertIn("next bot/facade start", data["memory"])
        # Create-only: the same name is now refused.
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(yes="true"))
        self.assertEqual(resp.status, 400)
        self.assertIn("create-only", json.dumps(await resp.json()))

    async def test_validation_matrix(self):
        cases = (
            ({"name": "bad/name"}, "invalid character name"),
            ({"name": "example"}, "create-only"),        # shipped root protected
            ({"source": ""}, "source attestation"),
            ({"persona": "## IDENTITY\n\nonly half\n"}, "SOUL"),
            ({"memory_tier": "bogus"}, "unknown memory tier"),
        )
        for over, want in cases:
            resp = await self.client.post("/admin/roster/onboard",
                                          headers=self.BEARER,
                                          data=self._form(**over))
            self.assertEqual(resp.status, 400, want)
            self.assertIn(want, json.dumps(await resp.json()))
        self.assertFalse((self.root / "characters").exists())  # nothing written

    async def test_garbage_clip_refused_honestly(self):
        self.wav.write_bytes(b"this is not audio at all")
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(yes="true"))
        self.assertEqual(resp.status, 422)
        # Rolled back: the new character dir is gone (the bare characters/
        # shell from scaffolding may remain — it carries nothing).
        self.assertFalse((self.root / "characters" / "zz-roster-test").exists())

    async def test_short_clip_refused(self):
        _test_wav(self.wav, seconds=1.0)
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form())
        self.assertEqual(resp.status, 422)
        self.assertIn("clone reference wants", json.dumps(await resp.json()))

    async def test_memory_toml_absent_still_onboards(self):
        (self.root / "config" / "memory.toml").unlink()
        resp = await self.client.post("/admin/roster/onboard", headers=self.BEARER,
                                      data=self._form(name="zz-roster-nomem",
                                                      memory_tier="floor",
                                                      yes="true"))
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(data["created"])
        self.assertIn("memory.toml absent", data["memory"])
        self.assertTrue((self.root / "characters" / "zz-roster-nomem"
                         / "persona.md").is_file())

    # ── the editing half: persona editor ─────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
