"""config_loader.py — load the Hearth voice pipeline's selection from config files.

Moves the three hand-edited constants out of bot.py into data files: per-model
config and two-layer prompt composition (the MODEL template envelope wraps the
CHARACTER persona).

What it resolves (all pre-runtime; no hot-swap):
    config/active.toml                                  → which character + model + voice
    config/models/<model>/model.toml                    → id, temperature, reasoning_effort, …
    config/models/<model>/system-prompt-template.md     → the MODEL layer (envelope + hard rules)
    characters/<char>/persona.md                         → the CHARACTER layer ({{persona}} slot)
    characters/<char>/voices/<voice>/voice.toml          → ref_wav (relative to its dir) + synth facts

Composition (byte-equivalent to today's bot.py SYSTEM_INSTRUCTION literal):
    system_instruction = template with {{persona}} replaced by
                         IDENTITY-section body + "\n\n" + SOUL-section body

Design rules honored:
- Paths resolve relative to the repo root — never an absolute prefix, so the tree
  stays relocatable.
- context_length is NOT read here — the live server's loaded context length stays
  the source of truth.
- Fail-fast: a missing/malformed file raises ConfigError naming the exact file. No
  silent fallback to hardcoded literals (that would mask config errors).
- tomllib (stdlib, read-only TOML) — no new dependency.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The repo root holds config/ and characters/. This module lives at
# src/hearth/config/, so the asset tree is three levels up; HEARTH_ROOT overrides it
# for a relocated asset tree. Every asset path resolves against this root so the
# tree stays relocatable.
_ROOT = Path(os.environ.get("HEARTH_ROOT") or Path(__file__).resolve().parents[3])
CONFIG_DIR = _ROOT / "config"
MODELS_DIR = CONFIG_DIR / "models"
CHARACTERS_DIR = _ROOT / "characters"
ACTIVE_TOML = CONFIG_DIR / "active.toml"

OPENCLAW_TOML = CONFIG_DIR / "openclaw.toml"
SERVE_TOML = CONFIG_DIR / "serve.toml"

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_PERSONA_SLOT = "{{persona}}"
_DATETIME_SLOT = "{{datetime}}"
_OPENCLAW_SLOT = "{{openclaw_tools}}"


class ConfigError(RuntimeError):
    """A config file is missing, malformed, or missing a required key.

    The message always names the exact offending file so a startup failure is
    self-diagnosing (fail-fast; no silent fallback to literals).
    """


# ── low-level readers (each names the file it failed on) ─────────────────────

def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed TOML in {path}: {exc}") from exc


def _read_text(path: Path) -> str:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    return path.read_text(encoding="utf-8")


def _require(mapping: dict, key: str, path: Path):
    if key not in mapping:
        raise ConfigError(f"missing required key '{key}' in {path}")
    return mapping[key]


def _strip_comments(text: str) -> str:
    """Drop every HTML comment block. The pilot files carry authoring notes as
    <!-- … --> comments that must NOT reach the composed prompt."""
    return _HTML_COMMENT.sub("", text)


# ── resolved bundle ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActiveConfig:
    """Everything bot.py needs, resolved from config/active.toml + its targets.

    Field origins (each replaces a former bot.py / mlx_tts_service.py literal):
        model_id            ← model.toml.id
        temperature         ← model.toml.temperature
        reasoning_effort    ← model.toml.reasoning_effort
        needs_template_edit ← model.toml.needs_template_edit
        no_kv_reuse         ← model.toml.no_kv_reuse
        system_instruction  ← template ⊗ persona.md
        voice_tag           ← voice descriptor .tag
        ref_wav             ← voice descriptor .ref_wav (was the module default)
        model_repo/sample_rate/streaming_interval ← voice descriptor (documented; not
            forced into the TTS call — today's code uses the module defaults, which
            equal these values, so behavior is byte-identical)
    """

    character: str
    model_name: str
    voice_name: str

    model_id: str
    temperature: float
    reasoning_effort: str
    needs_template_edit: bool
    no_kv_reuse: bool

    system_instruction: str
    prompt_fingerprint: str  # system_instruction sans {{datetime}} — the stable drift-hash basis

    voice_tag: str
    ref_wav: str
    model_repo: str | None = None
    sample_rate: int | None = None
    streaming_interval: float | None = None
    reliable_context: int | None = None  # measured reliable-usable ctx line; panel gauges vs this


# ── individual loaders (composable; used by load_active() below) ──────────────

def load_active_selection() -> dict:
    """Read config/active.toml → {'character', 'model', 'voice'}."""
    data = _read_toml(ACTIVE_TOML)
    return {
        "character": _require(data, "character", ACTIVE_TOML),
        "model": _require(data, "model", ACTIVE_TOML),
        "voice": _require(data, "voice", ACTIVE_TOML),
    }


def load_model(model_name: str) -> dict:
    """Read config/models/<model_name>/model.toml. Requires 'id'."""
    path = MODELS_DIR / model_name / "model.toml"
    data = _read_toml(path)
    _require(data, "id", path)
    return data


def load_voice(character: str, voice: str) -> dict:
    """Read characters/<character>/voices/<voice>/voice.toml. Requires 'tag' + 'ref_wav'.

    The voice is a self-contained bundle: the descriptor and its reference clip
    live in one directory. `ref_wav` is resolved to an absolute path — a RELATIVE
    value resolves against this descriptor's own directory (so a vendored-in-repo
    clip travels with the tree, keeping it portable); an ABSOLUTE value is kept
    as-is (back-compat). The resolved clip MUST exist — it is launch-critical
    (prepare_conditionals reads it at TTS __init__), so a missing clip fails fast.
    The returned dict's 'ref_wav' is the resolved absolute path.
    """
    vdir = CHARACTERS_DIR / character / "voices" / voice
    path = vdir / "voice.toml"
    data = _read_toml(path)
    _require(data, "tag", path)
    ref = _require(data, "ref_wav", path)
    ref_path = Path(ref).expanduser()
    if not ref_path.is_absolute():
        ref_path = vdir / ref_path
    ref_path = ref_path.resolve()
    if not ref_path.exists():
        raise ConfigError(f"voice ref_wav not found: {ref_path} (declared in {path})")
    data["ref_wav"] = str(ref_path)
    return data


def compose_persona(character: str) -> str:
    """Extract the {{persona}} text from characters/<character>/persona.md.

    persona.md is authored as two labelled sections ('## IDENTITY', '## SOUL')
    with HTML-comment guidance. The composed persona is the IDENTITY body plus a
    blank line plus the SOUL body — reproducing the original prompt's paragraph
    shape (identity paragraph first) byte-for-byte (plan §6, wiring-doc §4).
    """
    path = CHARACTERS_DIR / character / "persona.md"
    text = _strip_comments(_read_text(path))
    # Split on the section headers, capturing the section name.
    parts = re.split(r"^##\s+([A-Za-z]+)\s*$", text, flags=re.MULTILINE)
    # parts = [preamble, name1, body1, name2, body2, ...]
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].strip().upper()] = parts[i + 1].strip()
    for required in ("IDENTITY", "SOUL"):
        if required not in sections or not sections[required]:
            raise ConfigError(f"persona.md missing non-empty '## {required}' section: {path}")
    return sections["IDENTITY"] + "\n\n" + sections["SOUL"]


def load_openclaw_config() -> dict | None:
    """Read config/openclaw.toml — the OpenClaw dispatch-bridge activation gate.

    Returns the [openclaw] table with defaults applied, or None when the file is
    absent or enabled=false. The SAME gate drives both consumers, so they can
    never disagree: openclaw_bridge.maybe_attach() (tool registration) and the
    {{openclaw_tools}} prompt slot below (capability paragraph). Malformed file
    ⇒ ConfigError naming it (fail-fast, per this module's contract); an absent
    optional file is NOT an error — it just means "bridge off".
    """
    if not OPENCLAW_TOML.exists():
        return None
    oc = _read_toml(OPENCLAW_TOML).get("openclaw")
    if not isinstance(oc, dict) or not oc.get("enabled"):
        return None
    cfg: dict = {
        "gateway_url": "http://127.0.0.1:18789",
        "agent": "hands",
        "token_source": "",
        "quick_wait_s": 8.0,
        "timeout_s": 600.0,
        "max_in_flight": 2,
        "prompt_block": "",
    }
    cfg.update(oc)
    return cfg


def _openclaw_prompt_block() -> str:
    """The {{openclaw_tools}} slot body: [openclaw].prompt_block when the bridge
    is enabled, else "" (the slot line is then removed entirely so a disabled
    bridge leaves the composed prompt byte-identical to the pre-bridge render).
    """
    cfg = load_openclaw_config()
    return str(cfg.get("prompt_block", "")).strip() if cfg else ""


def load_serve_config() -> dict | None:
    """Read config/serve.toml — the /v1 serve-facade activation gate (M8 / convergence P1).

    Returns the [serve] table with defaults applied, or None when the file is
    absent or enabled=false — the load_openclaw_config shape and contract: an
    absent optional file is NOT an error (facade off); malformed ⇒ ConfigError
    naming it. Unlike OpenClaw the facade is strictly I/O-edge — no prompt slot,
    no tool registration — so this gate never participates in prompt composition
    or the drift fingerprint (M8 §modular-gate: off = byte-identical appliance).

    token_source / lm_token_source are PATHS to secrets, never secrets;
    relative paths resolve against the repo root. Env wins: SERVE_TOKEN for the
    facade bearer, LM_API_TOKEN for the LM Studio key.

    Optional [serve.identity] table (character + voice, both required if the
    table exists): pins the facade's persona/voice to a FIXED selection instead
    of snapshotting active.toml — the facade then keeps its own identity no
    matter what the live session runs or when the facade was (re)started.
    Resolution happens in serve/app.py start(); here we only validate shape.
    """
    if not SERVE_TOML.exists():
        return None
    sv = _read_toml(SERVE_TOML).get("serve")
    if not isinstance(sv, dict) or not sv.get("enabled"):
        return None
    ident = sv.get("identity")
    if ident is not None:
        if not isinstance(ident, dict):
            raise ConfigError(f"[serve.identity] must be a table: {SERVE_TOML}")
        for key in ("character", "voice"):
            if not str(ident.get(key, "")).strip():
                raise ConfigError(f"[serve.identity] requires non-empty '{key}': {SERVE_TOML}")
        if "tts" in ident and not isinstance(ident["tts"], dict):
            raise ConfigError(f"[serve.identity.tts] must be a table: {SERVE_TOML}")
    cfg: dict = {
        "host": "127.0.0.1",
        "port": 65001,
        "token_source": "config/serve-token",
        "lm_base_url": "http://127.0.0.1:1234/v1",
        "lm_token_source": "~/.lmstudio/lm-probe-token",
        "audio_base_url": "http://127.0.0.1:8555/v1",
        "tts_model": "mlx-community/chatterbox-turbo-fp16",
        "stt_model": "mlx-community/whisper-large-v3-turbo",
        "speech_enabled": True,
        "transcriptions_enabled": False,
        "transcript_tap": True,
        "transcript_dir": "transcripts",
    }
    cfg.update(sv)
    return cfg


def _session_datetime_str() -> str:
    """Local wall-clock stamp for the {{datetime}} slot: weekday, date, time, tz.

    Read ONCE at load (session start) and frozen for the session — a per-turn
    refresh would change the cached system-prompt prefix every turn (defeating LM
    Studio's prompt-prefix KV reuse) and nudge the model into announcing the time
    each turn. macOS-only build, so the %-d/%-I no-pad flags are safe.
    """
    return datetime.now().astimezone().strftime("%A, %B %-d, %Y at %-I:%M %p %Z")


def compose_with_persona(model_name: str, persona_text: str, *, datetime_str: str | None = None) -> str:
    """Render the MODEL template with {{persona}} filled by the given persona text.

    The composition primitive shared by two callers:
      - load-time: compose_system_instruction() passes the character's persona.md body;
      - live-reload: config_reload passes an operator-supplied persona override.

    Because the MODEL template — which carries the spoken/no-markdown HARD RULES —
    is always the wrapper and ONLY the {{persona}} slot is substituted, a live
    persona override can never drop those hard rules: they are pinned by
    construction. (This is why the live [llm] override key is `persona`, not a raw
    `system_instruction` — config_reload §persona-slot.)
    """
    tpl_path = MODELS_DIR / model_name / "system-prompt-template.md"
    template = _strip_comments(_read_text(tpl_path)).strip()
    if _PERSONA_SLOT not in template:
        raise ConfigError(f"system-prompt-template.md has no {_PERSONA_SLOT} slot: {tpl_path}")
    composed = template.replace(_PERSONA_SLOT, persona_text)
    # Optional one-time clock. The {{datetime}} slot (if the template has one) is
    # filled ONCE here at load = session start, then frozen. datetime_str="" is
    # passed for the drift fingerprint (stable hash → no false resume warning);
    # None in production (→ live clock). Use `is not None` so "" is honored, not
    # treated as falsy-and-replaced-with-now.
    if _DATETIME_SLOT in composed:
        stamp = datetime_str if datetime_str is not None else _session_datetime_str()
        composed = composed.replace(_DATETIME_SLOT, stamp)
    # Optional OpenClaw-bridge capability paragraph (D3: model layer). Present in
    # the rendered prompt ONLY while config/openclaw.toml enables the bridge —
    # the same gate that registers the tools (openclaw_bridge.maybe_attach), so
    # prompt and capability appear/disappear together. Slot filling is
    # deterministic, so it participates in the drift fingerprint: toggling the
    # bridge (or editing prompt_block) warns on resume of pre-toggle sessions,
    # by design. Disabled ⇒ the slot LINE is removed (with its following blank
    # line) so the composed prompt stays byte-identical to the pre-bridge render.
    if _OPENCLAW_SLOT in composed:
        block = _openclaw_prompt_block()
        if block:
            composed = composed.replace(_OPENCLAW_SLOT, block)
        else:
            composed = composed.replace(_OPENCLAW_SLOT + "\n\n", "", 1)
            composed = composed.replace(_OPENCLAW_SLOT, "")  # defensive: odd slot placement
    return composed


def compose_system_instruction(model_name: str, character: str, *, datetime_str: str | None = None) -> str:
    """Render the MODEL template with {{persona}} filled from the CHARACTER.

    The composed string is what OpenAILLMService.Settings.system_instruction
    receives. If the template has a {{datetime}} slot it is filled with the local
    session-start clock (datetime_str=None → live), so system_instruction is NOT
    byte-stable across sessions. Drift detection therefore hashes the datetime-free
    fingerprint (ActiveConfig.prompt_fingerprint = this with datetime_str=""),
    NOT system_instruction — see load_active + session_store.prompt_sha256.
    """
    return compose_with_persona(model_name, compose_persona(character), datetime_str=datetime_str)


# ── top-level entry point ────────────────────────────────────────────────────

def load_active() -> ActiveConfig:
    """Resolve the full active configuration from config/active.toml.

    Raises ConfigError (naming the file) on any missing/malformed input. Call
    once at startup; the result is what bot.py wires into the pipeline.
    """
    sel = load_active_selection()
    model = load_model(sel["model"])
    voice = load_voice(sel["character"], sel["voice"])
    system_instruction = compose_system_instruction(sel["model"], sel["character"])
    # Drift-hash basis: same compose with the {{datetime}} slot emptied, so
    # prompt_sha256 stays stable across sessions (the live clock varies every run).
    prompt_fingerprint = compose_system_instruction(sel["model"], sel["character"], datetime_str="")

    model_path = MODELS_DIR / sel["model"] / "model.toml"
    return ActiveConfig(
        character=sel["character"],
        model_name=sel["model"],
        voice_name=sel["voice"],
        model_id=model["id"],
        temperature=float(_require(model, "temperature", model_path)),
        reasoning_effort=_require(model, "reasoning_effort", model_path),
        needs_template_edit=bool(model.get("needs_template_edit", False)),
        no_kv_reuse=bool(model.get("no_kv_reuse", False)),
        system_instruction=system_instruction,
        prompt_fingerprint=prompt_fingerprint,
        voice_tag=voice["tag"],
        ref_wav=voice["ref_wav"],
        model_repo=voice.get("model_repo"),
        sample_rate=voice.get("sample_rate"),
        streaming_interval=voice.get("streaming_interval"),
        reliable_context=model.get("reliable_context"),
    )
