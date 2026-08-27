"""config_reload.py — turn-boundary live-config reload (FREE + HIDEABLE tiers).

The live counterpart to `config_loader.py`. Where `config_loader` reads the
selection ONCE at startup (fail-fast), this module re-reads a small live-override
layer at every turn boundary and applies only what changed — with the inverse
error posture: **fail-soft** (a malformed/partial override never crashes the loop).

Two pieces:
    ConfigReloader          — pure poll+diff+apply LOGIC (no pipeline, unit-testable).
    ConfigReloadProcessor   — a thin FrameProcessor that fires the reloader once per
                              turn (on LLMContextFrame) and routes the deltas.

Tier map:
    FREE / LLM     temperature, reasoning_effort, persona  → LLMUpdateSettingsFrame
    FREE / TTS     temperature, top_p, top_k, repetition_penalty → tts.set_synth_params()
    FREE / VAD     confidence, start_secs, stop_secs, min_volume → analyzer.set_params()
                   (the CALIBRATION tier — per-room/mic, never
                   profile-carried; baseline = config/vad.toml [live])
    HIDEABLE/voice ref_wav                                  → tts.set_ref_wav()  (~0.2s)
The EXPENSIVE tier (LLM-model swap, TTS engine swap) is DEFERRED.

Key design choice — **desired = baseline ⊗ overrides**, diffed against applied:
    A live override is an OVERLAY on the persisted baseline. `desired` is always the
    full baseline with the current override keys layered on top; the delta is
    `desired` vs the last-applied state. This is what makes REVERT work: delete a key
    from overrides.toml and `desired` falls back to the baseline value, producing a
    delta that restores it. A naive "only keys present in the file" diff would leave a
    reverted key stuck at its last override.

Persona-slot refinement (why the live LLM key is `persona`, not `system_instruction`):
    A live system-prompt change overrides only the {{persona}} slot and is re-composed
    through the MODEL template via `config_loader.compose_with_persona`. The template
    carries the spoken/no-markdown HARD RULES, so they stay pinned by construction — an
    operator override can never drop them. A raw [llm].system_instruction is therefore
    NOT a live key this pass (it is logged and ignored).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import Frame, LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import LLMSettings

from hearth.config import config_loader

# Live layer + per-engine persisted baselines live under config/ (reuse the loader's dir).
OVERRIDES_TOML = config_loader.CONFIG_DIR / "overrides.toml"
TTS_DIR = config_loader.CONFIG_DIR / "tts"
VAD_TOML = config_loader.CONFIG_DIR / "vad.toml"

# The synth knobs each engine actually HONORS live. Only these keys are accepted
# from [tts]; anything else (e.g. Turbo's inert exaggeration/cfg_weight/min_p) is
# dropped with a warning. proper-chatterbox keys are added
# when that EXPENSIVE engine lands.
_ENGINE_LIVE_KEYS: dict[str, frozenset[str]] = {
    "chatterbox-turbo": frozenset({"temperature", "top_p", "top_k", "repetition_penalty"}),
    "chatterbox": frozenset(
        {"temperature", "top_p", "top_k", "repetition_penalty", "exaggeration", "cfg_weight"}
    ),
}


def load_tts_baseline(engine: str) -> dict:
    """Read config/tts/<engine>/tts.toml [live] section → {knob: value}.

    Fail-soft: missing / empty / malformed ⇒ {} so behavior equals today (run_tts
    then passes no synth kwargs and generate() uses its own defaults). Only keys
    the engine honors live are kept; anything else is dropped (defends the
    byte-identical no-op: an [inert] section can never reach generate()).
    """
    path = TTS_DIR / engine / "tts.toml"
    allowed = _ENGINE_LIVE_KEYS.get(engine, frozenset())
    try:
        if not path.exists():
            return {}
        with open(path, "rb") as f:
            data = tomllib.load(f)
        live = data.get("live", {}) or {}
        return {k: v for k, v in live.items() if k in allowed}
    except Exception as exc:  # never let a bad baseline break startup wiring
        logger.warning("config_reload: bad tts baseline {} ({}) — using empty", path, type(exc).__name__)
        return {}


# In-code fallback for the [vad] tier = the params bot.py shipped with before the tier
# existed (its former inline VADParams literals). config/vad.toml [live] overlays this.
_VAD_FALLBACK: dict = {"confidence": 0.7, "start_secs": 0.2, "stop_secs": 0.5, "min_volume": 0.6}
_VAD_KEYS = frozenset(_VAD_FALLBACK)


def load_vad_baseline() -> dict:
    """config/vad.toml [live] overlaid on the in-code fallback → a COMPLETE param dict.

    Always returns all four keys — unlike the tts baseline ({} means "engine defaults
    apply implicitly"), the analyzer is constructed with explicit params, so bot.py
    seeds the analyzer AND the reloader from this same call; single-sourcing is what
    keeps the panel's rendered baseline and the running analyzer from ever diverging.
    Fail-soft: missing/malformed file ⇒ pure fallback; unknown/non-numeric keys dropped.
    """
    vals = dict(_VAD_FALLBACK)
    try:
        if VAD_TOML.exists():
            with open(VAD_TOML, "rb") as f:
                data = tomllib.load(f)
            live = data.get("live", {}) or {}
            for k, v in live.items():
                if k not in _VAD_KEYS:
                    logger.warning("config_reload: vad.toml [live].{} unknown — ignoring", k)
                elif isinstance(v, bool) or not isinstance(v, (int, float)):
                    logger.warning("config_reload: vad.toml [live].{} must be a number — ignoring", k)
                else:
                    vals[k] = float(v)
    except Exception as exc:  # never let a bad baseline break startup wiring
        logger.warning("config_reload: bad vad baseline {} ({}) — using fallback", VAD_TOML, type(exc).__name__)
    return vals


@dataclass
class ReloadDeltas:
    """What changed this turn. `llm` is the CHANGED llm keys (for a delta-mode
    settings frame); `tts` is the FULL desired synth dict (set_synth_params replaces
    wholesale) present only when it changed; `voice` is a resolved ref_wav path or None."""

    llm: dict = field(default_factory=dict)      # subset of {temperature, reasoning_effort, persona}
    tts: dict | None = None                       # full desired synth dict, or None if unchanged
    vad: dict | None = None                       # full desired VAD params (set_params replaces wholesale), or None
    voice: str | None = None                      # resolved abs ref_wav path, or None if unchanged

    def empty(self) -> bool:
        return not self.llm and self.tts is None and self.vad is None and self.voice is None


class ConfigReloader:
    """Poll config/overrides.toml at a turn boundary; return tier-routed deltas.

    Owns its own small mutable "applied" state (never mutates the frozen
    ActiveConfig). Seed it with the SAME baseline the live services were built from
    so an empty overrides.toml diffs to zero.
    """

    def __init__(
        self,
        *,
        engine: str,
        model_name: str,
        baseline_llm: dict,      # {temperature, reasoning_effort, persona}
        baseline_tts: dict,      # synth knobs (from load_tts_baseline); may be {}
        baseline_voice: str,     # ref_wav abs path
        baseline_vad: dict | None = None,  # COMPLETE vad params (load_vad_baseline); None ⇒ fallback
    ) -> None:
        self._engine = engine
        self._model_name = model_name
        self._live_keys = _ENGINE_LIVE_KEYS.get(engine, frozenset())

        # Immutable baseline (the persisted between-sessions config).
        self._baseline_llm = dict(baseline_llm)
        self._baseline_tts = dict(baseline_tts)
        self._baseline_voice = baseline_voice
        self._baseline_vad = dict(baseline_vad) if baseline_vad is not None else dict(_VAD_FALLBACK)

        # Mutable "currently applied" state; starts == baseline (turn-1 no-op).
        self._applied_llm = dict(baseline_llm)
        self._applied_tts = dict(baseline_tts)
        self._applied_voice = baseline_voice
        self._applied_vad = dict(self._baseline_vad)

        self._last_mtime: float | None = None
        self._bad_mtime: float | None = None  # suppress repeat logging of the same broken file

    # ── poll ──────────────────────────────────────────────────────────────────

    def poll(self) -> ReloadDeltas | None:
        """Return tier-routed deltas, or None if nothing changed / unparseable.

        Layer-1 no-op: an unchanged (or absent) overrides.toml never gets parsed.
        Layer-2 no-op: a semantically-empty file diffs to zero deltas.
        Fail-soft: any parse/validation error logs once (keyed on mtime) and returns
        None, keeping the last-good applied state.
        """
        try:
            mtime = os.stat(OVERRIDES_TOML).st_mtime
        except FileNotFoundError:
            # Absent ⇒ no live layer. Reset so a later re-creation is re-read.
            self._last_mtime = None
            return None
        except OSError as exc:
            logger.warning("config_reload: cannot stat {} ({}) — skipping", OVERRIDES_TOML, exc)
            return None

        if self._last_mtime is not None and mtime == self._last_mtime:
            return None  # Layer-1: unchanged → no re-parse, no allocation.
        self._last_mtime = mtime

        try:
            with open(OVERRIDES_TOML, "rb") as f:
                data = tomllib.load(f)
            deltas = self._diff(data)
        except Exception as exc:
            if self._bad_mtime != mtime:  # log once per broken revision
                logger.warning(
                    "config_reload: overrides.toml rejected ({}: {}) — keeping last-good",
                    type(exc).__name__, exc,
                )
                self._bad_mtime = mtime
            return None

        self._bad_mtime = None
        return None if deltas.empty() else deltas

    # ── diff (baseline ⊗ overrides vs applied) ─────────────────────────────────

    def _diff(self, data: dict) -> ReloadDeltas:
        d = ReloadDeltas()

        # LLM — desired = baseline overlaid with valid override keys; delta = changed.
        ov_llm = data.get("llm", {}) or {}
        if "system_instruction" in ov_llm:
            logger.warning(
                "config_reload: [llm].system_instruction is not a live key — use "
                "[llm].persona so the template hard-rules stay pinned; ignoring."
            )
        desired_llm = dict(self._baseline_llm)
        if "temperature" in ov_llm:
            desired_llm["temperature"] = float(ov_llm["temperature"])
        if "reasoning_effort" in ov_llm:
            desired_llm["reasoning_effort"] = str(ov_llm["reasoning_effort"])
        if "persona" in ov_llm:
            desired_llm["persona"] = str(ov_llm["persona"])
        for k, v in desired_llm.items():
            if v != self._applied_llm.get(k):
                d.llm[k] = v

        # TTS — desired = baseline overlaid with valid (honored) override knobs.
        ov_tts = data.get("tts", {}) or {}
        desired_tts = dict(self._baseline_tts)
        for k, v in ov_tts.items():
            if k not in self._live_keys:
                logger.warning(
                    "config_reload: [tts].{} is inert/unknown for engine {} — ignoring.",
                    k, self._engine,
                )
                continue
            desired_tts[k] = v
        if desired_tts != self._applied_tts:
            d.tts = desired_tts

        # VAD — desired = baseline overlaid with valid override knobs; the delta is the
        # FULL desired dict (set_params replaces wholesale, like set_synth_params).
        ov_vad = data.get("vad", {}) or {}
        desired_vad = dict(self._baseline_vad)
        for k, v in ov_vad.items():
            if k not in _VAD_KEYS:
                logger.warning("config_reload: [vad].{} is unknown — ignoring.", k)
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                logger.warning("config_reload: [vad].{} must be a number — ignoring.", k)
                continue
            desired_vad[k] = float(v)
        if desired_vad != self._applied_vad:
            d.vad = desired_vad

        # voice — desired = baseline unless a valid ref_wav override resolves.
        ov_voice = data.get("voice", {}) or {}
        desired_voice = self._baseline_voice
        if "ref_wav" in ov_voice:
            resolved = self._resolve_ref(str(ov_voice["ref_wav"]))
            if resolved is not None:
                desired_voice = resolved
        if desired_voice != self._applied_voice:
            d.voice = desired_voice

        return d

    @staticmethod
    def _resolve_ref(ref: str) -> str | None:
        """Resolve a ref_wav path (repo-relative → absolute) fail-soft. Missing ⇒
        None (logged; current voice kept). Mirrors config_loader.load_voice's
        resolution but as a soft skip rather than a raise."""
        p = Path(ref).expanduser()
        if not p.is_absolute():
            p = config_loader._ROOT / p
        p = p.resolve()
        if not p.exists():
            logger.warning("config_reload: ref_wav not found: {} — keeping current voice.", p)
            return None
        return str(p)

    # ── build the LLM settings delta (persona → recomposed system_instruction) ──

    def build_llm_delta(self, delta_llm: dict) -> LLMSettings:
        """Turn a changed-llm-keys dict into a pipecat LLMSettings delta.

        temperature → typed field; reasoning_effort → extra (it is NOT a typed
        LLMSettings field); persona → recomposed system_instruction
        via the MODEL template (hard-rules pinned by construction)."""
        kwargs: dict = {}
        extra: dict = {}
        if "temperature" in delta_llm:
            kwargs["temperature"] = delta_llm["temperature"]
        if "reasoning_effort" in delta_llm:
            extra["reasoning_effort"] = delta_llm["reasoning_effort"]
        if "persona" in delta_llm:
            kwargs["system_instruction"] = config_loader.compose_with_persona(
                self._model_name, delta_llm["persona"]
            )
        if extra:
            kwargs["extra"] = extra
        return LLMSettings(**kwargs)

    # ── commit (advance applied state after a successful apply) ─────────────────

    def commit_llm(self, delta_llm: dict) -> None:
        self._applied_llm.update(delta_llm)

    def commit_tts(self, desired_tts: dict) -> None:
        self._applied_tts = dict(desired_tts)

    def commit_vad(self, desired_vad: dict) -> None:
        self._applied_vad = dict(desired_vad)

    def commit_voice(self, path: str) -> None:
        self._applied_voice = path


class ConfigReloadProcessor(FrameProcessor):
    """Fires the reloader once per turn (on LLMContextFrame) and routes deltas.

    Mirrors the MuteGate/SpeakingTap shape (control_taps.py): override process_frame,
    call super(), do work, push_frame. On the turn's LLMContextFrame:
      1. LLM deltas → push LLMUpdateSettingsFrame BEFORE forwarding the context
         frame, so the change lands on THIS turn (frame-ordering proven by T3).
      2. TTS synth deltas → tts.set_synth_params(full desired dict).
      3. VAD deltas → vad_analyzer.set_params(VADParams(**full desired)) — an instant
         threshold swap (safe mid-stream; the analyzer reads params per frame), applied
         here at the turn boundary so listening feel never shifts mid-utterance.
      4. voice delta → tts.set_ref_wav(path); await the re-prepare BEFORE forwarding
         (so the turn's first run_tts sees the new conditionals) — masked under LLM
         think-time (~0.2s < ~0.55s TTFT).
    Every non-context frame is forwarded untouched.
    """

    def __init__(self, reloader: ConfigReloader, tts, vad_analyzer=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._reloader = reloader
        self._tts = tts
        self._vad_analyzer = vad_analyzer  # None ⇒ vad deltas are logged and skipped

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            try:
                deltas = self._reloader.poll()
            except Exception as exc:  # reloader is fail-soft, but never trust the loop to one try
                logger.warning("config_reload: poll raised ({}) — skipping this turn", type(exc).__name__)
                deltas = None

            if deltas is not None:
                # HIDEABLE / voice — apply and AWAIT before the turn's first run_tts.
                if deltas.voice is not None:
                    await self._apply_voice(deltas.voice)
                # FREE / TTS — dict ref swap (no frame, no await).
                if deltas.tts is not None:
                    try:
                        self._tts.set_synth_params(deltas.tts)
                        self._reloader.commit_tts(deltas.tts)
                        logger.info("config_reload: TTS synth params → {}", deltas.tts)
                    except Exception as exc:
                        logger.warning("config_reload: set_synth_params failed ({})", type(exc).__name__)
                # FREE / VAD — instant threshold swap on the analyzer (no frame, no await).
                if deltas.vad is not None:
                    if self._vad_analyzer is None:
                        logger.warning("config_reload: vad delta but no analyzer wired — ignoring")
                    else:
                        try:
                            self._vad_analyzer.set_params(VADParams(**deltas.vad))
                            self._reloader.commit_vad(deltas.vad)
                            logger.info("config_reload: VAD params → {}", deltas.vad)
                        except Exception as exc:
                            logger.warning("config_reload: set_params failed ({})", type(exc).__name__)
                # FREE / LLM — push settings delta BEFORE the context frame (T3 ordering).
                if deltas.llm:
                    try:
                        settings = self._reloader.build_llm_delta(deltas.llm)
                        await self.push_frame(
                            LLMUpdateSettingsFrame(delta=settings), FrameDirection.DOWNSTREAM
                        )
                        self._reloader.commit_llm(deltas.llm)
                        logger.info("config_reload: LLM settings → {}", list(deltas.llm))
                    except Exception as exc:
                        logger.warning("config_reload: LLM delta failed ({})", type(exc).__name__)

        await self.push_frame(frame, direction)

    async def _apply_voice(self, path: str) -> None:
        """Submit prepare_conditionals on the TTS executor and await it (masked
        under LLM think-time). Fail-soft: a failure keeps the current voice."""
        import asyncio
        try:
            fut = self._tts.set_ref_wav(path)
            await asyncio.wrap_future(fut)
            self._reloader.commit_voice(path)
            logger.info("config_reload: voice ref_wav → {}", path)
        except Exception as exc:
            logger.warning("config_reload: set_ref_wav failed ({}) — keeping current voice", type(exc).__name__)
