"""test_live_switch.py — the LIVE companion switch (ADR 007 stroke 3).

Proves, on real files and real session stores (pipeline objects duck-typed —
the module is deliberately pipecat-free, so this suite runs in the base venv):

  1. PREPARE — arming validates against the registry + on-disk install and
     PREPARES eagerly (memory attach + recall, session resolution); refusals
     arm nothing; the model FIELD swap demands residency (M4c); a second arm
     supersedes the first and releases its prepared seam.
  2. APPLY — the turn-boundary swap: the trigger utterance carries over, the
     old session finalizes with graceful-stop parity (memory record first,
     hold honored, ephemeral true-deleted) in the background, the reloader is
     REBASED, engine facts / recorder / active.toml converge, and the returned
     delta dict carries the new model/temperature/system/effort.
  3. PARITY — provider aliases match the engine probe's; the registry declares
     all four selection fields live-capable (the router's consult).

Run:  .venv/bin/python -m unittest tests.test_live_switch
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hearth.config import config_loader
from hearth.pipeline import switcher as switcher_mod
from hearth.session import session_store
from hearth.supervisor import switch as switch_mod


def _build_install(root: Path) -> None:
    """Two characters (alpha has a persona variant), two models with templates."""
    for char in ("alpha", "beta"):
        c = root / "characters" / char
        (c / "voices" / "v1").mkdir(parents=True)
        (c / "persona.md").write_text(f"## IDENTITY\n{char} identity\n## SOUL\n{char} soul\n")
        (c / "voices" / "v1" / "sample.wav").write_bytes(b"RIFFfake")
        (c / "voices" / "v1" / "voice.toml").write_text(
            f'tag = "{char}-v1"\nref_wav = "sample.wav"\n')
    (root / "characters" / "alpha" / "persona.alt.md").write_text(
        "## IDENTITY\nalpha identity\n## SOUL\nalt soul\n")
    for name, mid in (("m1", "model-one"), ("m2", "model-two")):
        d = root / "config" / "models" / name
        d.mkdir(parents=True)
        (d / "model.toml").write_text(
            f'id = "{mid}"\ntemperature = 0.7\nreasoning_effort = "none"\n')
        (d / "system-prompt-template.md").write_text(f"SYS {name}: {{{{persona}}}}")


class _FakeTTS:
    def __init__(self):
        self.refs = []

    def set_ref_wav(self, path):
        import concurrent.futures as cf
        self.refs.append(path)
        f = cf.Future()
        f.set_result(None)
        return f


class _FakeContext:
    def __init__(self, messages=None):
        self._messages = list(messages or [])

    @property
    def messages(self):
        return self._messages

    def set_messages(self, msgs):
        self._messages[:] = msgs


class _FakeReloader:
    def __init__(self):
        self.rebases = []

    def rebase(self, **kw):
        self.rebases.append(kw)


class _FakeSeam:
    def __init__(self, companion):
        self.companion = companion
        self.ended = None
        self.closed = False

    def augment(self, system):
        return system + "\n\nMEM:" + self.companion

    def on_session_end(self, messages, store):
        self.ended = (list(messages), store)
        return f"record kept (fake {self.companion})"

    def close(self):
        self.closed = True


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_install(self.root)
        self._p = mock.patch.object(config_loader, "_DATA", self.root)
        self._p.start()
        self.addCleanup(self._p.stop)
        self.active = self.root / "config" / "active.toml"
        self._pa = mock.patch.object(switch_mod, "active_path", lambda: self.active)
        self._pa.start()
        self.addCleanup(self._pa.stop)
        self._ps = mock.patch(
            "hearth.control.features.config_knobs.scrub_session_scoped",
            return_value=[])
        self.scrub = self._ps.start()
        self.addCleanup(self._ps.stop)

        self.alpha_ref = config_loader.load_voice("alpha", "v1")["ref_wav"]
        self.beta_ref = config_loader.load_voice("beta", "v1")["ref_wav"]
        self.tts = _FakeTTS()
        self.reloader = _FakeReloader()
        self.ctx = _FakeContext([
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ])
        self.boot_seam = _FakeSeam("alpha")
        self.boot_store = session_store.SessionStore(
            session_id="session-boot", model="model-one", voice="alpha-v1",
            prompt_sha256="x" * 64, character="alpha", persona="default")
        self.boot_store.snapshot(self.ctx.messages[:2])
        self.ei = {"character": "alpha", "voice": "v1", "persona": "default",
                   "session": "New", "reliable": None}
        self.rec = SimpleNamespace(armed=False, character="alpha",
                                   base_dir=Path("."), default_name="session")
        self.resident = ["model-one"]
        self.seams = []

    def _mk(self) -> switcher_mod.LiveSwitcher:
        def seam_factory(char, persona):
            s = _FakeSeam(char)
            self.seams.append(s)
            return s

        async def probe():
            return self.resident

        active = SimpleNamespace(
            character="alpha", model_name="m1", voice_name="v1",
            persona_name="default", model_id="model-one", temperature=0.7,
            reasoning_effort="none", voice_tag="alpha-v1",
            ref_wav=self.alpha_ref, reliable_context=None)
        return switcher_mod.LiveSwitcher(
            active=active, reloader=self.reloader, tts=self.tts,
            context=self.ctx, store=self.boot_store, seam=self.boot_seam,
            lm_provider="llama-server", lm_base_url="http://127.0.0.1:1/v1",
            lm_token="", engine_info=self.ei, recorder=self.rec,
            seam_factory=seam_factory, resident_probe=probe)


class SwitcherPrepare(_Base):
    async def test_arm_valid_persona_variant(self):
        sw = self._mk()
        res = await sw.prepare({"persona": "alt"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["changed"], ["persona"])
        desc = await sw.describe()
        self.assertTrue(desc["armed"])
        self.assertEqual(desc["pending"]["persona"], "alt")
        self.assertEqual(desc["current"]["persona"], "default")
        self.assertNotIn("system_instruction", json.dumps(desc))  # names only

    async def test_noop_refused(self):
        sw = self._mk()
        res = await sw.prepare({})
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 400)
        self.assertIsNone(await sw.apply_pending())

    async def test_invalid_refused_nothing_armed(self):
        sw = self._mk()
        res = await sw.prepare({"voice": "nope"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 400)
        self.assertEqual(self.seams, [], "a refused arm must not attach a seam")
        self.assertFalse((await sw.describe())["armed"])

    async def test_model_swap_requires_residency(self):
        sw = self._mk()
        self.resident = []
        res = await sw.prepare({"model": "m2"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 409)
        self.assertTrue(any("resident" in e for e in res["errors"]), res)
        self.resident = None  # probe failure ⇒ refuse honestly
        res = await sw.prepare({"model": "m2"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 409)
        self.resident = ["model-two"]
        res = await sw.prepare({"model": "m2"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["changed"], ["model"])

    async def test_supersede_closes_prior_seam(self):
        sw = self._mk()
        self.assertTrue((await sw.prepare({"persona": "alt"}))["ok"])
        first = self.seams[0]
        self.assertTrue((await sw.prepare({"character": "beta"}))["ok"])
        self.assertTrue(first.closed, "a superseded arm must release its seam")
        self.assertEqual((await sw.describe())["pending"]["character"], "beta")

    async def test_prepare_busy_refused(self):
        sw = self._mk()
        sw._preparing = True
        res = await sw.prepare({"persona": "alt"})
        self.assertEqual(res["code"], 409)


class SwitcherApply(_Base):
    async def test_apply_swaps_everything(self):
        sw = self._mk()
        self.assertTrue((await sw.prepare({"character": "beta"}))["ok"])
        delta = await sw.apply_pending()
        self.assertIsNotNone(delta)
        self.assertEqual(delta["model"], "model-one")
        self.assertIn("beta identity", delta["system_instruction"])
        self.assertIn("MEM:beta", delta["system_instruction"])  # recall augmented
        self.assertEqual(delta["reasoning_effort"], "none")
        # context: only the trigger utterance carried over
        self.assertEqual(self.ctx.messages, [{"role": "user", "content": "u2"}])
        # voice re-cloned to beta's clip
        self.assertEqual(self.tts.refs, [self.beta_ref])
        # reloader rebased onto the new companion's baselines
        rb = self.reloader.rebases[-1]
        self.assertEqual(rb["model_name"], "m1")
        self.assertIn("beta identity", rb["baseline_llm"]["persona"])
        self.assertEqual(rb["baseline_voice"], self.beta_ref)
        # current pieces swapped; panel facts follow
        self.assertEqual(sw.current_store.character, "beta")
        self.assertIs(sw.current_seam, self.seams[0])
        self.assertEqual(self.ei["character"], "beta")
        self.assertEqual(self.ei["session"], "New")
        self.assertEqual(self.rec.character, "beta")
        self.assertTrue(self.scrub.called)
        # old side finalized with graceful-stop parity
        await sw.drain()
        self.assertIsNotNone(self.boot_seam.ended)
        self.assertEqual(self.boot_seam.ended[0],
                         self.ctx.messages[:0] + [{"role": "user", "content": "u1"},
                                                  {"role": "assistant", "content": "a1"}])
        self.assertTrue(self.boot_seam.closed)
        self.assertFalse(self.boot_store.path.exists(), "ephemeral old session true-deleted")
        # file discipline: active.toml converged on the applied selection
        with open(self.active, "rb") as f:
            data = tomllib.load(f)
        self.assertEqual(data["character"], "beta")
        self.assertEqual(sw._status["phase"], "applied")

    async def test_apply_hold_promotes_old(self):
        sw = self._mk()
        self.assertTrue((await sw.prepare(
            {"character": "beta", "hold": True, "hold_name": "keep-me"}))["ok"])
        self.assertIsNotNone(await sw.apply_pending())
        await sw.drain()
        kept = self.root / "characters" / "alpha" / "sessions" / "keep-me.json"
        self.assertTrue(kept.is_file(), "held old session must survive, renamed")
        self.assertTrue(json.loads(kept.read_text())["held"])

    async def test_apply_resume_seeds_messages(self):
        held = session_store.SessionStore(
            session_id="topic", model="model-one", voice="beta-v1",
            prompt_sha256="y" * 64, character="beta", persona="default",
            name="topic", held=True)
        held.snapshot([{"role": "user", "content": "old-b"},
                       {"role": "assistant", "content": "old-b-reply"}])
        sw = self._mk()
        res = await sw.prepare({"character": "beta", "mode": "resume", "name": "topic"})
        self.assertTrue(res["ok"], res)
        self.assertTrue(any("prompt changed" in w for w in res["warnings"]), res)
        self.assertIsNotNone(await sw.apply_pending())
        self.assertEqual(self.ctx.messages, [
            {"role": "user", "content": "old-b"},
            {"role": "assistant", "content": "old-b-reply"},
            {"role": "user", "content": "u2"},
        ])
        self.assertEqual(self.ei["session"], "topic")
        self.assertTrue(sw.current_store.held)

    async def test_apply_nothing_pending(self):
        sw = self._mk()
        self.assertIsNone(await sw.apply_pending())

    async def test_apply_converges_file_without_prev_churn(self):
        # The daemon path pre-writes the selection; apply must not rewrite it.
        switch_mod.write_selection({"character": "beta", "model": "m1",
                                    "voice": "v1", "persona": "default"})
        sw = self._mk()
        self.assertTrue((await sw.prepare({"character": "beta"}))["ok"])
        self.assertIsNotNone(await sw.apply_pending())
        await sw.drain()
        self.assertFalse((self.active.parent / "active.toml.prev").exists(),
                         "an already-converged file must not be rewritten")

    async def test_voice_unchanged_not_recloned(self):
        sw = self._mk()
        self.assertTrue((await sw.prepare({"persona": "alt"}))["ok"])
        self.assertIsNotNone(await sw.apply_pending())
        self.assertEqual(self.tts.refs, [], "same clip ⇒ no re-clone")
        self.assertIn("alt soul", self.reloader.rebases[-1]["baseline_llm"]["persona"])

    async def test_snapshot_routes_to_current_store(self):
        sw = self._mk()
        self.assertTrue((await sw.prepare({"character": "beta"}))["ok"])
        self.assertIsNotNone(await sw.apply_pending())
        sw.snapshot(self.ctx.messages)
        self.assertTrue(any(
            (self.root / "characters" / "beta" / "sessions").glob("*.json")))
        await sw.drain()

    async def test_close_pending_releases_seam(self):
        sw = self._mk()
        self.assertTrue((await sw.prepare({"character": "beta"}))["ok"])
        sw.close_pending()
        self.assertTrue(self.seams[0].closed)
        self.assertIsNone(await sw.apply_pending())


class Parity(unittest.TestCase):
    def test_llama_alias_parity_with_engine_probe(self):
        from hearth.control import engine_probe_llamaserver as probe
        self.assertEqual(switcher_mod._LLAMA_ALIASES, probe._LLAMASERVER_ALIASES)

    def test_registry_declares_all_selection_fields_live(self):
        self.assertEqual(switch_mod.live_capable_fields(),
                         frozenset(switch_mod.SELECTION_KEYS))

    def test_changed_fields_helper(self):
        merged = {"character": "b", "model": "m", "voice": "v", "persona": "p"}
        self.assertEqual(switch_mod.changed_fields(None, merged),
                         list(switch_mod.SELECTION_KEYS))
        prev = dict(merged, persona="default")
        self.assertEqual(switch_mod.changed_fields(prev, merged), ["persona"])


if __name__ == "__main__":
    unittest.main()
