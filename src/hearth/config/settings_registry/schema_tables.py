"""settings_registry/schema_tables.py — the per-TABLE schemas — [serve], [memory]
and [openclaw], each a gate inside a shared config file.

Sliced out of the single settings_registry.py it used to share; see the
package __init__ for the layout and the order the parts import in.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from .knobs import _Cfg, _NAME, _effect, _secret

# ── config/serve.toml [serve] (the facade gate) ──────────────────────────────

class _ServeIdentityTts(_Cfg):
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="pinned synth temperature (speech served by Hearth)")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="pinned top_p")
    top_k: Optional[int] = Field(None, ge=1, le=10_000, description="pinned top_k")
    repetition_penalty: Optional[float] = Field(None, ge=0.5, le=5.0, description="pinned repetition penalty")
    speed: Optional[float] = Field(None, gt=0.0, description="pinned playback-rate multiplier (upstream mlx-audio)")
    allow_tag_profiles: bool = Field(False, description="policy flag: may per-tag profiles overlay the pin? never forwarded upstream")


class _ServeIdentity(_Cfg):
    character: str = Field(pattern=_NAME, description="pinned character for Hearth's served lane (independent of active.toml)")
    voice: str = Field(pattern=_NAME, description="pinned voice bundle for that character")
    tts: Optional[_ServeIdentityTts] = Field(None, description="pinned synth knobs; win over client body and live layer")


class _SupWatch(_Cfg):
    url: str = Field(description="health-probe URL, reported on /admin/state externals (reachability boolean only)")


class _SupActuator(_Cfg):
    command: list[str] = Field(min_length=1, description="fixed argv, exec'd directly — no shell, no runtime arguments")
    timeout_s: float = Field(120.0, gt=0.0, description="bounded wait, seconds; overrun kills the command itself (never what it detached)")
    cwd: str = Field("", description="working directory for the command (empty = Hearth's own)")
    note: str = Field("", description="human description shown on /admin/actuators")
    probe_url: str = Field("", description="optional reachability probe shown beside the actuator (any HTTP answer = up)")
    guard: str = Field("", description="\"companion\" ⇒ refused (409) while a companion is running unless the press confirms with ?force=1 — for commands whose cost the next turn pays (freeing the model); empty ⇒ runs any time")


class _ServeSupervisor(_Cfg):
    enabled: bool = Field(False, description="mount the launch page (/admin routes + panel proxy) when Hearth runs standalone")
    panel_url: str = Field("http://127.0.0.1:65000", description="the companion's control panel — proxy target + reachability probe")
    stop_grace_s: float = Field(15.0, gt=0.0, description="seconds after SIGINT before escalating (memory-consolidation headroom)")
    term_grace_s: float = Field(5.0, gt=0.0, description="seconds after SIGTERM before SIGKILL")
    env: dict[str, str] = Field(default_factory=dict, description="extra child env for the spawned companion (e.g. LM_PROVIDER) — values never printed",
                                json_schema_extra=_secret())
    watch: dict[str, _SupWatch] = Field(default_factory=dict, description="extra watched externals probed on /admin/state (watched, never owned)")
    actuators: dict[str, _SupActuator] = Field(default_factory=dict, description="declared external actuators: operator-fixed commands behind the door, never children")
    compact_watch: bool = Field(True, description="run the auto-compaction watch: close-time compaction requests (DATA/ops/compact-queue) execute once no companion is alive, arbitrated by the per-character maintenance lock")


class ServeTable(_Cfg):
    enabled: bool = Field(False, description="master switch: off ⇒ nothing loads, no socket (byte-identical appliance)")
    host: str = Field("127.0.0.1", description="bind address — loopback by default; NEVER 0.0.0.0 without an overlay network")
    port: int = Field(65001, ge=1, le=65535, description="the port Hearth listens on")
    token_source: str = Field("config/serve-token", description="PATH to the access key file — never the key itself")
    lm_base_url: str = Field("http://127.0.0.1:8080/v1", description="upstream OpenAI-compatible model server endpoint")
    lm_token_source: str = Field("", description="PATH to the model server's key file, if it wants one (env LM_API_TOKEN wins)")
    audio_base_url: str = Field("http://127.0.0.1:8555/v1", description="upstream speech server (mlx-audio)")
    tts_model: str = Field("mlx-community/chatterbox-turbo-fp16", description="speech-synthesis model id sent upstream")
    stt_model: str = Field("mlx-community/whisper-large-v3-turbo", description="transcription model id sent upstream")
    speech_enabled: bool = Field(True, description="serve /v1/audio/speech (voice out)")
    transcriptions_enabled: bool = Field(False, description="serve /v1/audio/transcriptions (voice in) — ships OFF")
    transcript_tap: bool = Field(True, description="append each turn to a per-companion plaintext transcript")
    transcript_dir: str = Field("transcripts", description="relative ⇒ inside each companion's own directory; absolute used as-is")
    identity: Optional[_ServeIdentity] = Field(None, description="fixed identity for Hearth's served lane instead of the active.toml snapshot")
    characters: dict[str, str] = Field(default_factory=dict,
                                       description="roster a client may declare: character name → its voice bundle (/v1/models)")
    supervisor: Optional[_ServeSupervisor] = Field(
        None, description="the launch page: standalone Hearth owns the voice companion as a child process; absent/off ⇒ byte-identical")


# ── config/memory.toml [memory] (the memory seam gate) ───────────────────────

# Effect-time notes shared by a whole sub-table (kept short: they ride the
# JSON Schema into any surface that renders them).
_PER_TURN_NOTE = ("setting consulted every turn, value frozen into the seam at attach "
                  "from the process-boot snapshot — not hot")
_SIDECAR_NOTE = ("a dead sidecar's auto-respawn reuses the in-memory boot snapshot — "
                 "restart the owning process, not just the sidecar")


class _MemIntent(_Cfg):
    enabled: bool = Field(False, description="intent-primed boot recall: a stated next-topic survives the gap",
                          json_schema_extra=_effect("bot+facade"))
    expiry_days: int = Field(14, ge=0, description="skip + clear a slot older than this (0 = no expiry)",
                             json_schema_extra=_effect("bot+facade"))
    llm_provider: str = Field("", description="extraction transport; falls back to [memory.hindsight]'s",
                              json_schema_extra=_effect("bot+facade"))
    llm_model: str = Field("", description="extraction model; falls back to [memory.hindsight]'s",
                           json_schema_extra=_effect("bot+facade"))
    llm_url: str = Field("", description="extraction endpoint override",
                         json_schema_extra=_effect("bot+facade"))
    companions: dict[str, bool] = Field(default_factory=dict, description="per-companion enable override",
                                        json_schema_extra=_effect("bot+facade"))


class _MemPerTurn(_Cfg):
    enabled: bool = Field(False, description="per-turn targeted recall (chat lane): re-query the bank with the user's own words each request",
                          json_schema_extra=_effect("bot+facade", _PER_TURN_NOTE))
    limit: int = Field(3, ge=0, description="targeted extras appended under their own labeled line (deduped against the open block)",
                       json_schema_extra=_effect("bot+facade", _PER_TURN_NOTE))
    min_cue_chars: int = Field(12, ge=0, description="skip cues shorter than this (bare greetings and closes)",
                               json_schema_extra=_effect("bot+facade", _PER_TURN_NOTE))
    voice: bool = Field(False, description="ALSO run the voice lane (prefetch-behind: recall after turn N, injected before turn N+1); needs enabled=true; ships OFF",
                        json_schema_extra=_effect("bot+facade",
                                                  _PER_TURN_NOTE + "; on the desk pipeline it also "
                                                  "decides whether the prefetch processor is built at all"
                                                  " — in a voice-on sitting the panel can pause/resume"
                                                  " the lane live (runtime-only poke; this file stays"
                                                  " the between-sessions truth)"))


class _MemServe(_Cfg):
    enabled: bool = Field(False, description="served-lane sessions (idle-gap boundaries, records, recall)",
                          json_schema_extra=_effect("facade", "the companion lane never reads [memory.serve]"))
    idle_close_voice: int = Field(5, ge=1, description="voice-lane idle close, MINUTES (grace + margin over the voice server's reaper)",
                                  json_schema_extra=_effect("facade"))
    idle_close_chat: int = Field(480, ge=1, description="chat-lane idle FALLBACK close, MINUTES (behind deliberate closure)",
                                 json_schema_extra=_effect("facade"))
    checkpoint: bool = Field(True, description="checkpoint open sessions each exchange (crash leaves a recoverable orphan)",
                             json_schema_extra=_effect("facade"))


class _MemHindsight(_Cfg):
    mode: str = Field("sidecar", description='"sidecar" (own venv; protobuf wall) or "embedded" (non-pipecat hosts only)',
                      json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    python: Optional[str] = Field(None, description="sidecar venv python — REQUIRED for sidecar mode",
                                  json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    runner: Optional[str] = Field(None, description="sidecar runner override (default: bundled sidecar_runner.py)",
                                  json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    llm_provider: str = Field("ollama", description="extraction-model provider",
                              json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    llm_model: Optional[str] = Field(None, description="REQUIRED when any companion selects hindsight: local extraction model",
                                     json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    llm_api_key: str = Field("", description="provider key if the local server wants one",
                             json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE, secret=True))
    db_url: str = Field("pg0", description="backend store — pg0 = bundled embedded PostgreSQL",
                        json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    retain_max_chars: int = Field(6000, ge=0, description="transcript tail handed to extraction at stop",
                                  json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    recent_boost: int = Field(3, ge=0, description="the last-session slot: newest valid facts appended past semantic rank (0 = off)",
                              json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    log_level: str = Field("warning", description="sidecar log level",
                           json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    log_file: Optional[str] = Field(None, description="sidecar child's own stdout+stderr (default: <data folder>/logs/…)",
                                    json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    start_timeout_s: Optional[float] = Field(None, gt=0.0, description="sidecar start timeout override, seconds",
                                             json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE))
    env: dict[str, str] = Field(default_factory=dict, description="extra environment for the server (setdefault; shell wins)",
                                json_schema_extra=_effect("bot+facade", _SIDECAR_NOTE, secret=True))


class MemoryTable(_Cfg):
    enabled: bool = Field(False, description="master switch: off ⇒ engine byte-identical (no recall, no records)",
                          json_schema_extra=_effect("bot+facade"))
    backend: str = Field("floor", description='default backend per companion: "floor" | "hindsight" | "none"',
                         json_schema_extra=_effect("bot+facade",
                                                   "the facade keeps one backend per companion for its whole life"))
    recall_limit: int = Field(6, ge=0, description="recalled items injected at session start (one dated line each)",
                              json_schema_extra=_effect("bot+facade"))
    recall_query: str = Field("the user's life, preferences, and recent conversations",
                              description="what recall asks the backend for (semantic backends only)",
                              json_schema_extra=_effect("bot+facade"))
    companions: dict[str, str] = Field(default_factory=dict, description="per-companion backend override (the continuity dial)",
                                       json_schema_extra=_effect("bot+facade",
                                                                 "the facade keeps one backend per companion for its whole life"))
    intent: Optional[_MemIntent] = Field(None, description="intent-primed boot recall")
    per_turn: Optional[_MemPerTurn] = Field(None, description="per-turn targeted recall (chat lane synchronous; voice lane prefetch-behind; ships OFF)")
    serve: Optional[_MemServe] = Field(None, description="served-lane session glue")
    hindsight: Optional[_MemHindsight] = Field(None, description="Hindsight backend settings")


# ── config/openclaw.toml [openclaw] (the dispatch-bridge gate) ───────────────

class OpenclawTable(_Cfg):
    enabled: bool = Field(False, description="master switch: off ⇒ no tools registered, prompt byte-identical")
    gateway_url: str = Field("http://127.0.0.1:18789", description="OpenClaw gateway endpoint")
    agent: str = Field("hands", description="gateway agent name dispatched to")
    token_source: str = Field("", description="PATH to the gateway token file — never the token itself")
    quick_wait_s: float = Field(8.0, ge=0.0, description="synchronous wait before handing off to background, seconds")
    timeout_s: float = Field(600.0, ge=0.0, description="dispatch hard timeout, seconds")
    max_in_flight: int = Field(2, ge=1, description="concurrent dispatch cap")
    prompt_block: str = Field("", description="{{openclaw_tools}} capability paragraph injected while enabled")


