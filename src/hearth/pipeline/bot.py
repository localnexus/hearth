"""
Hearth — fully-local voice-conversation pipeline (in-process TTS).

Architecture:
    mic ─▶ Silero VAD ─▶ MLX-Whisper-turbo ─▶ OpenAI-compatible LLM ─▶ Chatterbox-Turbo(in-process) ─▶ speaker
           (in LLMUserAggregatorParams)       :8080/v1 (llama-server)    mlx-audio / MLXAudioTTSService
    barge-in: SileroVADAnalyzer inside LLMUserAggregatorParams triggers InterruptionFrame automatically.

Endpoints:
    LLM       : http://127.0.0.1:8080/v1   (llama-server default; any OpenAI-compatible server —
                override with LM_BASE_URL, and LM_API_TOKEN if the server wants a key)
    TTS       : in-process via MLXAudioTTSService (Chatterbox-Turbo fp16, 24 kHz, default voice)

Usage:
    cd <repo root>          # wherever this tree lives — no absolute path is assumed
    .venv/bin/python -m hearth.pipeline.bot

    Prefer ./start.sh: it runs preflight first and resolves its own root, so it works
    from any account/location.
"""

# ─── STABLE CORE ────────────────────────────────────────────────────────────────
# This is the pipeline-assembly + entry core. Build new functionality in a SIBLING
# module and integrate with the minimal edit here.
# Sanctioned seams:  • the feature-import list (activate a module by importing it)
#                    • minimal wiring in build_pipeline() / main()
# (A processor registry — so new pipeline STAGES self-register instead of being
#  hand-wired into build_pipeline's Pipeline([...]) list — is deferred until a
#  feature needs it; until then, wire minimally and leave a note.)
# ────────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

# ── Privacy: full HF Hub airgap BEFORE any HF-backed import ───────────────────
# HF libs (huggingface_hub, via transformers / mlx-audio / mlx-whisper) otherwise
# reach huggingface.co on every model load — an analytics ping AND per-file cache-
# revision HEAD requests. Both are outbound (IP + which models + timestamp), neither
# benefits a pinned local stack. Weights are cached (T7 gate met), so airgap them:
#   HF_HUB_OFFLINE=1  → zero outbound; weights frozen at what's on disk.
#   HF_HUB_DISABLE_TELEMETRY=1 → belt-and-suspenders (kills the ping even if online).
# To deliberately refresh a model, run:  HF_HUB_OFFLINE=0 ./start.sh
# (setdefault respects that override; on a genuine cache-miss the bot fails loudly at
#  load — rerun once with HF_HUB_OFFLINE=0 to re-fetch, then it's cached again.)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DISABLE_TELEMETRY", "1")  # legacy alias for older HF code paths

# ── Pipecat 1.4.0 imports (verified against installed version) ────────────────
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker, PipelineParams
from pipecat.workers.runner import WorkerRunner
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.tts_service import TTSService
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from loguru import logger
from hearth.tts.params import SAMPLE_RATE  # engine-owned output rate (backend-neutral module)
from hearth.stt.stt_service import MLXWhisperSTTService
from hearth.control.control import start_web_server
from hearth.control.engine_probe_llamaserver import fetch_engine_info_for
from hearth.control.control_taps import MuteGate, SpeakingTap
from hearth.session.session_cli import resolve_session
from hearth.recording.recording import (
    MicRecordTap,
    Recorder,
    TTSRecordTap,
    _slug as _capture_slug,
    repair_routing as recording_repair_routing,
)
from hearth.measurement.measurement_taps import (
    MEASURE_ENABLED,
    MeasurementLog,
    MeasureObserver,
    MeasureTapA,
    MeasureTapB,
    TurnState,
)
from hearth.session.token_meter import TokenMeter
from hearth.session import compact_trigger, maintenance_lock, session_store
from hearth.config import config_loader
from hearth.config import config_reload
from hearth.bridges import openclaw_bridge
from hearth import serve
from hearth import memory as hearth_memory
from hearth.pipeline import switcher as switcher_mod
from hearth.pipeline import memory_prefetch as memory_prefetch_mod

# ── L2 panel features (activation = import; registers routes on control_routes) ──
# Each import runs the module's @register side effect, adding its routes to the web
# panel when start_web_server() fans out over control_routes.contributors(). With none
# imported the panel is core-only (control.py). See control_routes.py.
# config_profiles pulls in config_knobs, so this ONE line lights up both:
#   /config          — live hot knobs (features/config_knobs.py)
#   /config/profiles — per-character / per-voice presets (features/config_profiles.py)
import hearth.control.features.config_profiles  # noqa: F401
# The manual contributor — destinations rail + in-page manual pane:
#   /manual/*        — users-manual/ pages rendered locally (features/manual.py)
import hearth.control.features.manual  # noqa: F401

#   /companion/*     — companion switcher: relay to the supervisor daemon (features/
#                      companion.py); inert unless [serve.supervisor] is enabled AND
#                      the panel binds loopback — see the module docstring.
import hearth.control.features.companion  # noqa: F401
#   /switch/live     — the bot half of the LIVE companion switch (features/
#                      live_switch.py): the supervisor's /admin/switch hands a
#                      bundle here when every changed piece has a live path;
#                      routes answer 503 until main() attaches the pipeline's
#                      LiveSwitcher below.
import hearth.control.features.live_switch  # noqa: F401
#   /memory          — read-only memory status tap (features/memory_status.py):
#                      mode + the current seam's backend/gates/recall attribution;
#                      DISPLAY only per the write-layer rule (c) — answers 503
#                      until main() attaches the switcher below.
import hearth.control.features.memory_status  # noqa: F401
#   /turn            — the last-reply echo above the compose box (features/
#                      turn_echo.py): a read of the LLMContext this process
#                      already holds, so it needs no tap and no attach call.
import hearth.control.features.turn_echo  # noqa: F401
# Already pulled in by config_profiles; imported explicitly because bot core calls its
# startup override-scrub below (remove that call too if these feature imports ever go).
import hearth.control.features.config_knobs  # noqa: F401

# ── Configuration ─────────────────────────────────────────────────────────────
# The live selection (character + model + voice) is externalized to config files.
# Loaded ONCE here, pre-runtime, from config/active.toml → model.toml + voice descriptor +
# the two-layer prompt render. A missing/malformed config file fails fast with a
# ConfigError naming the exact file (config_loader) — no silent fallback to
# literals. To change who/which-engine is live: edit config/active.toml + restart.
_CFG = config_loader.load_active()

# LLM endpoint — any OpenAI-compatible server. Default = llama-server on its default
# port, no API key. Override via env:
#   LM_BASE_URL   e.g. http://127.0.0.1:1234/v1 for LM Studio
#   LM_API_TOKEN  bearer key, only if the server requires one (llama-server --api-key,
#                 LM Studio's token). Unset ⇒ "none" is sent; keyless servers ignore it.
LM_BASE_URL = os.environ.get("LM_BASE_URL", "http://127.0.0.1:8080/v1")
LM_API_TOKEN = os.environ.get("LM_API_TOKEN") or "none"

# Engine-probe backend selector for the control panel: "llama-server" (default — the
# /props adapter in hearth/control/engine_probe_llamaserver.py) or "lmstudio" (the
# /api/v0/models probe). Rides the same env-config idiom as LM_BASE_URL / LM_API_TOKEN —
# the LM endpoint's home in this spine — so config_loader/ActiveConfig stay untouched.
LM_PROVIDER = os.environ.get("LM_PROVIDER", "llama-server")

# LLM: the model id comes from config/models/<active-model>/model.toml (.id),
# selected via config/active.toml.
# ⚠️ HYBRID thinking models emit chain-of-thought into `reasoning_content`, leaving
# spoken `content` empty until they finish, which starves the TTS stage and stalls
# the voice loop. Thinking is forced OFF via `reasoning_effort` (model.toml, fed into
# the LLM `extra` below). On some LM Studio builds that is the ONLY body field that
# reaches the jinja `enable_thinking` var — `chat_template_kwargs` / top-level
# `enable_thinking` are silently ignored; such a build also needs the persistent
# LM Studio Prompt-Template thinking-off edit ({%- set enable_thinking = false %}) —
# see model.toml.needs_template_edit. Hybrid architectures can also block KV-cache
# reuse; a full-attention build caches normally.
LM_MODEL = _CFG.model_id

# Voice tag for the persona (the cloned voice profile). Recorded in the session
# sidecar so a resume can warn if reloaded against a different voice. Human tag,
# not the reference-clip filename. Sourced from the active voice descriptor
# (characters/<char>/voices/<voice>.toml → .tag).
VOICE_TAG = _CFG.voice_tag

# Active TTS engine. Hardcoded this pass; the EXPENSIVE engine-swap seam reads it
# from active.toml.tts_engine when a second engine lands.
# Names the config/tts/<engine>/ baseline subtree + the reloader's live-key set.
TTS_ENGINE = "chatterbox-turbo"

# ── Persona / system prompt (hoisted to module level) ─────────────────────────
# Injected per-request from LLM settings (NOT stored in context.messages), so it
# is never persisted with a session and never duplicated on resume. Hoisted here
# so session continuity can hash it (prompt_sha256) for drift detection.
# The prompt is COMPOSED at load time from two externalized layers: the MODEL template
# (config/models/<model>/system-prompt-template.md — envelope + hard rules) with its
# {{persona}} slot filled from the active CHARACTER
# (characters/<char>/persona.md — ## IDENTITY then ## SOUL). The persona/template
# envelope is stable across sessions; drift is measured on PROMPT_FINGERPRINT (below),
# not on the rendered prompt. To iterate the persona, edit persona.md; to reshape
# output rules or model-whispering, edit the template. ⚠️ Any envelope edit (persona
# or template, excluding the {{datetime}} clock) changes prompt_sha256 and warns on
# resume of pre-edit sessions (by design).
#
# system_instruction now also carries a one-time {{datetime}} clock (session start),
# so it varies every run. Drift detection therefore hashes PROMPT_FINGERPRINT (the
# datetime-free composition), NOT SYSTEM_INSTRUCTION — see session_cli.resolve_session.
SYSTEM_INSTRUCTION = _CFG.system_instruction
PROMPT_FINGERPRINT = _CFG.prompt_fingerprint

# ── T4 latency instrumentation flag (opt-in; inert unless T4_METRICS=1) ────────
# The marker helper (_t4_mark) and the STT service that emits the markers now live
# in stt_service.py. main() still reads this flag to drive pipecat's per-processor
# metrics + TokenMeter verbosity; stt_service reads the same env var independently
# (each module reads config — no import coupling for the flag).
T4_METRICS = os.environ.get("T4_METRICS", "0") == "1"


# ── Pipeline assembly ──────────────────────────────────────────────────────────


async def build_pipeline(
    dump_dir: Optional[str] = None,
    resume_messages: Optional[list] = None,
    store: Optional["session_store.SessionStore"] = None,
    memory_mode: str = "full",
):
    """
    Construct the fully-local v2 voice pipeline.

    Pipeline order:
        transport.input()
        → mute_gate    (MuteGate: drops InputAudioRawFrame when muted — before VAD)
        → vad          (VADProcessor: Silero VAD → VADUser{Started,Stopped}SpeakingFrame)
        → stt          (MLX-Whisper VAD-segmented chunks → TranscriptionFrame)
        → user_agg     (aggregates transcriptions; turn-taking + barge-in)
        → llm          (OpenAILLMService pointing at your OpenAI-compatible server)
        → tts          (MLXAudioTTSService, sentence-at-a-time, streaming chunks)
        → transport.output()
        → speaking_tap (SpeakingTap: tracks BotStarted/StoppedSpeakingFrame for /say)
        → assistant_agg (collects LLM tokens → writes to context after full response)
    """
    # Transport (mic + speaker)
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=SAMPLE_RATE,
        )
    )

    # VAD (single source): a VADProcessor upstream of STT emits
    # VADUserStartedSpeaking/StoppedSpeaking frames. These drive BOTH:
    #   • SegmentedSTTService — buffers audio per segment, transcribes on stop
    #   • the user aggregator's UserTurnController — turn-taking + barge-in
    # (LocalAudioTransport does NOT do VAD in this pipecat build, and the
    #  aggregator's own analyzer would sit downstream of STT — too late to
    #  segment it. So VAD must live here, before STT.)
    # Params come from config/vad.toml [live] over the in-code fallback (the former
    # inline literals) — the CALIBRATION tier. The analyzer is held in a
    # named var so the reloader can retune it live via set_params() at turn boundaries;
    # seeding analyzer + reloader from the SAME load call keeps panel and reality equal.
    vad_baseline = config_reload.load_vad_baseline()
    vad_analyzer = SileroVADAnalyzer(params=VADParams(**vad_baseline))
    vad = VADProcessor(vad_analyzer=vad_analyzer)

    # STT (loads MLX-Whisper model into memory at construction)
    # Pass fully-initialised settings to silence STTSettings NOT_GIVEN validator
    # warnings (model and language are not runtime-configurable for this service).
    stt = MLXWhisperSTTService(
        settings=STTSettings(model=None, language=None),
    )

    # Memory seam (cross-session continuity): activation = config presence
    # (config/memory.toml enabled=true); absent/disabled ⇒ None and the composed
    # prompt is byte-identical. Enabled, recall runs ONCE here at session start
    # (never on the per-turn path) and appends a dated, provenance-framed block
    # AFTER the persona render — PROMPT_FINGERPRINT is computed memory-free in
    # config_loader, so drift detection and resume warnings stay stable.
    # memory_mode is this SITTING's posture (--memory): full / recall-only
    # (recall as normal, nothing retained) / off (no seam even when enrolled).
    if memory_mode != "full":
        print(f"[memory] session memory mode: {memory_mode}", flush=True)
        if config_loader.load_memory_config() is None:
            print(f"[memory] --memory {memory_mode}: memory is not enabled "
                  "(config/memory.toml) — the flag has no effect", flush=True)
    memory_seam = hearth_memory.maybe_attach(
        _CFG.character, persona=_CFG.persona_name, mode=memory_mode)
    system_instruction = (
        memory_seam.augment(SYSTEM_INSTRUCTION) if memory_seam else SYSTEM_INSTRUCTION
    )

    # LLM (OpenAI-compatible endpoint — llama-server by default)
    llm = OpenAILLMService(
        base_url=LM_BASE_URL,
        api_key=LM_API_TOKEN,
        settings=OpenAILLMService.Settings(
            model=LM_MODEL,
            system_instruction=system_instruction,
            # temperature + reasoning_effort sourced from the active model.toml
            # (was 0.7 / "none" hardcoded). model.toml pins today's values, so the
            # request body is byte-identical.
            temperature=_CFG.temperature,
            # Force Qwen3.6 hybrid thinking OFF — see LM_MODEL note above. This merges
            # into the top-level chat-completions params (OpenAILLMSettings.extra).
            extra={"reasoning_effort": _CFG.reasoning_effort},
        ),
    )

    # Persisted synth-knob baseline for the active engine (config/tts/<engine>/tts.toml
    # [live]). Empty {} if the file is absent — then run_tts passes no synth kwargs, exactly
    # as before live-config. The shipped Turbo baseline equals generate()'s own defaults, so
    # seeding it is behaviorally identical (machine-checked in tests/test_config_reload.py).
    # The reloader below is seeded from this SAME dict so an empty overrides.toml diffs to zero.
    tts_baseline = config_reload.load_tts_baseline(TTS_ENGINE)

    # TTS (Chatterbox-Turbo in-process, default voice, 24 kHz streaming)
    # Pass fully-initialised settings to silence TTSSettings NOT_GIVEN validator
    # warnings (model/voice/language are baked into the service at init; not
    # runtime-configurable via settings frames for this service).
    # Import deferred to construction (not module scope) so the base install —
    # no [mac] extra — stays importable on any host: this module pulls mlx.core.
    try:
        from hearth.tts.mlx_tts_service import MLXAudioTTSService
    except ImportError as exc:
        raise RuntimeError(
            "in-process TTS needs the MLX speech chain — install with the [mac] "
            "extra on Apple Silicon (gold tier); hearth[cuda] is not built yet"
        ) from exc
    tts = MLXAudioTTSService(
        settings=TTSSettings(model=None, voice=None, language=None),
        # ref_wav now comes from the active voice descriptor
        # (characters/<char>/voices/<voice>.toml → .ref_wav). Previously bot.py passed
        # nothing here and the service rode its built-in module default;
        # the descriptor path is byte-identical, so prepare_conditionals() clones the
        # same clip → same voice. No TTS signature change (__init__ already accepts ref_wav).
        ref_wav=_CFG.ref_wav,
        synth_params=tts_baseline,  # live-config baseline (empty ⇒ engine defaults)
        dump_dir=dump_dir,  # None unless --dump-tts; captures each utterance to WAV
    )

    # Context + aggregators (VAD goes HERE, not in transport params)
    # No seed/greeting message: an auto-greeting at startup races with the first
    # audio frames (Whisper can hallucinate a "turn" from startup noise and
    # immediately barge-in-cancel the greeting). The user speaks first.
    #
    # Session continuity (Tier 1): on --resume we reload the prior message list into
    # a FRESH context. The system prompt is NOT in these messages (it is re-injected
    # by the LLM ctor above), so there is no duplication. No auto-greeting either way
    # — the voice stays silent until spoken to, then answers WITH the reloaded memory.
    if resume_messages:
        context = LLMContext(messages=resume_messages)
    else:
        context = LLMContext()

    # OpenClaw dispatch bridge: the voice model's
    # narrow "hands" — dispatch_task/check_tasks via the local gateway. Activation
    # = config presence (config/openclaw.toml enabled=true); absent/disabled ⇒
    # returns None having registered nothing, behavior byte-identical.
    openclaw_bridge.maybe_attach(llm, context)

    # No vad_analyzer here — the upstream VADProcessor is the sole VAD source.
    # The aggregator's UserTurnController consumes the VAD frames it emits.
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(),
    )

    # Tier-1 persistence hook: on_assistant_turn_stopped fires AFTER the assistant
    # message is committed to context (llm_response_universal.py:1571, 2028) and the
    # paired user message is already in context — so it sees a COMPLETE exchange.
    # One hook covers both spoken turns and /say text turns (identical path). We
    # snapshot the whole context (system prompt excluded) atomically every turn.
    if store is not None:
        @assistant_agg.event_handler("on_assistant_turn_stopped")
        async def _persist_turn(_aggregator, _message):  # _message: AssistantTurnStoppedMessage
            try:
                # via the switcher: a live companion switch repoints the store.
                live_switcher.snapshot(context.messages)
            except Exception as exc:  # noqa: BLE001 — persistence must never break the loop
                logger.warning("[session] snapshot failed ({}) — continuing", type(exc).__name__)

    # Step 3: MuteGate before VAD (drops InputAudioRawFrame when muted so VAD
    # sees nothing → no barge-in, no half-open turns while muted).
    mute_gate = MuteGate()

    # Step 3: SpeakingTap after TTS output (watches BotStarted/StoppedSpeaking
    # so /say can decide whether to prepend an InterruptionFrame).
    speaking_tap = SpeakingTap()

    # Session recording. Two passive taps + a Recorder driven by the panel's Record
    # button. Disarmed →
    # byte-identical pass-throughs (the measure-tap contract). Captures land in the
    # companion's own directory under the data root (characters/<name>/captures/ —
    # sensitive, local-only, never the engine tree). The mic tap
    # sits AFTER mute_gate on purpose: Mute is honored — muted audio never touches
    # disk. The TTS tap sits before transport.output() so it sees every
    # TTSAudioRawFrame at the native 24 kHz.
    recorder = Recorder(
        character=_CFG.character,
        base_dir=config_loader.companion_state_dir(_CFG.character, "captures"),
        tts_rate=SAMPLE_RATE,
        mic_rate=16000,
    )
    mic_record_tap = MicRecordTap(recorder)
    tts_record_tap = TTSRecordTap(recorder)

    # Speculative-prefill MEASUREMENT GATE (log-only, CONTENT-FREE; see plan/005-Measurement.md).
    # Two passive pass-through taps + one observer, all sharing a MeasurementLog and an
    # in-memory TurnState. TapA/B observe turn structure (between stt/user_agg and user_agg/llm);
    # the observer captures LLM TTFB + prompt_tokens (the felt-latency-vs-context curve). Only
    # telemetry/booleans/lengths are written — no transcript. All no-op when MEASURE_ENABLED is
    # off (taps pass through; observer not attached below) → loop behaves byte-identically.
    _measure_log = MeasurementLog() if MEASURE_ENABLED else None
    _measure_turn = TurnState() if MEASURE_ENABLED else None
    measure_a = MeasureTapA(_measure_log, _measure_turn)
    measure_b = MeasureTapB(_measure_log, _measure_turn)
    measure_observer = MeasureObserver(_measure_log) if MEASURE_ENABLED else None

    # Live-config reload (FREE + HIDEABLE tiers). Seeded with the SAME baseline the
    # LLM + TTS were built from, so an empty overrides.toml diffs to zero (byte-identical
    # no-op). Polls config/overrides.toml by mtime at each turn boundary (on the
    # LLMContextFrame) and routes deltas: LLM via LLMUpdateSettingsFrame pushed AHEAD of
    # the context frame (lands this turn — T3-verified), TTS synth knobs via
    # tts.set_synth_params(), voice via tts.set_ref_wav(). Fail-soft: a malformed override
    # never crashes the loop. The persona baseline is the {{persona}} slot body so a live
    # persona override re-composes through the template (hard rules stay pinned).
    # [voice].ref_wav is SESSION-SCOPED — a panel
    # sample switch lives until shutdown; between sessions active.toml owns
    # who-sounds-how. Scrub BEFORE the reloader is seeded so a stale override from
    # a prior session can never re-apply at the first turn boundary.
    hearth.control.features.config_knobs.scrub_session_scoped()

    _reloader = config_reload.ConfigReloader(
        engine=TTS_ENGINE,
        model_name=_CFG.model_name,
        baseline_llm={
            "temperature": _CFG.temperature,
            "reasoning_effort": _CFG.reasoning_effort,
            "persona": config_loader.compose_persona(_CFG.character),
        },
        baseline_tts=tts_baseline,
        baseline_voice=_CFG.ref_wav,
        baseline_vad=vad_baseline,
    )
    # The LIVE companion switch. Owns the CURRENT session's
    # store/seam (the shutdown path reads them back from here so a live switch
    # is honored at stop), arms intents POSTed on /switch/live, and applies
    # them at the turn boundary via the processor below (which also rebases
    # the reloader onto the new companion's baselines).
    live_switcher = switcher_mod.LiveSwitcher(
        active=_CFG, reloader=_reloader, tts=tts, context=context,
        store=store, seam=memory_seam, memory_mode=memory_mode,
        lm_provider=LM_PROVIDER, lm_base_url=LM_BASE_URL, lm_token=LM_API_TOKEN,
        # The sitting's memory mode rides a live switch: the incoming
        # companion attaches under the SAME mode (off ⇒ no seam), and the
        # outgoing side's finalize suppresses itself via its own retain flag.
        seam_factory=lambda character, persona: hearth_memory.maybe_attach(
            character, persona=persona, mode=memory_mode),
    )
    config_reload_proc = config_reload.ConfigReloadProcessor(
        _reloader, tts, vad_analyzer=vad_analyzer, switcher=live_switcher)

    # Voice-lane per-turn recall (lane (b) voice stroke, prefetch-behind). Wired
    # ONLY when the seam is present and BOTH [memory.per_turn].enabled and .voice
    # are on — the chat gate alone never lights the voice loop. Placed after the
    # reload/switch processor so it reads the switcher's CURRENT seam + base and
    # rebases on a live switch. Absent ⇒ the pipeline is byte-identical to before.
    memory_prefetch_proc = None
    if (memory_seam is not None and getattr(memory_seam, "per_turn_enabled", False)
            and getattr(memory_seam, "per_turn_voice", False)):
        memory_prefetch_proc = memory_prefetch_mod.MemoryPrefetch(
            switcher=live_switcher, context=context)

    # Assemble pipeline
    pipeline = Pipeline([
        transport.input(),
        mute_gate,
        mic_record_tap,      # M7 RECORD (passive, armed via panel): mic stem — after the gate → Mute honored
        vad,
        stt,
        measure_a,           # MEASURE (log-only, gated): pre-finalization signal
        user_agg,
        measure_b,           # MEASURE (log-only, gated): finalized turn text
        config_reload_proc,  # LIVE-CONFIG: poll overrides.toml at the turn boundary
        *( [memory_prefetch_proc] if memory_prefetch_proc is not None else [] ),  # voice per-turn recall (gated)
        llm,
        tts,
        tts_record_tap,      # M7 RECORD (passive, armed via panel): the companion's voice, native 24 kHz
        transport.output(),
        speaking_tap,
        assistant_agg,
    ])

    # memory_prefetch_proc rides out because main() reports it to the panel tap
    # (voice_prefetch_built) — it is built here, so it can only be known there.
    return (pipeline, transport, context, mute_gate, speaking_tap, measure_observer,
            recorder, memory_seam, live_switcher, system_instruction,
            memory_prefetch_proc)


async def main(
    dump_dir: Optional[str] = None,
    store: Optional["session_store.SessionStore"] = None,
    resume_messages: Optional[list] = None,
    session_descriptor: Optional[str] = None,
    memory_mode: str = "full",
):
    """Entry point for the live-mic voice loop."""
    # Heal any LEAKED output mirror BEFORE the pipeline is constructed.
    # PortAudio snapshots the device list when the transport is built — repairing
    # after that leaves it holding a stale default-device reference and the
    # output stream fails to open ('!obj'/-9986, the companion's voice dead for the session).
    # Must be the FIRST audio-touching act.
    await recording_repair_routing()

    (pipeline, transport, context, mute_gate, speaking_tap, measure_observer,
     recorder, memory_seam, live_switcher, system_instruction,
     memory_prefetch_proc) = await build_pipeline(
        dump_dir, resume_messages=resume_messages, store=store,
        memory_mode=memory_mode,
    )

    # TokenMeter captures LM Studio's own per-turn usage block (ground truth).
    # Per-turn lines print only when verbose (T4_METRICS); the shutdown summary
    # and any reasoning-leak warning always print.
    meter = TokenMeter(verbose=T4_METRICS)
    # Seed the runway gauge: pre-fill (system prompt + memory block + any resumed
    # transcript) is real context the server won't report until the first turn.
    meter.prime_estimate(system_instruction, context.messages)
    live_switcher.attach_meter(meter)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=SAMPLE_RATE,
            # T4: pipecat's own per-processor TTFB/processing metrics, opt-in.
            # MEASURE mode needs per-processor TTFB (LLM prefill→first-token) for the
            # felt-latency-vs-context curve (M5), so force it on during measurement.
            enable_metrics=T4_METRICS or MEASURE_ENABLED,
            # Token capture is intentionally independent of the latency
            # instrumentation flag: TokenMeter needs usage metrics unconditionally.
            enable_usage_metrics=True,
        ),
        observers=[meter] + ([measure_observer] if measure_observer else []),
        # Appliance: don't self-terminate during a conversational pause. pipecat
        # cancels the worker after idle_timeout_secs (~5 min) of no speech frames
        # by default; for a live loop the user may simply be thinking. Keep the
        # loop alive until explicitly stopped (RUNBOOK §3).
        cancel_on_idle_timeout=False,
    )

    # No initial greeting turn injected — the user starts the conversation.
    # (LLMRunFrame kick removed; it produced an auto-greeting that raced with
    # startup-noise transcriptions and got barge-in cancelled.)

    # Phase 1 status block: fetch engine facts (context capacity + model id) at
    # startup via an authenticated /api/v0/models call, then keep them honest with
    # the slow re-poll task below. bot.py owns the base_url + token; control.py
    # exposes the pure fetch helper (no circular import). Fully graceful — any
    # failure yields all-None → panel shows '—'.
    engine_info = await fetch_engine_info_for(LM_PROVIDER, LM_BASE_URL, LM_API_TOKEN, LM_MODEL)
    # Piggyback the session descriptor onto the same static-facts dict: it's
    # resolved once at startup (New / Restored / <held-name>) and rides the
    # existing /engine route + one-shot fetch. "New" is the safe default.
    engine_info["session"] = session_descriptor or "New"
    # The sitting's memory posture, as EFFECTIVE state: the mode when the seam
    # is attached (or deliberately "off"); None when memory simply isn't
    # configured — the panel dashes rather than implying a bank exists.
    engine_info["memory_mode"] = (
        memory_mode if (memory_seam is not None or memory_mode == "off") else None
    )
    # Also piggyback the active persona identity (character + voice), resolved once
    # from config at startup (_CFG). Rides the same /engine route → the panel's
    # "Agent | Name: … · Voice: …" line.
    engine_info["character"] = _CFG.character
    engine_info["voice"] = _CFG.voice_name
    engine_info["persona"] = _CFG.persona_name
    # Gauge the panel against the MEASURED reliable-context line, not the advertised
    # window. None → the panel falls back to `allotted` (advertised) — see control.py.
    engine_info["reliable"] = _CFG.reliable_context

    # Hand the switcher its late-bound deps and expose it on
    # the panel — features/live_switch.py routes answer 503 until this attach.
    live_switcher.engine_info = engine_info
    live_switcher.recorder = recorder
    hearth.control.features.live_switch.attach(live_switcher)
    # The memory status tap reads the switcher's CURRENT seam per request (a
    # live switch is honored); mode is the sitting's effective posture, same
    # value the Misc line shows. Attached even seamless — the route reports
    # {attached: false} honestly rather than 503ing forever.
    hearth.control.features.memory_status.attach(
        live_switcher, engine_info["memory_mode"],
        voice_prefetch_built=memory_prefetch_proc is not None)

    # LM Studio serves several clients, so Hearth's model can be
    # swapped/unloaded mid-run — a startup-frozen Engine line would then show
    # something false. Re-poll the probe on a slow cadence into the SAME dict the
    # /engine route serves (shared by reference). fetch_engine_info returns ONLY
    # the probe keys, so .update() never touches the startup-resolved piggybacks
    # (session/character/voice/reliable); a failed poll yields Nones → the panel
    # degrades to '—' and the token gauge keeps budgeting off `reliable`.
    async def _engine_repoll(interval_s: float = 60.0) -> None:
        while True:
            await asyncio.sleep(interval_s)
            # live_switcher.lm_model tracks the ACTIVE model id across live
            # switches; it starts equal to LM_MODEL.
            engine_info.update(await fetch_engine_info_for(
                LM_PROVIDER, LM_BASE_URL, LM_API_TOKEN, live_switcher.lm_model))

    engine_repoll_task = asyncio.create_task(_engine_repoll())

    # M7: default capture name follows the session's held name when there is one
    # ("New"/"Restored" aren't names); one-shot loopback-device scan decides whether
    # the panel's "background music" tickbox is live or disabled.
    if session_descriptor and session_descriptor not in ("New", "Restored"):
        recorder.default_name = _capture_slug(session_descriptor)
    await recorder.detect_music_device()

    # Step 3: start the web control box in the same event loop before running
    # the pipeline so handlers can call worker/context/mute_gate directly.
    web_runner = await start_web_server(
        worker, context, mute_gate, speaking_tap, meter, engine_info, recorder
    )

    # M8/P1: the /v1 serve facade — one authed door for chat/voice-note/away
    # consumers (persona-composed chat → LM Studio; audio legs → :8555).
    # config/serve.toml gates it: absent/enabled=false ⇒ None and nothing loads
    # (byte-identical appliance, no prompt-slot participation). Own app + port —
    # deliberately NOT the panel's app (auth + tailnet isolation; see serve/).
    serve_runner = await serve.maybe_attach(_CFG, LM_BASE_URL, LM_API_TOKEN)

    runner = WorkerRunner()
    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        meter.print_summary()
        # M7: never lose an in-flight capture — a Ctrl-C mid-recording finalizes
        # (stems closed, mixdown rendered) exactly as if Record had been pressed off.
        if recorder.armed:
            try:
                res = await recorder.stop()
                print(f"[record] finalized on shutdown → {res.get('mix') or res.get('stems')}",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[record] shutdown finalize failed ({})", type(exc).__name__)
        engine_repoll_task.cancel()
        await web_runner.cleanup()
        if serve_runner is not None:
            await serve_runner.cleanup()
        # Memory seam: store + consolidate on graceful end — MUST run BEFORE
        # session_store.finalize below, which applies the keep-decision (and
        # true-deletes a recall-only sitting's transcript).
        # The seam writes the canonical memory record first, then lets the
        # backend index it; every step is contained inside on_session_end (a
        # memory failure degrades, never breaks shutdown).
        # A live companion switch may have replaced the
        # store/seam mid-run — the switcher owns the CURRENT pair. Drain its
        # background old-session finalize first so the two never interleave.
        await live_switcher.drain(30.0)
        seam_now = live_switcher.current_seam
        store_now = live_switcher.current_store
        if seam_now is not None:
            mem_status = seam_now.on_session_end(context.messages, store_now)
            if mem_status:
                print(f"[memory] {mem_status}", flush=True)
            seam_now.close()
        live_switcher.close_pending()
        # Session lifecycle (Tier 1): saved-by-default. On this graceful SIGINT/finally
        # path (what ./stop.sh triggers) the bot keeps its session file — the one
        # carve-out is a recall-only sitting, whose transcript is truly deleted unless
        # held. Snapshot+os.replace means no file handle is open here → delete frees it
        # cleanly. An UNCLEAN death (kill -9 / crash / outage) skips this block
        # entirely → the file survives untouched, which is what enables outage resume.
        if store_now is not None:
            try:
                status = session_store.finalize(store_now, context.messages)
                print(f"[session] {status}", flush=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[session] finalize failed: {}", type(exc).__name__)
            else:
                # Auto-compaction safety net: a HELD session past the trigger
                # drops a request for the facade's compact watch. The meter's
                # last per-turn prompt count (the server's own held-in-ctx
                # number) beats the file-size estimate when it is larger.
                note = compact_trigger.maybe_request(
                    store_now, live_tokens=meter.last_prompt or None)
                if note:
                    print(f"[session] {note}", flush=True)


# ── Session resolution ─────────────────────────────────────────────────────────
# The interactive resume/new choosers + resolution logic live in session_cli.py
# (imported as resolve_session above). They run BEFORE model load so their guards
# fail fast, and are parameterized by the live identity values below.


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hearth live voice loop")
    parser.add_argument(
        "--dump-tts",
        action="store_true",
        help="capture every synthesised utterance (audio + text) to --dump-dir "
        "for prosody-artifact debugging (off by default; no effect on the loop)",
    )
    parser.add_argument(
        "--dump-dir",
        default="tts-capture",
        help="directory for --dump-tts WAVs + manifest.tsv (default: ./tts-capture)",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",  # bare --resume (no value) → picker/single-candidate resolution
        default=None,  # flag absent → fresh session (subject to the --new guard)
        metavar="FILE|NAME",
        help="resume a prior session. Bare --resume picks among candidates "
        "(metadata only); --resume <file|name> loads that one explicitly.",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="start fresh. Saved sessions are kept (sessions save by default); "
        "only recall-only leftovers are swept. Skips the bare-start chooser.",
    )
    parser.add_argument(
        "--memory",
        choices=("full", "recall-only", "off"),
        default=None,  # absent ⇒ a resumed session's own stamp, else "full"
        metavar="MODE",
        help="this session's memory posture: full (default — recall and retain, "
        "unchanged), recall-only (the companion recalls their real memories but this "
        "session leaves no memory record — nothing is retained), off (no "
        "recall, no retention — a fresh meeting). Governs the memory bank "
        "only; the session transcript keeps its own lifecycle (--hold). "
        "Flag absent, a resumed session keeps the mode it was saved under.",
    )
    args = parser.parse_args()

    # Session-store maintenance lock (design: auto-compaction-on-close). The
    # bot holds the active character's lock for the life of the process — the
    # kernel releases it on ANY death — so offline compaction and a live
    # session can never overlap. Held = a compaction (or another bot) owns
    # the store right now: refuse fast, before any model load.
    if not maintenance_lock.hold(_CFG.character, op="session"):
        _busy = maintenance_lock.probe(_CFG.character) or {}
        print(f"[session] {_CFG.character}'s session store is busy "
              f"({maintenance_lock.describe(_busy)}) — try again in a few "
              "minutes.", flush=True)
        raise SystemExit(2)

    _store, _resume_messages, _session_desc = resolve_session(
        args, LM_MODEL, VOICE_TAG, PROMPT_FINGERPRINT,
        character=_CFG.character, persona=_CFG.persona_name,
    )

    # The sitting's memory mode: explicit --memory wins; flag absent, a resumed
    # session's stamp is inherited (a crashed recall-only sitting must not get
    # banked by a default resume). Stamps the store for this run's snapshots.
    _memory_mode = session_store.inherit_memory_mode(args.memory, _store)
    if args.memory is None and _memory_mode != "full":
        print(f"[memory] resumed session carries memory mode '{_memory_mode}' — "
              "inheriting it (pass --memory full to override)", flush=True)

    asyncio.run(main(
        dump_dir=args.dump_dir if args.dump_tts else None,
        store=_store,
        resume_messages=_resume_messages,
        session_descriptor=_session_desc,
        memory_mode=_memory_mode,
    ))
