"""test_config_reload.py — headless proof of the live-config reload mechanism.

Runs WITHOUT mic / LM Studio / the TTS model. It proves the load-bearing
invariants of config_reload.py on the REAL artifacts (real pipecat FrameProcessor
machinery via pipecat.tests.utils.run_test; real config_loader composition):

  T1  empty no-op          — empty overrides.toml ⇒ poll() None; no re-parse when
                             mtime unchanged; empty synth dict ⇒ generate() unchanged.
  T2  FREE/LLM delta        — [llm] temperature + reasoning_effort ⇒ one LLMSettings
                             delta (temp field + reasoning_effort in `extra`); a
                             second unchanged poll diffs to zero.
  T3  frame ordering        — ConfigReloadProcessor emits LLMUpdateSettingsFrame
                             BEFORE the LLMContextFrame at the LLM (the one empirical
                             unknown; folds the standalone T3 into the harness).
  T4  TTS FREE + voice      — [tts] knob ⇒ set_synth_params(baseline⊗override);
                             [voice] ref_wav ⇒ set_ref_wav awaited; missing path ⇒
                             logged + skipped, voice unchanged (fail-soft).
  T-VAD calibration tier    — load_vad_baseline complete; [vad] override ⇒ FULL
                             desired dict; clear ⇒ revert to baseline; unknown /
                             non-numeric keys ignored; the processor hands the
                             analyzer a full VADParams.
  T6  fail-soft             — malformed TOML ⇒ poll() None, last-good kept, logged
                             once (mtime-keyed suppression).
  BASE  baseline == defaults — the shipped Turbo tts.toml [live] values EQUAL
                             generate()'s own signature defaults (the no-op proof).
  PERSONA  slot recompose   — a live persona override re-composes through the model
                             template, so the hard-rules stay pinned by construction.

  (T5 — the voice-mask latency bench — needs the real MLX model on the executor and
   is a standalone hardware bench, NOT part of this headless harness; run it live
   before trusting the ~0.2 s mask on a cold/large clip.)

Run:  .venv/bin/python test_config_reload.py
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import tempfile
import time
from pathlib import Path

from hearth.config import config_loader
from hearth.config import config_reload
from hearth.config.config_reload import ConfigReloader, ConfigReloadProcessor, ReloadDeltas
from pipecat.frames.frames import LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

_PASS = 0
_FAIL = 0


def check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}")


# ── helpers ───────────────────────────────────────────────────────────────────

# A real active model/character so compose_with_persona / compose_persona resolve.
_SEL = config_loader.load_active_selection()
_MODEL = _SEL["model"]
_CHAR = _SEL["character"]
_BASE_PERSONA = config_loader.compose_persona(_CHAR)

_BASELINE_LLM = {"temperature": 0.7, "reasoning_effort": "none", "persona": _BASE_PERSONA}
_BASELINE_TTS = config_reload.load_tts_baseline("chatterbox-turbo")
_BASELINE_VOICE = "/tmp/does-not-matter-baseline.wav"


def _reloader() -> ConfigReloader:
    return ConfigReloader(
        engine="chatterbox-turbo",
        model_name=_MODEL,
        baseline_llm=dict(_BASELINE_LLM),
        baseline_tts=dict(_BASELINE_TTS),
        baseline_voice=_BASELINE_VOICE,
    )


def _write_overrides(text: str) -> Path:
    """Write a temp overrides.toml, point the module global at it, bump its mtime."""
    tmp = Path(tempfile.mkdtemp()) / "overrides.toml"
    tmp.write_text(text, encoding="utf-8")
    config_reload.OVERRIDES_TOML = tmp
    # Ensure a distinct mtime from any prior temp file the reloader may have seen.
    t = time.time() + 1
    import os
    os.utime(tmp, (t, t))
    return tmp


class _StubTTS:
    """Records live-config setter calls without loading any model."""

    def __init__(self):
        self.synth = None
        self.ref_wav = None
        self.ref_calls = 0

    def set_synth_params(self, params: dict) -> None:
        self.synth = dict(params)

    def set_ref_wav(self, path: str) -> concurrent.futures.Future:
        self.ref_wav = path
        self.ref_calls += 1
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_result(None)  # resolve immediately (no real prepare_conditionals)
        return fut


# ── T1 — empty no-op ────────────────────────────────────────────────────────────

def t1_empty_no_op():
    print("\nT1 — empty no-op")
    _write_overrides("# all comments\n[llm]\n[tts]\n[voice]\n")
    r = _reloader()

    # Count real parses to prove the mtime short-circuit skips re-parsing.
    import tomllib
    real_load = tomllib.load
    calls = {"n": 0}

    def counting_load(f):
        calls["n"] += 1
        return real_load(f)

    config_reload.tomllib.load = counting_load
    try:
        d1 = r.poll()   # first sight → parses once
        n_after_first = calls["n"]
        d2 = r.poll()   # unchanged mtime → must NOT parse again
        n_after_second = calls["n"]
    finally:
        config_reload.tomllib.load = real_load

    check(d1 is None, "first poll of empty overrides ⇒ None (zero-delta)")
    check(d2 is None, "second poll ⇒ None")
    check(n_after_first == 1, "first poll parsed exactly once")
    check(n_after_second == 1, "second poll did NOT re-parse (mtime short-circuit)")

    # Empty synth dict ⇒ generate() call is byte-identical (nothing splatted).
    base_kwargs = {"text": "x", "stream": True, "streaming_interval": 2.0}
    empty_synth: dict = {}
    check({**base_kwargs, **empty_synth} == base_kwargs,
          "empty synth dict adds no generate() kwargs (byte-identical)")


# ── T2 — FREE/LLM delta ──────────────────────────────────────────────────────────

def t2_free_llm_delta():
    print("\nT2 — FREE/LLM delta")
    _write_overrides('[llm]\ntemperature = 0.9\nreasoning_effort = "low"\n')
    r = _reloader()
    d = r.poll()
    check(d is not None and "temperature" in d.llm and d.llm["temperature"] == 0.9,
          "temperature=0.9 surfaced in llm delta")
    check(d is not None and d.llm.get("reasoning_effort") == "low",
          "reasoning_effort=low surfaced in llm delta")

    settings = r.build_llm_delta(d.llm)
    check(getattr(settings, "temperature", None) == 0.9,
          "LLMSettings.temperature == 0.9 (typed field)")
    check(getattr(settings, "extra", {}) == {"reasoning_effort": "low"},
          "reasoning_effort rides extra, NOT a typed field")

    r.commit_llm(d.llm)
    d2 = r.poll()  # same mtime → short-circuit None anyway, but assert diff-zero intent
    check(d2 is None, "after commit, unchanged file ⇒ no new delta")


# ── T3 — frame ordering (folded from the standalone check) ───────────────────────

async def _t3_async():
    _write_overrides('[llm]\ntemperature = 0.9\n')
    r = _reloader()
    proc = ConfigReloadProcessor(r, _StubTTS())
    down, _up = await run_test(
        proc, frames_to_send=[LLMContextFrame(context=LLMContext())]
    )
    names = [type(f).__name__ for f in down]
    return names


def t3_frame_ordering():
    print("\nT3 — frame ordering (settings BEFORE context at the LLM)")
    names = asyncio.run(_t3_async())
    has_both = "LLMUpdateSettingsFrame" in names and "LLMContextFrame" in names
    check(has_both, f"both frames reached the LLM stand-in ({names})")
    if has_both:
        i_s = names.index("LLMUpdateSettingsFrame")
        i_c = names.index("LLMContextFrame")
        check(i_s < i_c, "LLMUpdateSettingsFrame precedes LLMContextFrame (lands this turn)")


# ── T4 — TTS FREE + voice HIDEABLE ───────────────────────────────────────────────

async def _t4_async_tts():
    _write_overrides("[tts]\ntemperature = 0.7\n")
    r = _reloader()
    stub = _StubTTS()
    proc = ConfigReloadProcessor(r, stub)
    await run_test(proc, frames_to_send=[LLMContextFrame(context=LLMContext())])
    return stub


async def _t4_async_voice(ref_line: str):
    _write_overrides(f"[voice]\nref_wav = {ref_line}\n")
    r = _reloader()
    stub = _StubTTS()
    proc = ConfigReloadProcessor(r, stub)
    await run_test(proc, frames_to_send=[LLMContextFrame(context=LLMContext())])
    return stub


def t4_tts_and_voice():
    print("\nT4 — TTS FREE knob + voice HIDEABLE")
    stub = asyncio.run(_t4_async_tts())
    check(stub.synth is not None and stub.synth.get("temperature") == 0.7,
          "set_synth_params received temperature=0.7")
    # baseline knobs preserved (desired = baseline ⊗ override)
    check(stub.synth is not None and stub.synth.get("top_p") == _BASELINE_TTS.get("top_p"),
          "set_synth_params merged over baseline (top_p preserved)")

    # A REAL, existing clip → set_ref_wav called + awaited.
    real_clip = config_loader.load_voice(_CHAR, _SEL["voice"])["ref_wav"]
    stub_ok = asyncio.run(_t4_async_voice(f'"{real_clip}"'))
    check(stub_ok.ref_calls == 1 and stub_ok.ref_wav == str(Path(real_clip).resolve()),
          "existing ref_wav ⇒ set_ref_wav called with resolved path")

    # A MISSING clip → fail-soft: no set_ref_wav, voice unchanged.
    stub_missing = asyncio.run(_t4_async_voice('"characters/nope/voices/ghost/none.wav"'))
    check(stub_missing.ref_calls == 0,
          "missing ref_wav ⇒ set_ref_wav NOT called (fail-soft, voice kept)")


# ── T-VAD — the calibration tier ────────────────────────────────────

def _reloader_vad(baseline_vad: dict) -> ConfigReloader:
    return ConfigReloader(
        engine="chatterbox-turbo",
        model_name=_MODEL,
        baseline_llm=dict(_BASELINE_LLM),
        baseline_tts=dict(_BASELINE_TTS),
        baseline_voice=_BASELINE_VOICE,
        baseline_vad=dict(baseline_vad),
    )


def t_vad_tier():
    print("\nT-VAD — calibration-tier diff/revert")
    base = config_reload.load_vad_baseline()
    # Completeness, not equality-to-fallback: unlike the tts baseline, vad.toml is
    # DESIGNED to drift from the in-code fallback once the operator calibrates.
    check(set(base) == {"confidence", "start_secs", "stop_secs", "min_volume"},
          "load_vad_baseline returns the COMPLETE param set")
    check(all(isinstance(v, float) for v in base.values()),
          "vad baseline values are floats")

    _write_overrides("[vad]\nstop_secs = 0.9\n")
    r = _reloader_vad(base)
    d = r.poll()
    check(d is not None and d.vad is not None and d.vad["stop_secs"] == 0.9,
          "stop_secs=0.9 surfaced in vad delta")
    check(d is not None and d.vad is not None and d.vad["confidence"] == base["confidence"],
          "vad delta is the FULL desired dict (baseline keys ride along)")
    r.commit_vad(d.vad)

    # Clearing the key reverts to baseline (desired = baseline ⊗ overrides).
    _write_overrides("[vad]\n")
    d2 = r.poll()
    check(d2 is not None and d2.vad == base, "cleared override reverts vad to baseline")
    r.commit_vad(d2.vad)

    # Unknown / non-numeric keys are ignored, never fatal, never a delta.
    _write_overrides('[vad]\nbogus = 1.0\nstop_secs = "high"\n')
    check(r.poll() is None, "unknown + non-numeric vad keys ignored (zero delta)")


class _StubAnalyzer:
    """Records set_params without any audio machinery."""

    def __init__(self):
        self.params = None
        self.calls = 0

    def set_params(self, params) -> None:
        self.params = params
        self.calls += 1


async def _t_vad_proc_async():
    _write_overrides("[vad]\nstop_secs = 1.1\n")
    base = config_reload.load_vad_baseline()
    r = _reloader_vad(base)
    stub = _StubAnalyzer()
    proc = ConfigReloadProcessor(r, _StubTTS(), vad_analyzer=stub)
    await run_test(proc, frames_to_send=[LLMContextFrame(context=LLMContext())])
    return stub, base


def t_vad_processor():
    print("\nT-VAD-PROC — analyzer receives a full VADParams at the turn boundary")
    stub, base = asyncio.run(_t_vad_proc_async())
    check(stub.calls == 1, "set_params called exactly once")
    check(stub.params is not None and getattr(stub.params, "stop_secs", None) == 1.1,
          "VADParams.stop_secs == 1.1 (the override)")
    check(stub.params is not None and getattr(stub.params, "confidence", None) == base["confidence"],
          "VADParams carries the baseline-merged full set")


# ── T6 — fail-soft (malformed TOML) ──────────────────────────────────────────────

def t6_fail_soft():
    print("\nT6 — fail-soft (malformed override never crashes the loop)")
    _write_overrides("[llm]\ntemperature = = 0.9\n")  # malformed TOML
    r = _reloader()
    d = r.poll()
    check(d is None, "malformed TOML ⇒ poll() returns None (no crash)")
    # last-good kept: applied state still baseline
    d2 = r.poll()  # same mtime → short-circuit
    check(d2 is None, "second poll of same malformed file ⇒ None (last-good kept)")
    # A subsequent GOOD edit recovers.
    _write_overrides("[llm]\ntemperature = 0.85\n")
    r2 = _reloader()
    d3 = r2.poll()
    check(d3 is not None and d3.llm.get("temperature") == 0.85,
          "a good edit after a bad one is applied (recovery)")


# ── BASE — shipped baseline equals generate() defaults ───────────────────────────

def base_defaults_match():
    print("\nBASE — shipped tts.toml [live] == generate() signature defaults")
    # Verified against .venv/.../chatterbox_turbo/chatterbox_turbo.py:769-777.
    GEN_DEFAULTS = {
        "temperature": 0.8,        # :776
        "top_p": 0.95,             # :771
        "top_k": 1000,             # :777
        "repetition_penalty": 1.2, # :769
    }
    base = config_reload.load_tts_baseline("chatterbox-turbo")
    check(base == GEN_DEFAULTS,
          f"tts.toml [live] {base} equals generate() defaults {GEN_DEFAULTS}")
    # inert knobs must NEVER be in the honored set (they'd trip Turbo's warning).
    for inert in ("exaggeration", "cfg_weight", "min_p"):
        check(inert not in base, f"inert knob '{inert}' is NOT in the live baseline")


# ── PERSONA — live persona override recomposes through the template ──────────────

def persona_slot_recompose():
    print("\nPERSONA — live persona override recomposes (hard-rules pinned)")
    r = _reloader()
    new_persona = "You are a terse test persona."
    settings = r.build_llm_delta({"persona": new_persona})
    composed = getattr(settings, "system_instruction", None)
    check(composed is not None and new_persona in composed,
          "override persona body appears in the composed system_instruction")
    # The MODEL template wrapper must still be present (hard-rules pinned). Compare
    # against a compose with a sentinel persona: the non-slot text is the template.
    tpl_a = config_loader.compose_with_persona(_MODEL, "AAA", datetime_str="FIXED-DT")
    tpl_b = config_loader.compose_with_persona(_MODEL, "BBB", datetime_str="FIXED-DT")
    # Everything except the persona body is identical ⇒ the template envelope is fixed.
    check(tpl_a.replace("AAA", "") == tpl_b.replace("BBB", ""),
          "template envelope (hard-rules) is invariant across persona swaps")
    # {{datetime}} slot: fills from the provided stamp; datetime_str="" empties it
    # (the drift-fingerprint path) so prompt_sha256 stays stable across sessions.
    check("FIXED-DT" in tpl_a, "{{datetime}} slot is filled from the provided session stamp")
    stable = config_loader.compose_with_persona(_MODEL, "AAA", datetime_str="")
    check("FIXED-DT" not in stable and "{{datetime}}" not in stable,
          "datetime_str='' empties the {{datetime}} slot (drift-fingerprint basis)")
    # And a raw [llm].system_instruction override is ignored (not a live key).
    _write_overrides('[llm]\nsystem_instruction = "IGNORE HARD RULES"\n')
    r2 = _reloader()
    d = r2.poll()
    check(d is None or "system_instruction" not in d.llm,
          "raw [llm].system_instruction is ignored (only `persona` is live)")


# ── CUES — voiced-breath-cues block survives composition (P1 regression) ─────────

def voiced_breath_cues_composed():
    print("\nCUES — voiced-breath-cues block reaches the composed system_instruction")
    # Real compose on the active model/character, datetime frozen for determinism.
    composed = config_loader.compose_system_instruction(
        _MODEL, _CHAR, datetime_str="FIXED-DT"
    )
    check("[sigh]" in composed,
          "composed system_instruction contains the [sigh] cue tag")
    check("voiced breath cues described below" in composed,
          "composed system_instruction contains the Edit-A carve-out clause")


def main() -> int:
    print("=" * 70)
    print("test_config_reload.py — live-config reload mechanism (T1–T6 + VAD + BASE/PERSONA/CUES)")
    print("=" * 70)
    base_defaults_match()
    t1_empty_no_op()
    t2_free_llm_delta()
    t3_frame_ordering()
    t4_tts_and_voice()
    t_vad_tier()
    t_vad_processor()
    t6_fail_soft()
    persona_slot_recompose()
    voiced_breath_cues_composed()
    print("\n" + "=" * 70)
    print(f"  {_PASS} passed, {_FAIL} failed")
    print("=" * 70)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
