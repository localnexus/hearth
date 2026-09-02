"""test_switch.py — switch-companion, one action.

Proves, on real files and a real aiohttp app:
  1. HELPERS  — merge/validate/write against an on-disk fixture install:
                registry violations and missing pieces REFUSE (nothing
                written); the write is atomic, keeps unknown scalar keys,
                backs the previous file up as active.toml.prev, and refuses
                non-scalar extras rather than dropping them.
  2. CHOICES  — enumeration unions the data root and the shipped tree
                (characters keyed on persona.md, voices on voice.toml,
                models on model.toml, persona variants as siblings).
  3. ROUTES   — /admin/switch rides the bearer middleware; GET reports
                current + choices; POST validates BEFORE writing (400 leaves
                no file), schedules the supervised restart in the background
                (stop → start on the child, hold forwarded — the finalize/
                hold semantics live in the bot's own graceful path, proven in
                test_supervisor), refuses concurrent switches (409), and only
                restarts a bot that is running (or start:true).

The bot child here is a FAKE (calls recorded) — child process semantics are
test_supervisor.py's job; this file owns the switch orchestration contract.

  4. ROUTING — the registry-consulted live handoff: a switch whose
     every changed field has a live path goes to the bot's /switch/live (a
     fake panel server here); a refusal or unreachable panel falls back to
     the supervised restart; "apply" steers ("live" never restarts).
     The bot-side apply semantics live in test_live_switch.py.

Run:  .venv/bin/python -m unittest tests.test_switch
"""

from __future__ import annotations

import asyncio
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth import supervisor
from hearth.config import config_loader
from hearth.supervisor import switch as switch_mod


def _build_install(root: Path) -> None:
    """A minimal valid install: one character (two personas, one voice), one model."""
    char = root / "characters" / "testchar"
    (char / "voices" / "v1").mkdir(parents=True)
    (char / "persona.md").write_text("## IDENTITY\nx\n## SOUL\ny\n")
    (char / "persona.alt.md").write_text("## IDENTITY\nx\n## SOUL\nz\n")
    (char / "voices" / "v1" / "sample.wav").write_bytes(b"RIFFfake")
    (char / "voices" / "v1" / "voice.toml").write_text(
        'tag = "test-v1"\nref_wav = "sample.wav"\n')
    mdir = root / "config" / "models" / "m1"
    mdir.mkdir(parents=True)
    (mdir / "model.toml").write_text(
        'id = "test-model"\ntemperature = 0.7\nreasoning_effort = "none"\n')


GOOD = {"character": "testchar", "model": "m1", "voice": "v1", "persona": "default"}


class _FixtureBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_install(self.root)
        # _DATA is read at CALL time by _lookup/list_voices/choices — patchable.
        self._p = mock.patch.object(config_loader, "_DATA", self.root)
        self._p.start()
        self.addCleanup(self._p.stop)
        self.active = self.root / "config" / "active.toml"


class SelectionHelpers(_FixtureBase):
    def test_merge_partial_over_current(self):
        cur = {"character": "a", "model": "m", "voice": "v", "persona": "p", "zzz": 1}
        merged = switch_mod.merge_selection(cur, {"character": "b", "hold": True, "junk": "x"})
        self.assertEqual(merged, {"character": "b", "model": "m", "voice": "v", "persona": "p"})

    def test_merge_from_nothing_defaults_persona(self):
        merged = switch_mod.merge_selection(None, {"character": "c", "model": "m", "voice": "v"})
        self.assertEqual(merged["persona"], "default")

    def test_validate_good_selection(self):
        self.assertEqual(switch_mod.validate_selection(dict(GOOD)), [])

    def test_validate_persona_variant(self):
        self.assertEqual(switch_mod.validate_selection({**GOOD, "persona": "alt"}), [])
        errs = switch_mod.validate_selection({**GOOD, "persona": "missing"})
        self.assertTrue(any("persona" in e for e in errs), errs)

    def test_validate_missing_pieces(self):
        errs = switch_mod.validate_selection({**GOOD, "model": "nope"})
        self.assertTrue(any("nope" in e for e in errs), errs)
        errs = switch_mod.validate_selection({**GOOD, "voice": "nope"})
        self.assertTrue(errs, "missing voice must refuse")
        errs = switch_mod.validate_selection({"character": "ghost", "model": "m1",
                                              "voice": "v1", "persona": "default"})
        self.assertTrue(errs, "missing character must refuse")

    def test_validate_pattern_violation(self):
        errs = switch_mod.validate_selection({**GOOD, "character": "../evil"})
        self.assertTrue(errs, "path-ish names must fail the registry pattern")

    def test_validate_missing_required_key(self):
        errs = switch_mod.validate_selection({"character": "testchar", "persona": "default"})
        self.assertTrue(any("model" in e for e in errs), errs)
        self.assertTrue(any("voice" in e for e in errs), errs)

    def test_write_roundtrip_prev_and_extras(self):
        res = switch_mod.write_selection(dict(GOOD), path=self.active)
        self.assertIsNone(res["previous"])
        with open(self.active, "rb") as f:
            data = tomllib.load(f)
        self.assertEqual({k: data[k] for k in switch_mod.SELECTION_KEYS}, GOOD)
        # hand-add an extra scalar, switch again: extra survives, .prev holds v1
        self.active.write_text(self.active.read_text() + 'my_note = "keep me"\n')
        res = switch_mod.write_selection({**GOOD, "voice": "v1", "persona": "alt"},
                                         path=self.active)
        self.assertEqual(res["previous"]["persona"], "default")
        self.assertEqual(res["extras"], ["my_note"])
        with open(self.active, "rb") as f:
            data = tomllib.load(f)
        self.assertEqual(data["persona"], "alt")
        self.assertEqual(data["my_note"], "keep me")
        prev = self.active.parent / "active.toml.prev"
        self.assertTrue(prev.is_file())
        with open(prev, "rb") as f:
            self.assertEqual(tomllib.load(f)["persona"], "default")

    def test_write_refuses_non_scalar_extra(self):
        self.active.parent.mkdir(parents=True, exist_ok=True)
        self.active.write_text('character = "a"\nmodel = "m"\nvoice = "v"\n[weird]\nx = 1\n')
        with self.assertRaises(ValueError):
            switch_mod.write_selection(dict(GOOD), path=self.active)

    def test_read_selection_reports_malformed(self):
        self.active.parent.mkdir(parents=True, exist_ok=True)
        self.active.write_text("not = toml = at all\n")
        data, err = switch_mod.read_selection(self.active)
        self.assertIsNone(data)
        self.assertIn("unreadable", err)

    def test_choices_fixture_and_shipped_union(self):
        ch = switch_mod.choices()
        names = {c["name"] for c in ch["characters"]}
        self.assertIn("testchar", names)
        self.assertIn("example", names)  # shipped tree still offered (ROOT fallback)
        tc = next(c for c in ch["characters"] if c["name"] == "testchar")
        self.assertEqual(tc["voices"], ["v1"])
        self.assertEqual(tc["personas"], ["alt", "default"])
        self.assertIn("m1", ch["models"])


class _FakeChild:
    """Records calls; state machine just rich enough for the routes."""

    def __init__(self, state="running"):
        self.calls = []
        self.state = state
        self.pid = 4242 if state == "running" else None
        self.managed = state == "running"
        self.stop_gate: asyncio.Event | None = None

    def status(self):
        return {"state": self.state, "pid": self.pid, "managed": self.managed,
                "uptime_s": None, "last_exit": None}

    async def adopt(self):
        return self.state == "running"

    async def stop(self, hold=False, name=None):
        self.calls.append(("stop", hold, name))
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        self.state = "down"
        self.pid = None
        return {"ok": True, "escalated": False, "held": bool(hold)}

    async def start(self, mode="new", name=None, memory=None):
        # 3-tuple when no memory rider (keeps existing assertions), 4-tuple with.
        self.calls.append(("start", mode, name) if memory is None
                          else ("start", mode, name, memory))
        self.state = "running"
        self.pid = 4243
        result = {"ok": True, "pid": self.pid, "mode": mode}
        if memory is not None:
            result["memory"] = memory
        return result

    def close(self):
        pass


class _SwitchHarness(AioHTTPTestCase):
    BEARER = {"Authorization": "Bearer test-bearer"}

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            character="testchar",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none",
            session=None,
        )
        mount = supervisor.build_mount({"enabled": True, "panel_url": "http://127.0.0.1:1"})
        mount(app)

        async def _open(app_):
            app_["deps"].session = aiohttp.ClientSession()

        async def _close(app_):
            await app_["deps"].session.close()

        app.on_startup.append(_open)
        app.on_cleanup.append(_close)
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _build_install(self.root)
        self.active = self.root / "config" / "active.toml"
        self._patches = [
            mock.patch.object(config_loader, "_DATA", self.root),
            mock.patch.object(switch_mod, "active_path", lambda: self.active),
        ]
        for p in self._patches:
            p.start()
        self.app["bot_child"].close()
        self.child = _FakeChild()
        self.app["bot_child"] = self.child

    async def asyncTearDown(self):
        task = self.app["switch_state"]["task"]
        if task is not None and not task.done():
            task.cancel()
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()
        await super().asyncTearDown()


class SwitchRoutes(_SwitchHarness):
    async def test_bearer_required(self):
        for method, path in (("GET", "/admin/switch"), ("POST", "/admin/switch")):
            resp = await self.client.request(method, path)
            self.assertEqual(resp.status, 401, path)

    async def test_get_reports_current_and_choices(self):
        switch_mod.write_selection(dict(GOOD), path=self.active)
        resp = await self.client.get("/admin/switch", headers=self.BEARER)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["current"], GOOD)
        self.assertIn("testchar", [c["name"] for c in data["choices"]["characters"]])
        self.assertEqual(data["bot"]["state"], "running")
        self.assertFalse(data["facade"]["identity_pinned"])
        self.assertIsNone(data["switch"])

    async def test_get_with_no_active_file(self):
        resp = await self.client.get("/admin/switch", headers=self.BEARER)
        data = await resp.json()
        self.assertIsNone(data["current"])
        self.assertIsNone(data["current_error"])

    async def test_post_invalid_writes_nothing(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={**GOOD, "model": "nope"})
        self.assertEqual(resp.status, 400)
        self.assertFalse((await resp.json())["ok"])
        self.assertFalse(self.active.exists(), "a refused switch must write nothing")
        self.assertEqual(self.child.calls, [])

    async def test_post_valid_switch_restarts(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER, json=dict(GOOD))
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["wrote"], GOOD)
        self.assertIn("scheduled", data["restart"])
        self.assertIn("untouched", data["facade"])
        self.assertTrue(self.active.is_file())
        await self.app["switch_state"]["task"]
        self.assertEqual(self.child.calls,
                         [("stop", False, None), ("start", "new", None)])
        self.assertEqual(self.app["switch_state"]["last"]["phase"], "done")
        resp = await self.client.get("/admin/state", headers=self.BEARER)
        self.assertEqual((await resp.json())["switch"]["phase"], "done")

    async def test_post_hold_and_resume_forwarded(self):
        body = {**GOOD, "hold": True, "hold_name": "keepme",
                "mode": "resume", "name": "keepme"}
        resp = await self.client.post("/admin/switch", headers=self.BEARER, json=body)
        self.assertEqual(resp.status, 200)
        await self.app["switch_state"]["task"]
        self.assertEqual(self.child.calls,
                         [("stop", True, "keepme"), ("start", "resume", "keepme")])

    async def test_post_memory_rider_forces_restart_and_forwards(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={**GOOD, "memory": "recall-only"})
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual((await resp.json())["applied"], "restart")
        await self.app["switch_state"]["task"]
        self.assertEqual(self.child.calls,
                         [("stop", False, None), ("start", "new", None, "recall-only")])

    async def test_post_bad_memory_writes_nothing(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={**GOOD, "memory": "bogus"})
        self.assertEqual(resp.status, 400)
        self.assertFalse(self.active.exists(), "a refused rider must write nothing")
        self.assertEqual(self.child.calls, [])

    async def test_post_apply_live_with_memory_refused(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={**GOOD, "apply": "live", "memory": "off"})
        self.assertEqual(resp.status, 409)
        self.assertIn("restart", (await resp.json())["errors"][0])
        self.assertEqual(self.child.calls, [], "no stop/start on a refused live+memory")

    async def test_post_bot_down_writes_without_restart(self):
        self.child.state = "down"
        self.child.managed = False
        resp = await self.client.post("/admin/switch", headers=self.BEARER, json=dict(GOOD))
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("not scheduled", data["restart"])
        self.assertTrue(self.active.is_file())
        self.assertEqual(self.child.calls, [])
        # start:true launches even from down
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={**GOOD, "start": True})
        self.assertTrue((await resp.json())["ok"])
        await self.app["switch_state"]["task"]
        self.assertEqual(self.child.calls, [("stop", False, None), ("start", "new", None)])

    async def test_concurrent_switch_refused(self):
        self.child.stop_gate = asyncio.Event()
        resp = await self.client.post("/admin/switch", headers=self.BEARER, json=dict(GOOD))
        self.assertEqual(resp.status, 200)
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={**GOOD, "persona": "alt"})
        self.assertEqual(resp.status, 409)
        self.assertIn("in progress", (await resp.json())["error"])
        self.child.stop_gate.set()
        await self.app["switch_state"]["task"]
        self.assertEqual(self.app["switch_state"]["last"]["phase"], "done")

    async def test_post_malformed_current_refuses(self):
        self.active.parent.mkdir(parents=True, exist_ok=True)
        self.active.write_text("not = toml = at all\n")
        resp = await self.client.post("/admin/switch", headers=self.BEARER, json=dict(GOOD))
        self.assertEqual(resp.status, 409)
        self.assertEqual(self.child.calls, [])

    async def test_offline_page_mentions_switch(self):
        self.child.state = "down"
        resp = await self.client.get("/", headers=self.BEARER)
        self.assertIn("/admin/switch", await resp.text())


class _FakePanel:
    """A loopback stand-in for the bot's /switch/live intent slot."""

    def __init__(self):
        self.requests: list = []
        self.response: tuple = ({"ok": True, "armed": True,
                                 "changed": ["persona"],
                                 "applies": "at the next turn boundary"}, 200)
        self.runner = None
        self.port = None

    async def start(self):
        app = web.Application()

        async def arm(req: web.Request) -> web.Response:
            self.requests.append(await req.json())
            body, status = self.response
            return web.json_response(body, status=status)

        app.router.add_post("/switch/live", arm)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = self.runner.addresses[0][1]

    async def stop(self):
        if self.runner is not None:
            await self.runner.cleanup()


class SwitchRouting(_SwitchHarness):
    """Stroke 3: live-vs-restart routing on the same harness + a fake panel."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.panel = _FakePanel()
        await self.panel.start()
        self.app["panel_url"] = f"http://127.0.0.1:{self.panel.port}"
        # a previous selection on disk → only the delta counts as changed
        switch_mod.write_selection(dict(GOOD), path=self.active)

    async def asyncTearDown(self):
        await self.panel.stop()
        await super().asyncTearDown()

    async def test_live_handoff_taken(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"persona": "alt"})
        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual(data["applied"], "live")
        self.assertIn("untouched", data["facade"])
        self.assertEqual(self.child.calls, [], "live path must not restart")
        self.assertEqual(len(self.panel.requests), 1)
        self.assertEqual(self.panel.requests[0]["persona"], "alt")
        self.assertEqual(self.panel.requests[0]["character"], "testchar")
        self.assertEqual(self.app["switch_state"]["last"]["phase"], "live")
        with open(self.active, "rb") as f:
            self.assertEqual(tomllib.load(f)["persona"], "alt")

    async def test_live_refusal_falls_back_to_restart(self):
        self.panel.response = ({"ok": False,
                                "errors": ["model 'x' is not resident"]}, 409)
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"persona": "alt"})
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["applied"], "restart")
        self.assertIn("not resident", " ".join(data["live_refused"]))
        await self.app["switch_state"]["task"]
        self.assertEqual(self.child.calls,
                         [("stop", False, None), ("start", "new", None)])

    async def test_apply_restart_skips_live(self):
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"persona": "alt", "apply": "restart"})
        data = await resp.json()
        self.assertEqual(data["applied"], "restart")
        self.assertEqual(self.panel.requests, [], "apply=restart must not touch the bot slot")
        await self.app["switch_state"]["task"]

    async def test_apply_live_never_restarts(self):
        self.panel.response = ({"ok": False, "errors": ["busy"]}, 409)
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"persona": "alt", "apply": "live"})
        self.assertEqual(resp.status, 409)
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertIn("hint", data)
        self.assertEqual(self.child.calls, [])
        self.assertEqual(self.app["switch_state"]["last"]["phase"], "live-refused")

    async def test_apply_live_bot_down_409(self):
        self.child.state = "down"
        self.child.managed = False
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"persona": "alt", "apply": "live"})
        self.assertEqual(resp.status, 409)
        self.assertEqual(self.panel.requests, [])

    async def test_hold_and_mode_forwarded_on_live(self):
        resp = await self.client.post(
            "/admin/switch", headers=self.BEARER,
            json={"persona": "alt", "hold": True, "hold_name": "keep",
                  "mode": "resume", "name": "keep"})
        self.assertEqual((await resp.json())["applied"], "live")
        req = self.panel.requests[0]
        self.assertTrue(req["hold"])
        self.assertEqual(req["hold_name"], "keep")
        self.assertEqual(req["mode"], "resume")
        self.assertEqual(req["name"], "keep")

    async def test_unreachable_panel_falls_back(self):
        self.app["panel_url"] = "http://127.0.0.1:1"
        resp = await self.client.post("/admin/switch", headers=self.BEARER,
                                      json={"persona": "alt"})
        data = await resp.json()
        self.assertEqual(data["applied"], "restart")
        self.assertTrue(data["live_refused"])
        await self.app["switch_state"]["task"]

    async def test_choices_carry_model_ids(self):
        resp = await self.client.get("/admin/switch", headers=self.BEARER)
        data = await resp.json()
        self.assertEqual(data["choices"]["model_ids"].get("m1"), "test-model")


if __name__ == "__main__":
    unittest.main()
