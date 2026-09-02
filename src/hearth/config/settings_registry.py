"""settings_registry.py — one declared schema per Hearth config file.

The settings registry (schema-driven settings, step 1): a single declarative
source of truth for every file-configurable setting — its TOML path, type,
constraints, default, scope layer, live-tunability, and help text. Three
consumers derive from it so they cannot drift:

  1. config_loader._schema_check — the load-time shape check (lenient: type
     violations fail fast naming the file; unknown keys and out-of-range
     values only warn, so a boot that works today keeps working).
  2. `python -m hearth.config.check` — strict whole-install validation, plus
     the GENERATED settings reference (docs config-manual) and the JSON
     Schema bundle. A test keeps the generated page byte-synced.
  3. (step 2, unbuilt) the panel's generated settings forms — json_schema()
     is that contract, carrying an `x-hearth` extra per live-tunable field.

Derived surfaces (derive-knobs stroke, 2026-09-01): the honored-surface
constants now DERIVE from this registry — config_knobs' schema/ranges,
config_reload._ENGINE_LIVE_KEYS/_VAD_FALLBACK, tag_profiles._ALLOWED_KNOBS/
TEMP_CEILING, and tts_prep._SPEECH_KNOBS all import from here; the step-1
hand-sync parity pins retired. One deliberate exception runs the other way:
the paralinguistic tag VOCABULARY lives with its stem-behavior table
(paralinguistics._STEMS — a name list cannot generate behavior), so
CANONICAL_TAGS derives FROM paralinguistics. The ear-verified content values
are pinned in tests/test_settings_registry.py, so an accidental edit here
still turns the suite red.

Dependency note: pydantic v2 is guaranteed present transitively — pipecat-ai,
a base dependency, pins pydantic>=2.10.6,<3 — and is deliberately NOT added to
[project.dependencies] this stroke (uv.lock regeneration is out of scope;
declare it explicitly at the next legitimate lock regeneration).

Error contract: this module never raises ConfigError (config_loader owns
that); it raises SchemaError, which the loader converts — no import cycle.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hearth.tts import paralinguistics as _paralinguistics  # leaf module (stdlib-only)

_NAME = r"^[A-Za-z0-9._-]+$"  # dir-name safe (config_loader._NAME_RE)

# ── single-source knob surfaces (the live modules import these) ───────────────
TEMP_CEILING = 1.4  # highest ear-verified-clean synth temperature (2026-08-26 ear session)
ENGINE_LIVE_KNOBS: dict[str, frozenset[str]] = {
    # Synth knobs each engine HONORS live; proper Chatterbox adds the two
    # emotion knobs (EXPENSIVE tier — engine swap not wired).
    "chatterbox-turbo": frozenset({"temperature", "top_p", "top_k", "repetition_penalty"}),
    "chatterbox": frozenset({"temperature", "top_p", "top_k", "repetition_penalty",
                             "exaggeration", "cfg_weight"}),
}
TURBO_LIVE_KNOBS = ENGINE_LIVE_KNOBS["chatterbox-turbo"]
SERVE_SPEECH_KNOBS = TURBO_LIVE_KNOBS | {"speed"}  # the facade speech layer adds speed
# The tag vocabulary derives the OTHER way — it lives with its stem-behavior
# table (a name list cannot generate behavior); bracketless here.
CANONICAL_TAGS = frozenset(t[1:-1] for t in _paralinguistics._CANONICAL)


def _live(hot_via: str, status: str | None = None) -> dict:
    """x-hearth extra for a field with a live path: overrides.toml for the
    knob tiers; the supervisor's switch intent for the selection fields."""
    return {"x-hearth": {"hot_via": hot_via, "status_source": status}}


class _Cfg(BaseModel):
    """Base for every file model: unknown keys tolerated at validation (they are
    reported separately, as warnings — never a hard stop)."""
    model_config = ConfigDict(extra="allow")


# ── config/active.toml ────────────────────────────────────────────────────────

class ActiveFile(_Cfg):
    character: str = Field(pattern=_NAME, description="who is live — dir under characters/",
                           json_schema_extra=_live("switch intent (turn boundary)", "GET /admin/switch"))
    model: str = Field(pattern=_NAME, description="model config — dir under config/models/",
                       json_schema_extra=_live("switch intent (turn boundary; resident models only)",
                                               "GET /admin/switch"))
    voice: str = Field(pattern=_NAME, description="voice bundle — dir under characters/<character>/voices/",
                       json_schema_extra=_live("switch intent (turn boundary)", "GET /admin/switch"))
    persona: str = Field("default", pattern=_NAME,
                         description='persona variant: "default" = persona.md, else persona.<name>.md',
                         json_schema_extra=_live("switch intent (turn boundary)", "GET /admin/switch"))


# ── config/models/<model>/model.toml ─────────────────────────────────────────

class ModelFile(_Cfg):
    id: str = Field(description="model id your inference server advertises, verbatim")
    temperature: float = Field(ge=0.0, le=2.0, description="request-side sampling temperature",
                               json_schema_extra=_live("llm.temperature", "GET /config/profiles"))
    reasoning_effort: Literal["none", "low", "medium", "high"] = Field(
        description='request-side reasoning control; "none" is a harmless no-op on non-reasoning models',
        json_schema_extra=_live("llm.reasoning_effort"))
    needs_template_edit: bool = Field(False, description="model needs a persistent chat-template edit (e.g. thinking off)")
    no_kv_reuse: bool = Field(False, description="true if prefix KV-cache reuse is unsafe for this model")
    reliable_context: Optional[int] = Field(None, ge=1,
                                            description="measured usable-context ceiling the panel's token gauge counts against")


# ── characters/<c>/voices/<v>/voice.toml ─────────────────────────────────────

class VoiceFile(_Cfg):
    tag: str = Field(description="human-readable voice tag, recorded with sessions (voice-drift warning)")
    ref_wav: str = Field(description="clone reference clip; relative = beside this descriptor",
                         json_schema_extra=_live("voice.ref_wav (session-scoped)"))
    license: Optional[str] = Field(None, description="clip license (provenance)")
    source: Optional[str] = Field(None, description="clip source (provenance)")
    model_repo: Optional[str] = Field(None, description="synth model the clip is prepared for (doc)")
    sample_rate: Optional[int] = Field(None, ge=1, description="clip sample rate, Hz (doc)")
    streaming_interval: Optional[float] = Field(None, ge=0.0, description="synth chunk interval, s (doc)")


# ── config/overrides.toml (panel-managed live layer) ─────────────────────────

class _OvLLM(_Cfg):
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="live LLM temperature")
    reasoning_effort: Optional[Literal["none", "low", "medium", "high"]] = Field(
        None, description="live reasoning control")
    persona: Optional[str] = Field(None, max_length=16_000,
                                   description="live {{persona}} slot text (template hard-rules stay pinned)")


class _OvTTS(_Cfg):
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="synth temperature (intonation looseness)")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="synth nucleus sampling")
    top_k: Optional[int] = Field(None, ge=1, le=10_000, description="synth top-k")
    repetition_penalty: Optional[float] = Field(None, ge=0.5, le=5.0, description="synth repetition penalty")


class _OvVAD(_Cfg):
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="how sure the VAD must be a sound is speech")
    start_secs: Optional[float] = Field(None, ge=0.05, le=1.0, description="sustained sound before 'you started talking'")
    stop_secs: Optional[float] = Field(None, ge=0.2, le=3.0, description="silence after speech before 'you finished'")
    min_volume: Optional[float] = Field(None, ge=0.0, le=1.0, description="loudness floor to count as speech")


class _OvVoice(_Cfg):
    ref_wav: Optional[str] = Field(None, description="live voice audition (SESSION-SCOPED: scrubbed at next startup)")


class OverridesFile(_Cfg):
    llm: Optional[_OvLLM] = Field(None, description="per-character reasoning/sampling overrides")
    tts: Optional[_OvTTS] = Field(None, description="per-voice synthesis overrides")
    vad: Optional[_OvVAD] = Field(None, description="listening calibration overrides")
    voice: Optional[_OvVoice] = Field(None, description="live voice-clip audition")


# ── config/tts/<engine>/tts.toml (shipped baseline) ──────────────────────────

class _TtsLive(_Cfg):
    temperature: float = Field(0.8, ge=0.0, le=2.0, description="baseline synth temperature (== engine default)",
                               json_schema_extra=_live("tts.temperature", "GET /config/profiles"))
    top_p: float = Field(0.95, ge=0.0, le=1.0, description="baseline nucleus sampling (== engine default)",
                         json_schema_extra=_live("tts.top_p"))
    top_k: int = Field(1000, ge=1, le=10_000, description="baseline top-k (== engine default)",
                       json_schema_extra=_live("tts.top_k"))
    repetition_penalty: float = Field(1.2, ge=0.5, le=5.0, description="baseline repetition penalty (== engine default)",
                                      json_schema_extra=_live("tts.repetition_penalty"))


class _TagProfile(_Cfg):
    temperature: Optional[float] = Field(None, ge=0.0, le=TEMP_CEILING,
                                         description=f"per-tag temperature delta target (ear-calibrated; ceiling {TEMP_CEILING})")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="per-tag top_p (ear-calibrated)")
    top_k: Optional[int] = Field(None, ge=1, le=10_000, description="per-tag top_k (ear-calibrated)")
    repetition_penalty: Optional[float] = Field(None, ge=0.5, le=5.0, description="per-tag repetition penalty (ear-calibrated)")


class TtsBaselineFile(_Cfg):
    live: _TtsLive = Field(default_factory=_TtsLive,
                           description="knobs the engine honors live; every value == the engine's own default (no-op guarantee)")
    tag_profiles: dict[str, _TagProfile] = Field(
        default_factory=dict,
        description="per-tag knob deltas for the one chunk carrying the tag; keys must be canonical tags")
    inert: dict[str, Any] = Field(default_factory=dict,
                                  description="documentation-only: knobs this engine accepts-but-drops; never passed")


# ── config/vad.toml (shipped listening calibration) ──────────────────────────

class _VadLive(_Cfg):
    confidence: float = Field(0.7, ge=0.0, le=1.0, description="how sure the VAD must be a sound is speech",
                              json_schema_extra=_live("vad.confidence", "GET /config"))
    start_secs: float = Field(0.2, ge=0.05, le=1.0, description="sustained sound before 'you started talking'",
                              json_schema_extra=_live("vad.start_secs"))
    stop_secs: float = Field(0.5, ge=0.2, le=3.0, description="silence after speech before 'you finished'",
                             json_schema_extra=_live("vad.stop_secs"))
    min_volume: float = Field(0.6, ge=0.0, le=1.0, description="loudness floor to count as speech",
                              json_schema_extra=_live("vad.min_volume"))


class VadFile(_Cfg):
    live: _VadLive = Field(default_factory=_VadLive,
                           description="mic/room/operator calibration — never carried by character or voice profiles")


# ── config/serve.toml [serve] (the facade gate) ──────────────────────────────

class _ServeIdentityTts(_Cfg):
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="pinned synth temperature (facade speech)")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="pinned top_p")
    top_k: Optional[int] = Field(None, ge=1, le=10_000, description="pinned top_k")
    repetition_penalty: Optional[float] = Field(None, ge=0.5, le=5.0, description="pinned repetition penalty")
    speed: Optional[float] = Field(None, gt=0.0, description="pinned playback-rate multiplier (upstream mlx-audio)")
    allow_tag_profiles: bool = Field(False, description="policy flag: may per-tag profiles overlay the pin? never forwarded upstream")


class _ServeIdentity(_Cfg):
    character: str = Field(pattern=_NAME, description="pinned facade character (independent of active.toml)")
    voice: str = Field(pattern=_NAME, description="pinned voice bundle for that character")
    tts: Optional[_ServeIdentityTts] = Field(None, description="pinned synth knobs; win over client body and live layer")


class _SupWatch(_Cfg):
    url: str = Field(description="health-probe URL, reported on /admin/state externals (reachability boolean only)")


class _SupActuator(_Cfg):
    command: list[str] = Field(min_length=1, description="fixed argv, exec'd directly — no shell, no runtime arguments")
    timeout_s: float = Field(120.0, gt=0.0, description="bounded wait, seconds; overrun kills the command itself (never what it detached)")
    cwd: str = Field("", description="working directory for the command (empty = the daemon's)")
    note: str = Field("", description="human description shown on /admin/actuators")
    probe_url: str = Field("", description="optional reachability probe shown beside the actuator (any HTTP answer = up)")


class _ServeSupervisor(_Cfg):
    enabled: bool = Field(False, description="mount the daemon face (/admin routes + panel proxy) in the STANDALONE facade")
    panel_url: str = Field("http://127.0.0.1:65000", description="the bot's control panel — proxy target + reachability probe")
    stop_grace_s: float = Field(15.0, gt=0.0, description="seconds after SIGINT before escalating (memory-consolidation headroom)")
    term_grace_s: float = Field(5.0, gt=0.0, description="seconds after SIGTERM before SIGKILL")
    env: dict[str, str] = Field(default_factory=dict, description="extra child env for the spawned bot (e.g. LM_PROVIDER) — values never printed")
    watch: dict[str, _SupWatch] = Field(default_factory=dict, description="extra watched externals probed on /admin/state (ADR 007 §3 — watched, never owned)")
    actuators: dict[str, _SupActuator] = Field(default_factory=dict, description="declared external actuators (stroke 4): operator-fixed commands behind the door, never children")


class ServeTable(_Cfg):
    enabled: bool = Field(False, description="master gate: off ⇒ nothing loads, no socket (byte-identical appliance)")
    host: str = Field("127.0.0.1", description="bind address — loopback by default; NEVER 0.0.0.0 without an overlay network")
    port: int = Field(65001, ge=1, le=65535, description="facade port")
    token_source: str = Field("config/serve-token", description="PATH to the bearer token file — never the token itself")
    lm_base_url: str = Field("http://127.0.0.1:8080/v1", description="upstream OpenAI-compatible LLM endpoint")
    lm_token_source: str = Field("", description="PATH to the LLM server's key file, if it wants one (env LM_API_TOKEN wins)")
    audio_base_url: str = Field("http://127.0.0.1:8555/v1", description="upstream speech server (mlx-audio)")
    tts_model: str = Field("mlx-community/chatterbox-turbo-fp16", description="speech-synthesis model id sent upstream")
    stt_model: str = Field("mlx-community/whisper-large-v3-turbo", description="transcription model id sent upstream")
    speech_enabled: bool = Field(True, description="serve /v1/audio/speech (voice out)")
    transcriptions_enabled: bool = Field(False, description="serve /v1/audio/transcriptions (voice in) — ships OFF")
    transcript_tap: bool = Field(True, description="append each turn to a per-companion plaintext transcript")
    transcript_dir: str = Field("transcripts", description="relative ⇒ inside each companion's own directory; absolute used as-is")
    identity: Optional[_ServeIdentity] = Field(None, description="fixed facade identity instead of the active.toml snapshot")
    characters: dict[str, str] = Field(default_factory=dict,
                                       description="roster a client may declare: character name → its voice bundle (/v1/models)")
    supervisor: Optional[_ServeSupervisor] = Field(
        None, description="the daemon face (ADR 007): the standalone facade owns the voice bot as a child; absent/off ⇒ byte-identical")


# ── config/memory.toml [memory] (the memory seam gate) ───────────────────────

class _MemIntent(_Cfg):
    enabled: bool = Field(False, description="intent-primed boot recall: a stated next-topic survives the gap")
    expiry_days: int = Field(14, ge=0, description="skip + clear a slot older than this (0 = no expiry)")
    llm_provider: str = Field("", description="extraction transport; falls back to [memory.hindsight]'s")
    llm_model: str = Field("", description="extraction model; falls back to [memory.hindsight]'s")
    llm_url: str = Field("", description="extraction endpoint override")
    companions: dict[str, bool] = Field(default_factory=dict, description="per-companion enable override")


class _MemPerTurn(_Cfg):
    enabled: bool = Field(False, description="per-turn targeted recall (chat lane): re-query the bank with the user's own words each request")
    limit: int = Field(3, ge=0, description="targeted extras appended under their own labeled line (deduped against the open block)")
    min_cue_chars: int = Field(12, ge=0, description="skip cues shorter than this (bare greetings and closes)")


class _MemServe(_Cfg):
    enabled: bool = Field(False, description="facade-lane sessions (idle-gap boundaries, records, recall)")
    idle_close_voice: int = Field(5, ge=1, description="voice-lane idle close, MINUTES (grace + margin over the voice server's reaper)")
    idle_close_chat: int = Field(480, ge=1, description="chat-lane idle FALLBACK close, MINUTES (behind deliberate closure)")
    checkpoint: bool = Field(True, description="checkpoint open sessions each exchange (crash leaves a recoverable orphan)")


class _MemHindsight(_Cfg):
    mode: str = Field("sidecar", description='"sidecar" (own venv; protobuf wall) or "embedded" (non-pipecat hosts only)')
    python: Optional[str] = Field(None, description="sidecar venv python — REQUIRED for sidecar mode")
    runner: Optional[str] = Field(None, description="sidecar runner override (default: bundled sidecar_runner.py)")
    llm_provider: str = Field("ollama", description="extraction-model provider")
    llm_model: Optional[str] = Field(None, description="REQUIRED when any companion selects hindsight: local extraction model")
    llm_api_key: str = Field("", description="provider key if the local server wants one")
    db_url: str = Field("pg0", description="backend store — pg0 = bundled embedded PostgreSQL")
    retain_max_chars: int = Field(6000, ge=0, description="transcript tail handed to extraction at stop")
    recent_boost: int = Field(3, ge=0, description="the last-session slot: newest valid facts appended past semantic rank (0 = off)")
    log_level: str = Field("warning", description="sidecar log level")
    log_file: Optional[str] = Field(None, description="sidecar child's own stdout+stderr (default: <data root>/logs/…)")
    start_timeout_s: Optional[float] = Field(None, gt=0.0, description="sidecar start timeout override, seconds")
    env: dict[str, str] = Field(default_factory=dict, description="extra environment for the server (setdefault; shell wins)")


class MemoryTable(_Cfg):
    enabled: bool = Field(False, description="master gate: off ⇒ engine byte-identical (no recall, no records)")
    backend: str = Field("floor", description='default backend per companion: "floor" | "hindsight" | "none"')
    recall_limit: int = Field(6, ge=0, description="recalled items injected at session start (one dated line each)")
    recall_query: str = Field("the user's life, preferences, and recent conversations",
                              description="what recall asks the backend for (semantic backends only)")
    companions: dict[str, str] = Field(default_factory=dict, description="per-companion backend override (the continuity dial)")
    intent: Optional[_MemIntent] = Field(None, description="intent-primed boot recall")
    per_turn: Optional[_MemPerTurn] = Field(None, description="per-turn targeted recall (chat lane; ships OFF)")
    serve: Optional[_MemServe] = Field(None, description="facade-lane session glue")
    hindsight: Optional[_MemHindsight] = Field(None, description="Hindsight backend settings")


# ── config/openclaw.toml [openclaw] (the dispatch-bridge gate) ───────────────

class OpenclawTable(_Cfg):
    enabled: bool = Field(False, description="master gate: off ⇒ no tools registered, prompt byte-identical")
    gateway_url: str = Field("http://127.0.0.1:18789", description="OpenClaw gateway endpoint")
    agent: str = Field("hands", description="gateway agent name dispatched to")
    token_source: str = Field("", description="PATH to the gateway token file — never the token itself")
    quick_wait_s: float = Field(8.0, ge=0.0, description="synchronous wait before handing off to background, seconds")
    timeout_s: float = Field(600.0, ge=0.0, description="dispatch hard timeout, seconds")
    max_in_flight: int = Field(2, ge=1, description="concurrent dispatch cap")
    prompt_block: str = Field("", description="{{openclaw_tools}} capability paragraph injected while enabled")


# ── characters/<c>[/voices/<v>]/profile.toml (+ overrides.toml mirrors) ──────

class ProfileFile(_Cfg):
    llm: Optional[_OvLLM] = Field(None, description="character preset: deltas from the model baseline")
    tts: Optional[_OvTTS] = Field(None, description="voice preset: deltas from the engine baseline")


# ── the registry ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FileEntry:
    kind: str
    model: type[BaseModel]
    title: str
    path: str          # human-readable location pattern (data root first)
    role: str          # selection | load facts | descriptor | live overrides | calibration | gate | preset
    owner: str         # operator | panel | shipped
    layer: str         # place | model | identity  (ADR 005 scopes)
    restart: str       # what must relaunch for a persisted edit to land: none | bot | facade | bot+facade
    top_key: str | None = None  # gate files: the single top-level table
    note: str = ""


REGISTRY: dict[str, FileEntry] = {e.kind: e for e in (
    FileEntry("active", ActiveFile, "The selection pointer", "config/active.toml",
              "selection", "operator", "place", "bot+facade",
              note="Your one deliberate lever for who is live. Read once at startup; the "
                   "supervisor's switch button writes it and applies it live at the next turn "
                   "boundary (or via a warm restart) — hand-edit + restart keeps working. The "
                   "facade re-reads at kickstart (a [serve.identity] pin keeps its own voice "
                   "regardless)."),
    FileEntry("model", ModelFile, "Model load facts", "config/models/<model>/model.toml",
              "load facts", "operator", "model", "bot",
              note="Per-model request facts. context_length is deliberately absent — the live "
                   "server's loaded value wins. The facade re-reads at kickstart."),
    FileEntry("voice", VoiceFile, "Voice bundle descriptor", "characters/<character>/voices/<voice>/voice.toml",
              "descriptor", "operator", "identity", "bot",
              note="A voice is a self-contained bundle: descriptor + reference clip in one "
                   "directory. The clip conditions once at startup."),
    FileEntry("overrides", OverridesFile, "The live override layer", "config/overrides.toml",
              "live overrides", "panel", "place", "none",
              note="PANEL-MANAGED. Polled every turn boundary; values overlay the baselines "
                   "(delete a key and it reverts). [voice].ref_wav is session-scoped."),
    FileEntry("tts-baseline", TtsBaselineFile, "TTS engine baseline", "config/tts/<engine>/tts.toml",
              "calibration", "shipped", "place", "bot",
              note="Every [live] value equals the engine's own default (machine-checked no-op "
                   "guarantee). [tag_profiles.*] deltas are ear-calibrated — change by listening. "
                   "A data-root copy wins whole-file. The facade re-reads per speech request."),
    FileEntry("vad", VadFile, "Listening calibration", "config/vad.toml",
              "calibration", "shipped", "place", "bot",
              note="Mic, room, and speech-habit calibration — plumbing, never character texture; "
                   "profiles never carry it. A data-root copy wins whole-file."),
    FileEntry("serve", ServeTable, "The serve-facade gate", "config/serve.toml",
              "gate", "operator", "place", "facade", top_key="serve",
              note="Holds a bearer-token PATH: manage it, never print it. Off ⇒ byte-identical "
                   "appliance, no socket."),
    FileEntry("memory", MemoryTable, "The memory-seam gate", "config/memory.toml",
              "gate", "operator", "place", "bot+facade", top_key="memory",
              note="Cross-session continuity per companion. Records are the truth; backends are "
                   "derived indexes (delete a record, rerun rebuild, and the backend forgets too)."),
    FileEntry("openclaw", OpenclawTable, "The OpenClaw-bridge gate", "config/openclaw.toml",
              "gate", "operator", "place", "bot", top_key="openclaw",
              note="One gate drives tool registration AND the {{openclaw_tools}} prompt slot, so "
                   "capability and prompt can never disagree."),
    FileEntry("profile", ProfileFile, "Companion knob presets", "characters/<c>[/voices/<v>]/profile.toml (+ overrides.toml mirrors)",
              "preset", "panel", "identity", "none",
              note="PANEL-MANAGED snapshots of the override deltas for one companion or voice; "
                   "they travel with the companion's directory. An empty preset == baseline."),
)}


# Environment variables the engine READS (documented here; validated nowhere).
ENV_VARS: tuple[tuple[str, str, str, str], ...] = (
    ("HEARTH_ROOT", "the checkout (found from the package)", "config_loader", "engine-tree anchor"),
    ("HEARTH_DATA", "HEARTH_ROOT", "config_loader", "data root — everything the operator owns"),
    ("WEB_HOST", "127.0.0.1", "control panel", "panel bind address (0.0.0.0 = LAN)"),
    ("WEB_PORT", "65000", "control panel", "panel port"),
    ("LM_BASE_URL", "http://127.0.0.1:8080/v1", "pipeline (bot)", "LLM server endpoint"),
    ("LM_API_TOKEN", "none", "pipeline (bot)", "LLM bearer key, only if the server wants one"),
    ("LM_PROVIDER", "llama-server", "pipeline + panel", "which engine probe the panel uses (llama-server | lmstudio)"),
    ("T4_METRICS", "0", "pipeline (bot)", "1 = per-turn latency marks in the log"),
    ("SERVE_TOKEN", "(unset)", "serve facade", "facade bearer override — wins over token_source"),
)


# ── validation ───────────────────────────────────────────────────────────────

class SchemaError(ValueError):
    """A type violation on a present key (loader mode). config_loader converts
    this to ConfigError so startup errors keep naming the offending file."""


# Constraint-class pydantic error types: warnings at load time, errors under
# `check`. Everything else non-missing is a type violation → error in both.
_WARN_TYPES = frozenset({
    "greater_than_equal", "less_than_equal", "greater_than", "less_than",
    "literal_error", "string_too_long", "string_pattern_mismatch", "multiple_of",
})


def _model_of(annotation) -> type[BaseModel] | None:
    """Unwrap Optional[Model] / Model → the model class, else None."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (types.UnionType, Union):
        for arg in get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None


def _dict_value_model_of(annotation) -> type[BaseModel] | None:
    """dict[str, Model] → Model, else None."""
    if get_origin(annotation) is dict:
        args = get_args(annotation)
        if len(args) == 2 and isinstance(args[1], type) and issubclass(args[1], BaseModel):
            return args[1]
    return None


def _unknown_keys(model_cls: type[BaseModel], data: dict, prefix: str = "") -> list[str]:
    """Recursive unknown-key report against the declared fields (keys only —
    never values). The coverage property: a key cannot exist without appearing."""
    out: list[str] = []
    fields = model_cls.model_fields
    for key, value in data.items():
        if key not in fields:
            out.append(f"unknown key '{prefix}{key}'")
            continue
        ann = fields[key].annotation
        sub = _model_of(ann)
        if sub is not None and isinstance(value, dict):
            out.extend(_unknown_keys(sub, value, f"{prefix}{key}."))
            continue
        val_model = _dict_value_model_of(ann)
        if val_model is not None and isinstance(value, dict):
            for name, item in value.items():
                if isinstance(item, dict):
                    out.extend(_unknown_keys(val_model, item, f"{prefix}{key}.{name}."))
    return out


def _validate(kind: str, data: dict, *, strict: bool) -> tuple[list[str], list[str]]:
    entry = REGISTRY[kind]
    warnings = _unknown_keys(entry.model, data)
    if kind == "tts-baseline":
        for tag in (data.get("tag_profiles") or {}):
            if tag not in CANONICAL_TAGS:
                warnings.append(f"[tag_profiles.{tag}] is not a canonical tag (runtime ignores it)")
    errors: list[str] = []
    try:
        entry.model.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            dotted = ".".join(str(p) for p in err["loc"])
            msg = f"{dotted}: {err['msg']}"  # pydantic msg carries no input value
            if err["type"] == "missing":
                if strict:
                    errors.append(f"missing required key '{dotted}'")
            elif err["type"] in _WARN_TYPES:
                (errors if strict else warnings).append(msg)
            else:
                errors.append(msg)
    return errors, warnings


def loader_check(kind: str, data: dict) -> list[str]:
    """Load-time posture: returns warnings (unknown keys, out-of-range values);
    raises SchemaError on a type violation on a present key."""
    errors, warnings = _validate(kind, data, strict=False)
    if errors:
        raise SchemaError("; ".join(errors))
    return warnings


def strict_check(kind: str, data: dict) -> tuple[list[str], list[str]]:
    """`check` posture: (errors, warnings) with required-ness and constraints binding."""
    return _validate(kind, data, strict=True)


# ── JSON Schema emission (the step-2 form contract) ──────────────────────────

def json_schema() -> dict[str, dict]:
    return {
        kind: {
            "title": e.title, "path": e.path, "role": e.role, "owner": e.owner,
            "layer": e.layer, "restart": e.restart, "note": e.note,
            "schema": e.model.model_json_schema(),
        }
        for kind, e in REGISTRY.items()
    }


# ── generated settings reference (markdown) ──────────────────────────────────

def _type_name(annotation) -> str:
    if get_origin(annotation) is Literal:
        return "enum(" + " | ".join(str(a) for a in get_args(annotation)) + ")"
    if get_origin(annotation) in (types.UnionType, Union):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _type_name(args[0]) if len(args) == 1 else " | ".join(_type_name(a) for a in args)
    if _model_of(annotation) is not None:
        return "table"
    if get_origin(annotation) is dict:
        args = get_args(annotation)
        val = _dict_value_model_of(annotation)
        if val is not None:
            return "tables"
        return f"map(str → {getattr(args[1], '__name__', 'any')})" if len(args) == 2 else "map"
    return getattr(annotation, "__name__", str(annotation))


def _constraints(field) -> str:
    lo = hi = None
    for m in field.metadata:
        for attr, slot in (("ge", 0), ("gt", 0), ("le", 1), ("lt", 1)):
            v = getattr(m, attr, None)
            if v is not None:
                lo, hi = (v, hi) if slot == 0 else (lo, v)
        if getattr(m, "max_length", None) is not None:
            return f"≤ {m.max_length} chars"
    if lo is not None or hi is not None:
        return f"{lo if lo is not None else ''}–{hi if hi is not None else ''}"
    return ""


def _default_str(field) -> str:
    if field.is_required():
        return "**required**"
    if field.default_factory is not None:
        return "—"
    d = field.default
    if d is None:
        return "—"
    return f"`{str(d).lower()}`" if isinstance(d, bool) else f"`{d}`"


def _field_rows(model_cls: type[BaseModel], prefix: str = "") -> list[str]:
    rows: list[str] = []
    subtables: list[tuple[str, type[BaseModel]]] = []
    for name, field in model_cls.model_fields.items():
        ann = field.annotation
        extra = (field.json_schema_extra or {}).get("x-hearth", {}) if isinstance(field.json_schema_extra, dict) else {}
        live = f"`{extra['hot_via']}`" if extra.get("hot_via") else "—"
        sub = _model_of(ann)
        if sub is not None:
            subtables.append((f"{prefix}{name}", sub))
        rows.append(f"| `{prefix}{name}` | {_type_name(ann)} | {_default_str(field)} "
                    f"| {_constraints(field)} | {live} | {field.description or ''} |")
    for sub_name, sub_model in subtables:
        rows.extend(_field_rows(sub_model, prefix=f"{sub_name}."))
    return rows


_HEADER_ROW = ("| key | type | default | range | live path | what it sets |\n"
               "|---|---|---|---|---|---|")


# The generated reference ships as TWO pages (POL: split along natural seams —
# everyday knobs vs the gate files). Both regenerate from one command and a
# test keeps each byte-synced with this module.
MANUAL_PAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "settings-reference.md": (
        "Settings reference — selection, models, voices, live knobs",
        ("active", "model", "voice", "overrides", "tts-baseline", "vad", "profile"),
    ),
    "settings-reference-gates.md": (
        "Settings reference — the gate files",
        ("serve", "memory", "openclaw"),
    ),
}


def _render_page(name: str) -> str:
    title, kinds = MANUAL_PAGES[name]
    other = next(n for n in MANUAL_PAGES if n != name)
    out: list[str] = [
        f"# {title}",
        "",
        "> **GENERATED — do not hand-edit.** Source of truth: the settings registry",
        "> (`hearth/config/settings_registry.py`). Regenerate both pages:",
        "> `python -m hearth.config.check --emit-manual <this directory>`; a test fails on drift.",
        "",
        f"Companion page: [{other}]({other}). **Live path** = how a setting hot-applies at the next turn",
        "boundary: a `config/overrides.toml` dotted key (the panel writes that layer), or the supervisor's",
        "*switch intent* for the selection fields (the COMPANION button / `/admin/switch`, ADR 007 stroke 3).",
        "**Restart** (in each",
        "section header) = what must relaunch for a persisted edit to land: *bot* = the desk pipeline",
        "(`start.sh`) · *facade* = the serve facade (kickstart) · *none* = applies live. Strict validation",
        "of your install: `python -m hearth.config.check`.",
        "",
    ]
    for kind in kinds:
        entry = REGISTRY[kind]
        out += [
            f"## `{entry.path}` — {entry.title}",
            "",
            f"*{entry.layer} scope · {entry.owner}-owned · {entry.role} · restart: {entry.restart}*",
            "",
        ]
        if entry.note:
            out += [entry.note, ""]
        if entry.top_key:
            out += [f"All keys below live under the `[{entry.top_key}]` table.", ""]
        out += [_HEADER_ROW, *_field_rows(entry.model), ""]
    if name == "settings-reference-gates.md":
        out += [
            "## Prose layers (deliberately not schema'd)",
            "",
            "| file | what it is |",
            "|---|---|",
            "| `characters/<c>/persona.md` | the CHARACTER layer: `## IDENTITY` + `## SOUL` fill the `{{persona}}` slot |",
            "| `config/models/<m>/system-prompt-template.md` | the MODEL layer: envelope, spoken/no-markdown hard rules, `{{persona}}` slot |",
            "",
            "## Environment variables the engine reads",
            "",
            "| var | default | consumer | meaning |",
            "|---|---|---|---|",
            *(f"| `{n}` | `{d}` | {c} | {m} |" for n, d, c, m in ENV_VARS),
            "",
        ]
    return "\n".join(out)


def generate_manual_pages() -> dict[str, str]:
    """filename → rendered markdown, for every generated reference page."""
    return {name: _render_page(name) for name in MANUAL_PAGES}


# ── derived-surface helpers (derive-knobs stroke) ──────────────────────────────
# The live modules build their honored surfaces from these at import time; the
# declared field constraints above are the only place ranges/defaults are stated.

def _field_bounds(model, name):
    lo = hi = None
    for m in model.model_fields[name].metadata:
        lo = getattr(m, "ge", None) if getattr(m, "ge", None) is not None else lo
        hi = getattr(m, "le", None) if getattr(m, "le", None) is not None else hi
    return lo, hi


def _field_is_int(model, name) -> bool:
    ann = model.model_fields[name].annotation
    return ann is int or int in get_args(ann)


def vad_fallback() -> dict:
    """In-code [vad]-tier fallback = _VadLive defaults (bot.py's pre-tier literals)."""
    return {k: f.default for k, f in _VadLive.model_fields.items()}


def live_knob_ranges(section: str) -> dict:
    """{knob: (lo, hi, is_int)} for the 'tts' / 'vad' override tier, read off the
    declared field constraints — config_knobs' validation ranges derive here."""
    model = {"tts": _OvTTS, "vad": _OvVAD}[section]
    return {k: (*_field_bounds(model, k), _field_is_int(model, k))
            for k in model.model_fields}


def llm_knob_facts() -> dict:
    """The [llm] override tier's declared facts (panel schema + validation)."""
    ann = _OvLLM.model_fields["reasoning_effort"].annotation
    if get_origin(ann) in (types.UnionType, Union):
        for a in get_args(ann):
            if get_origin(a) is not None:
                ann = a
                break
    persona_max = next(m.max_length for m in _OvLLM.model_fields["persona"].metadata
                       if getattr(m, "max_length", None) is not None)
    return {
        "temperature": _field_bounds(_OvLLM, "temperature"),
        "reasoning_effort": frozenset(get_args(ann)),
        "persona_max_len": persona_max,
    }
