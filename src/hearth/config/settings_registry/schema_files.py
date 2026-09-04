"""settings_registry/schema_files.py — the per-FILE schemas: active, model, voice,
overrides, tts-baseline, vad, and the profile mirror.

Sliced out of the single settings_registry.py it used to share; see the
package __init__ for the layout and the order the parts import in.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from .knobs import TEMP_CEILING, _Cfg, _NAME, _live

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


# ── characters/<c>[/voices/<v>]/profile.toml (+ overrides.toml mirrors) ──────

class ProfileFile(_Cfg):
    voice: Optional[str] = Field(None, pattern=_NAME,
                                 description="character scope only: that character's remembered voice bundle — what "
                                             "the switch pickers offer when you move to them (else first-in-list)")
    llm: Optional[_OvLLM] = Field(None, description="character preset: deltas from the model baseline")
    tts: Optional[_OvTTS] = Field(None, description="voice preset: deltas from the engine baseline")

