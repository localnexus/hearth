"""test_supervisor_models.py — /admin/models: the weights surface behind the door.

The routes are the web half of `python -m hearth.weights`, so they inherit its
habits and are pinned for them here: nothing mutates without a second call, a
file that went missing is a reported STATE rather than an exception, and the
launchd unit is only ever written through the injectable helper. Every case
runs against a scratch data folder and a scratch `HEARTH_LAUNCH_AGENTS` — no
test touches this machine's real data root, its real weights, its real
`~/Library/LaunchAgents`, or the door that may be running on it.

Pins:

  1.  every route is behind the bearer, including the reading ones;
  2.  the list reports state / fit / unit / resident for an enrolled model, and
      the enrollable targets beside it;
  3.  R3 — with the weights file deleted, the row says `missing` and carries
      the plain sentence `check` wrote; the route still answers 200, and the
      card's Apply is the thing that goes away, not the row;
  4.  the scan lists what is on disk, marks a candidate already enrolled, and
      answers from cache until `?refresh=1`;
  5.  enroll / unenroll / apply preview first and write NOTHING; the confirming
      call with `"yes": true` writes, and only then;
  6.  enroll into a `"new"` directory copies the shipped example first, and
      refuses a name that is not a safe path segment;
  7.  render writes into DATA/render/ and never into LaunchAgents; apply writes
      the unit, archives what was there, and hands back the two launchctl lines
      without running anything;
  8.  a companion guard that says BLOCKED is a 409 that wrote nothing; one that
      is merely uncertain is a 200 carrying the warning;
  9.  the door's `api_key_file` PATH appears in NO response body, on any route —
      asserted against the raw JSON text, not the parsed fields;
  10. the two built-in actuators exist exactly when `[weights.door]` does, carry
      `guard = "companion"`, and never overwrite an operator's own declaration.

Run:  .venv/bin/python -m unittest tests.test_supervisor_models
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from hearth import supervisor
from hearth.config import config_loader as cl
from hearth.supervisor import models as models_mod
# The G1 shadowing trap: the weights package re-exports `enroll` as a FUNCTION,
# so the submodule of that name is only reachable by module path.
enroll_mod = import_module("hearth.weights.enroll")
render_mod = import_module("hearth.weights.render")
roots_mod = import_module("hearth.weights.roots")
scan_mod = import_module("hearth.weights.scan")

from tests.test_weights_render import live_plist
from tests.test_weights_scan import tiny_gguf

#: A door binary that is not on this machine — named, never run. The renderer
#: only writes the name into the unit, and the budget falls back to sysctl.
DOOR_BINARY = "/nonexistent/zz-test/llama-server"

#: Deliberately not 8080: a test must not so much as probe a real door.
DOOR_PORT = 65099

#: The one string that must never reach a response body.
KEY_FILE_NAME = "zz-secret-key-file"

MODEL_TOML = """\
# config/models/m1/model.toml — a fixture, mostly comments, like a real one.

id = "zz-test-model"
temperature = 0.7
reasoning_effort = "none"

[server]
alias = "zz-test-model"
ctx-size = 32768
"""

WEIGHTS_TOML = """\
[weights]
roots = ["{models}"]
product_dirs = false
llama_server = "{binary}"

[weights.door]
label = "zz.test.door"
host = "127.0.0.1"
port = {port}
api_key_file = "{key_file}"
threads = 8
load_mode = "mlock"
log_file = "{log_file}"
webui = false
"""


class _Surface(AioHTTPTestCase):
    """One scratch data folder, one scratch LaunchAgents dir, one mounted app."""

    BEARER = {"Authorization": "Bearer test-bearer"}
    MODEL = "m1"

    #: Subclasses that want no door table set this False.
    DOOR = True

    async def get_application(self) -> web.Application:
        from hearth.serve import app as serve_app

        app = web.Application(middlewares=[serve_app._auth])
        app["deps"] = SimpleNamespace(
            bearer="test-bearer",
            cfg={"audio_base_url": "http://127.0.0.1:1/v1"},
            lm_base_url="http://127.0.0.1:1/v1",
            lm_token="none",
            session=None,        # no probe session → residency is honest null
            memory=None,
        )
        mount = supervisor.build_mount({"enabled": True,
                                        "panel_url": "http://127.0.0.1:1",
                                        "compact_watch": False})
        mount(app)
        return app

    def _tree(self) -> None:
        """The scratch install: one model directory, one GGUF, one weights.toml."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"
        self.model_dir = self.data / "config" / "models" / self.MODEL
        self.model_dir.mkdir(parents=True)
        (self.model_dir / "model.toml").write_text(MODEL_TOML, encoding="utf-8")
        (self.data / "logs").mkdir(parents=True)
        self.weights = tiny_gguf(self.tmp / "models" / "pub" / "repo" / "m.gguf")
        self.key_file = self.data / "config" / KEY_FILE_NAME
        if self.DOOR:
            (self.data / "config" / "weights.toml").write_text(
                WEIGHTS_TOML.format(models=self.tmp / "models", binary=DOOR_BINARY,
                                    port=DOOR_PORT, key_file=self.key_file,
                                    log_file=self.data / "logs" / "door.log"),
                encoding="utf-8")
        self.agents = self.tmp / "LaunchAgents"
        self.agents.mkdir()

    def _anchor(self) -> None:
        """config_loader resolves its anchors once at import, so every one of
        them is redirected here — and `HEARTH_LAUNCH_AGENTS` keeps the unit
        writer away from the real one."""
        for attr, value in (("_DATA", self.data), ("DATA_DIR", self.data),
                            ("CONFIG_DIR", self.data / "config"),
                            ("MODELS_DIR", self.data / "config" / "models")):
            patch = mock.patch.object(cl, attr, value)
            patch.start()
            self.addCleanup(patch.stop)
        for module, attr, value in (
                (roots_mod, "WEIGHTS_TOML", self.data / "config" / "weights.toml"),
                (scan_mod, "HEADER_CACHE_PATH",
                 self.data / ".cache" / "weights-headers.json")):
            patch = mock.patch.object(module, attr, value)
            patch.start()
            self.addCleanup(patch.stop)
        env = mock.patch.dict(os.environ, {"HEARTH_LAUNCH_AGENTS": str(self.agents)})
        env.start()
        self.addCleanup(env.stop)
        # The scan memoizes headers per identity across a process; a scratch
        # tree must never inherit another case's answers.
        scan_mod._MEMO.clear()
        scan_mod._DISK = None

    async def asyncSetUp(self):
        # Anchor BEFORE the app is built: the mount reads config/weights.toml to
        # derive the two built-in door actuators, and it must read the scratch
        # one — never this machine's.
        self._tree()
        self._anchor()
        await super().asyncSetUp()
        self.app["bot_child"].close()

    def enroll_fixture(self) -> None:
        """Bind the fixture GGUF to the fixture model, without a route."""
        root = roots_mod.Root("models", self.tmp / "models", "user")
        cands = scan_mod.scan_dir(self.weights.parent, root)
        enroll_mod.enroll(self.MODEL, cands[0])

    async def get(self, path: str):
        r = await self.client.get(path, headers=self.BEARER)
        return r.status, await r.json(), await r.text()

    async def post(self, path: str, body: dict):
        r = await self.client.post(path, json=body, headers=self.BEARER)
        return r.status, await r.json(), await r.text()


# ── 1. the door ──────────────────────────────────────────────────────────────

class BehindTheBearer(_Surface):

    async def test_every_route_is_authed(self):
        for path in ("/admin/models", "/admin/models/scan", "/admin/models/door"):
            self.assertEqual((await self.client.get(path)).status, 401, path)
        for path in ("/admin/models/enroll", "/admin/models/unenroll",
                     "/admin/models/render", "/admin/models/apply"):
            self.assertEqual((await self.client.post(path, json={})).status, 401, path)


# ── 2 & 3. the list, and R3 ──────────────────────────────────────────────────

class TheList(_Surface):

    async def test_an_enrolled_model_reports_state_fit_and_unit(self):
        self.enroll_fixture()
        status, body, _ = await self.get("/admin/models")
        self.assertEqual(status, 200)
        self.assertEqual([m["name"] for m in body["models"]], [self.MODEL])
        row = body["models"][0]
        self.assertEqual(row["state"], "present")
        self.assertEqual(row["id"], "zz-test-model")
        self.assertIn(row["fit"], ("fits", "too large", "unknown"))
        # No unit on this scratch machine yet.
        self.assertEqual(row["unit"], "unapplied")
        # Nothing answered about residency, so nothing is claimed.
        self.assertIsNone(row["resident"])
        self.assertEqual(row["weights"]["path"], str(self.weights.resolve()))
        self.assertIn(self.MODEL, body["targets"])
        self.assertTrue(body["door"]["declared"])

    async def test_nothing_enrolled_is_an_empty_list_not_an_error(self):
        status, body, _ = await self.get("/admin/models")
        self.assertEqual(status, 200)
        self.assertEqual(body["models"], [])
        self.assertIn(self.MODEL, body["targets"])

    async def test_missing_weights_are_a_reported_state(self):
        """Guard rail R3, at the surface: the file is gone, the row says so in
        the plain sentence `check` wrote, and the route answers 200."""
        self.enroll_fixture()
        self.weights.unlink()
        status, body, _ = await self.get("/admin/models")
        self.assertEqual(status, 200)
        row = body["models"][0]
        self.assertEqual(row["state"], "missing")
        self.assertIn("not there any more", row["state_text"])
        self.assertIn(str(self.weights), row["state_text"])
        self.assertTrue(any(f["level"] == "error" for f in row["findings"]))

    async def test_the_card_holds_apply_shut_on_a_missing_model(self):
        """The R3 consequence the page owns: the row still draws, and the one
        button that would put a unit naming a vanished file in front of launchd
        is the one that goes away."""
        from hearth.ui import launch_models
        js = launch_models.PATH.read_text(encoding="utf-8")
        self.assertIn('m.state === "missing"', js)
        self.assertIn("apply.disabled = true", js)


# ── 4. the scan ──────────────────────────────────────────────────────────────

class TheScan(_Surface):

    async def test_scan_lists_the_file_and_marks_it_once_enrolled(self):
        status, body, _ = await self.get("/admin/models/scan")
        self.assertEqual(status, 200)
        keys = [c["display_key"] for c in body["candidates"]]
        self.assertEqual(len(keys), 1, body["candidates"])
        self.assertIsNone(body["candidates"][0]["enrolled_as"])
        self.assertFalse(body["cached"])
        self.assertEqual(body["candidates"][0]["path"], str(self.weights.resolve()))

        self.enroll_fixture()
        status, body, _ = await self.get("/admin/models/scan")
        self.assertTrue(body["cached"])        # served from the kept walk…
        self.assertEqual(body["candidates"][0]["enrolled_as"], self.MODEL)  # …refreshed

    async def test_refresh_walks_again(self):
        await self.get("/admin/models/scan")
        second = tiny_gguf(self.tmp / "models" / "pub" / "repo2" / "n.gguf")
        _, cached, _ = await self.get("/admin/models/scan")
        self.assertEqual(len(cached["candidates"]), 1)
        _, fresh, _ = await self.get("/admin/models/scan?refresh=1")
        self.assertFalse(fresh["cached"])
        self.assertIn(str(second.resolve()),
                      [c["path"] for c in fresh["candidates"]])


# ── 5 & 6. enroll and unenroll ───────────────────────────────────────────────

class EnrollPreviewThenConfirm(_Surface):

    def identity(self) -> str:
        root = roots_mod.Root("models", self.tmp / "models", "user")
        return scan_mod.scan_dir(self.weights.parent, root)[0].identity

    async def test_preview_writes_nothing(self):
        before = (self.model_dir / "model.toml").read_text(encoding="utf-8")
        status, body, _ = await self.post(
            "/admin/models/enroll", {"model": self.MODEL, "path": str(self.weights)})
        self.assertEqual(status, 200)
        self.assertFalse(body["enrolled"])
        self.assertIn("[weights]", body["preview"]["block"])
        self.assertIn("[weights.header]", body["preview"]["block"])
        self.assertIn(str(self.weights.resolve()), body["preview"]["block"])
        self.assertEqual(before,
                         (self.model_dir / "model.toml").read_text(encoding="utf-8"))

    async def test_yes_writes_the_block_and_keeps_every_other_line(self):
        status, body, _ = await self.post(
            "/admin/models/enroll",
            {"model": self.MODEL, "path": str(self.weights), "yes": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["enrolled"])
        after = (self.model_dir / "model.toml").read_text(encoding="utf-8")
        self.assertIn("[weights]", after)
        self.assertIn("# config/models/m1/model.toml", after)   # the comments live
        self.assertIsNotNone(enroll_mod.load_enrolled(self.MODEL))

    async def test_enroll_by_identity_uses_the_scan(self):
        await self.get("/admin/models/scan")
        status, body, _ = await self.post(
            "/admin/models/enroll",
            {"model": self.MODEL, "identity": self.identity(), "yes": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["enrolled"])

    async def test_an_unknown_target_is_a_404(self):
        status, body, _ = await self.post(
            "/admin/models/enroll", {"model": "no-such", "path": str(self.weights)})
        self.assertEqual(status, 404)
        self.assertIn(self.MODEL, body["targets"])

    async def test_a_new_directory_is_copied_from_the_shipped_example(self):
        status, body, _ = await self.post(
            "/admin/models/enroll",
            {"new": "zz-new-model", "path": str(self.weights), "yes": True})
        self.assertEqual(status, 200, body)
        made = cl.MODELS_DIR / "zz-new-model" / "model.toml"
        self.assertTrue(made.is_file())
        self.assertIn("[weights]", made.read_text(encoding="utf-8"))
        self.assertIn("id", body["note"])   # its id is still the placeholder

    async def test_a_new_name_that_is_not_a_safe_segment_is_refused(self):
        for bad in ("../escape", "Zz-Caps", "with space", ""):
            status, _, _ = await self.post(
                "/admin/models/enroll", {"new": bad, "path": str(self.weights)})
            self.assertIn(status, (400, 404), bad)
        self.assertFalse((cl.MODELS_DIR / "escape").exists())


class UnenrollPreviewThenConfirm(_Surface):

    async def test_preview_then_confirm_and_the_file_stays(self):
        self.enroll_fixture()
        status, body, _ = await self.post("/admin/models/unenroll",
                                          {"model": self.MODEL})
        self.assertEqual(status, 200)
        self.assertFalse(body["unenrolled"])
        self.assertIsNotNone(enroll_mod.load_enrolled(self.MODEL))

        status, body, _ = await self.post("/admin/models/unenroll",
                                          {"model": self.MODEL, "yes": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["unenrolled"])
        self.assertIsNone(enroll_mod.load_enrolled(self.MODEL))
        self.assertTrue(self.weights.is_file())   # never the file

    async def test_nothing_enrolled_is_a_404(self):
        status, _, _ = await self.post("/admin/models/unenroll", {"model": self.MODEL})
        self.assertEqual(status, 404)


# ── 7 & 8. render and apply ──────────────────────────────────────────────────

class RenderAndApply(_Surface):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.enroll_fixture()
        self.unit = self.agents / "zz.test.door.plist"

    async def test_render_writes_only_into_the_data_folder(self):
        status, body, _ = await self.post("/admin/models/render", {"model": self.MODEL})
        self.assertEqual(status, 200, body)
        self.assertIn("--model", body["argv"])
        self.assertEqual(body["unit"], "unapplied")
        self.assertTrue(Path(body["wrote"]).is_file())
        self.assertTrue(str(body["wrote"]).startswith(str(self.data)))
        self.assertFalse(self.unit.exists())          # LaunchAgents untouched

    async def test_apply_previews_then_writes_and_archives(self):
        guard = render_mod.Guard(False, True, "no companion running")
        with mock.patch.object(render_mod, "companion_guard", return_value=guard):
            status, body, _ = await self.post("/admin/models/apply",
                                              {"model": self.MODEL})
            self.assertEqual(status, 200, body)
            self.assertFalse(body["applied"])
            self.assertFalse(self.unit.exists())      # a preview writes nothing

            live_plist(self.unit, ["/bin/echo", "--model", "somewhere/else.gguf"],
                       "zz.test.door")
            status, body, _ = await self.post("/admin/models/apply",
                                              {"model": self.MODEL, "yes": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["applied"])
        self.assertTrue(self.unit.is_file())
        self.assertIn("ProgramArguments", self.unit.read_text(encoding="utf-8"))
        self.assertTrue(Path(body["archived"]).is_file())   # never deleted
        self.assertEqual(len(body["lines"]), 2)
        self.assertTrue(body["lines"][0].startswith("launchctl bootout "))

    async def test_a_blocked_companion_guard_is_a_409_that_wrote_nothing(self):
        guard = render_mod.Guard(True, True, "a companion is running — stop it first")
        with mock.patch.object(render_mod, "companion_guard", return_value=guard):
            status, body, _ = await self.post("/admin/models/apply",
                                              {"model": self.MODEL, "yes": True})
        self.assertEqual(status, 409)
        self.assertEqual(body["guard"], "companion")
        self.assertIn("companion is running", body["error"])
        self.assertFalse(self.unit.exists())

    async def test_an_uncertain_guard_is_a_warning_on_a_200(self):
        guard = render_mod.Guard(False, False, "the facade did not answer")
        with mock.patch.object(render_mod, "companion_guard", return_value=guard):
            status, body, _ = await self.post("/admin/models/apply",
                                              {"model": self.MODEL, "yes": True})
        self.assertEqual(status, 200, body)
        self.assertIn("did not answer", body["warning"])

    async def test_a_model_with_no_weights_is_a_404_on_both(self):
        enroll_mod.unenroll(self.MODEL)
        for path in ("/admin/models/render", "/admin/models/apply"):
            status, _, _ = await self.post(path, {"model": self.MODEL})
            self.assertEqual(status, 404, path)

    async def test_a_unit_that_matches_reads_applied(self):
        guard = render_mod.Guard(False, True, "no companion running")
        with mock.patch.object(render_mod, "companion_guard", return_value=guard):
            await self.post("/admin/models/apply", {"model": self.MODEL, "yes": True})
        _, body, _ = await self.get("/admin/models")
        self.assertEqual(body["models"][0]["unit"], "applied")
        self.assertEqual(body["models"][0]["diff"]["real"], 0)


# ── 9. the key path is never a fact ──────────────────────────────────────────

class TheAccessKeyPathNeverAppears(_Surface):

    async def test_no_route_ever_names_the_key_file(self):
        self.enroll_fixture()
        guard = render_mod.Guard(False, True, "no companion running")
        texts = []
        for path in ("/admin/models", "/admin/models/scan", "/admin/models/door"):
            _, _, text = await self.get(path)
            texts.append((path, text))
        for path, body in (("/admin/models/render", {"model": self.MODEL}),
                           ("/admin/models/enroll",
                            {"model": self.MODEL, "path": str(self.weights)}),
                           ("/admin/models/unenroll", {"model": self.MODEL})):
            _, _, text = await self.post(path, body)
            texts.append((path, text))
        with mock.patch.object(render_mod, "companion_guard", return_value=guard):
            for yes in (False, True):
                _, _, text = await self.post("/admin/models/apply",
                                             {"model": self.MODEL, "yes": yes})
                texts.append(("/admin/models/apply", text))
        for path, text in texts:
            with self.subTest(route=path):
                self.assertNotIn(KEY_FILE_NAME, text)
                self.assertNotIn(str(self.key_file), text)

    async def test_the_door_view_reports_set_and_not_a_path(self):
        status, body, _ = await self.get("/admin/models/door")
        self.assertEqual(status, 200)
        self.assertEqual(body["api_key"], "set")
        self.assertNotIn("api_key_file", body)
        self.assertEqual(body["label"], "zz.test.door")
        self.assertEqual(body["port"], DOOR_PORT)

    async def test_the_rendered_argv_keeps_the_flag_and_drops_the_value(self):
        self.enroll_fixture()
        _, body, text = await self.post("/admin/models/render", {"model": self.MODEL})
        self.assertIn("--api-key-file", body["argv"])
        self.assertIn(models_mod.HIDDEN, body["argv"])
        self.assertNotIn(KEY_FILE_NAME, json.dumps(body))
        self.assertNotIn(KEY_FILE_NAME, text)


# ── 10. the built-in actuators ───────────────────────────────────────────────

class BuiltInDoorActuators(_Surface):

    async def test_the_pair_is_registered_and_guarded(self):
        acts = self.app["actuators"]
        self.assertIn(models_mod.LOAD, acts)
        self.assertIn(models_mod.UNLOAD, acts)
        for name in (models_mod.LOAD, models_mod.UNLOAD):
            self.assertEqual(acts.guard(name), "companion")
        _, body, _ = await self.get("/admin/models/door")
        self.assertEqual(body["actuators"],
                         {"load": models_mod.LOAD, "unload": models_mod.UNLOAD})

    def test_the_commands_are_derived_from_the_door_table(self):
        cfg = roots_mod.load_weights_config(self.data / "config" / "weights.toml")
        derived = models_mod.door_actuators(cfg, uid=501)
        self.assertEqual(derived[models_mod.UNLOAD]["command"],
                         ["/bin/launchctl", "bootout", "gui/501/zz.test.door"])
        self.assertEqual(derived[models_mod.LOAD]["command"][:3],
                         ["/bin/launchctl", "bootstrap", "gui/501"])
        self.assertTrue(derived[models_mod.LOAD]["command"][3]
                        .endswith("zz.test.door.plist"))
        self.assertEqual(derived[models_mod.LOAD]["probe_url"],
                         f"http://127.0.0.1:{DOOR_PORT}/health")
        self.assertEqual(derived[models_mod.LOAD]["timeout_s"], 60.0)

    def test_an_operator_declaration_of_the_same_name_wins(self):
        cfg = roots_mod.load_weights_config(self.data / "config" / "weights.toml")
        mine = {models_mod.LOAD: {"command": ["/bin/echo", "mine"]}}
        merged = models_mod.with_door_actuators(mine, cfg, uid=501)
        self.assertEqual(merged[models_mod.LOAD]["command"], ["/bin/echo", "mine"])
        self.assertIn(models_mod.UNLOAD, merged)   # the other half still arrives

    def test_actuators_declared_under_other_names_are_untouched(self):
        cfg = roots_mod.load_weights_config(self.data / "config" / "weights.toml")
        mine = {"lm-load": {"command": ["/bin/launchctl", "bootstrap", "gui/501", "x"]}}
        merged = models_mod.with_door_actuators(mine, cfg, uid=501)
        self.assertEqual(merged["lm-load"], mine["lm-load"])
        self.assertEqual(len(merged), 3)


class NoDoorTableNoButtons(_Surface):
    """A machine that never named a door gets no built-ins and no Load button."""

    DOOR = False

    async def test_nothing_is_derived(self):
        acts = self.app["actuators"]
        self.assertNotIn(models_mod.LOAD, acts)
        self.assertNotIn(models_mod.UNLOAD, acts)
        _, body, _ = await self.get("/admin/models/door")
        self.assertFalse(body["declared"])
        self.assertEqual(body["actuators"], {})
        self.assertEqual(body["api_key"], "unset")


if __name__ == "__main__":
    unittest.main()
