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

from loguru import logger

# ── the two anchors ──────────────────────────────────────────────────────────
#
# HEARTH_ROOT — the ENGINE tree: this package plus what ships with it (the calibrated
#   baselines under config/tts/ and config/vad.toml, the example model config, the
#   example character). Versioned, public, replaceable. Resolved three levels above
#   this module (src/hearth/config/ → the checkout) unless HEARTH_ROOT says otherwise.
# HEARTH_DATA — everything the operator OWNS: characters (persona, voices, and each
#   companion's sessions / transcripts / captures), model configs, the selection
#   (active.toml), the panel's overrides, serve.toml + its token. Never versioned.
#   Defaults to HEARTH_ROOT, so an unconfigured checkout behaves exactly as before:
#   set it to keep companions outside the checkout (a vault, ~/.hearth, anywhere).
#
# Lookup rule: identity and model scope resolve DATA first, then ROOT — so the shipped
# `example` character/model stay reachable from an empty data root, and an operator's
# own `characters/example/` shadows the shipped one. Runtime state always lands under
# DATA (never inside the engine tree). Baselines are read from ROOT unless DATA carries
# its own copy (a per-machine calibration), which then wins whole-file.
_ROOT = Path(os.environ.get("HEARTH_ROOT") or Path(__file__).resolve().parents[3]).expanduser()
_DATA = Path(os.environ.get("HEARTH_DATA") or _ROOT).expanduser()

ROOT_CONFIG_DIR = _ROOT / "config"          # shipped baselines + the example model config
DATA_DIR = _DATA
CONFIG_DIR = _DATA / "config"               # place scope: selection, overrides, serve, openclaw
MODELS_DIR = CONFIG_DIR / "models"          # model scope (operator's); ROOT holds the example
CHARACTERS_DIR = _DATA / "characters"       # identity scope (operator's); ROOT holds the example
ACTIVE_TOML = CONFIG_DIR / "active.toml"

OPENCLAW_TOML = CONFIG_DIR / "openclaw.toml"
SERVE_TOML = CONFIG_DIR / "serve.toml"
MEMORY_TOML = CONFIG_DIR / "memory.toml"

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")  # dir-name / variant-name safe; blocks traversal
_PERSONA_SLOT = "{{persona}}"
_DATETIME_SLOT = "{{datetime}}"
_OPENCLAW_SLOT = "{{openclaw_tools}}"


class ConfigError(RuntimeError):
    """A config file is missing, malformed, or missing a required key.

    The message always names the exact offending file so a startup failure is
    self-diagnosing (fail-fast; no silent fallback to literals).
    """


if not (_ROOT / "config").is_dir():  # fail-fast at import: a wrong root is never silent
    raise ConfigError(
        f"Hearth engine tree not found: no config/ under {_ROOT}. Set HEARTH_ROOT to the "
        "checkout (a non-editable install cannot locate it by itself)."
    )


# ── root lookups (DATA first, then ROOT) ─────────────────────────────────────

def _lookup(rel: str) -> Path:
    """DATA/rel if it exists, else ROOT/rel if it exists, else DATA/rel (so an error
    names the path the operator is expected to create).

    Always keyed on a FILE, never a directory: a companion's directory under DATA comes
    into existence the moment its runtime state is written (sessions/, a profile), and
    an empty directory must not shadow the shipped definition next to it."""
    d = _DATA / rel
    if d.is_file():
        return d
    r = _ROOT / rel
    if r.is_file():
        return r
    return d


def model_dir(model_name: str) -> Path:
    """config/models/<model_name>/ — the operator's copy under DATA (keyed on its
    model.toml), else the shipped one."""
    return _lookup(f"config/models/{model_name}/model.toml").parent


def character_dir(character: str) -> Path:
    """characters/<character>/ (the DEFINITION, keyed on its persona.md) — DATA, else ROOT."""
    return _lookup(f"characters/{character}/persona.md").parent


def voice_dir(character: str, voice: str) -> Path:
    """characters/<character>/voices/<voice>/ (keyed on its voice.toml) — DATA, else ROOT.
    Looked up per voice, so an operator can add a voice to the shipped example under
    DATA without copying the persona."""
    return _lookup(f"characters/{character}/voices/{voice}/voice.toml").parent


def list_voices(character: str) -> list:
    """Voice bundle names (dirs holding a voice.toml) across DATA and ROOT, merged."""
    names = set()
    for root in (_DATA, _ROOT):
        names.update(p.parent.name for p in (root / "characters" / character / "voices").glob("*/voice.toml"))
    return sorted(names)


def baseline_path(rel: str) -> Path:
    """A shipped calibration file (config/tts/<engine>/tts.toml, config/vad.toml): the
    DATA copy wins whole-file when present; otherwise the ROOT baseline."""
    return _lookup(f"config/{rel}")


def resolve_data_path(ref: str) -> Path:
    """A relative operator path (e.g. an overrides [voice].ref_wav) → absolute, DATA
    first then ROOT (the shipped example clip). Absolute inputs pass through."""
    p = Path(ref).expanduser()
    if p.is_absolute():
        return p
    return _lookup(str(p))


def companion_data_dir(character: str) -> Path:
    """DATA/characters/<character>/ — the companion's OWN directory: where its runtime
    state, saved profiles, and knob mirrors live. Always under DATA, never the engine
    tree, even when the character DEFINITION is the shipped one under ROOT."""
    if not _NAME_RE.match(character or "") or character.startswith("."):
        raise ConfigError(f"invalid character name: {character!r}")
    return _DATA / "characters" / character


def companion_state_dir(character: str, kind: str) -> Path:
    """DATA/characters/<character>/<kind>/ (kind = sessions | transcripts | captures)."""
    return companion_data_dir(character) / kind


def persona_path(character: str, persona: str | None = None) -> Path:
    """persona.md, or the variant file persona.<variant>.md, beside it."""
    if persona in (None, "", "default"):
        return _lookup(f"characters/{character}/persona.md")
    if not _NAME_RE.match(persona) or persona.startswith("."):
        raise ConfigError(f"invalid persona variant name: {persona!r}")
    return _lookup(f"characters/{character}/persona.{persona}.md")  # a variant may live in DATA alone


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


def _schema_check(kind: str, data: dict, path: Path) -> None:
    """Registry-backed shape check (settings_registry — schema-driven settings, step 1).

    Lenient at load time by design: UNKNOWN keys and out-of-range values only
    WARN (an operator's extra key or bold value never blocks a boot that works
    today); a TYPE violation on a present key fails fast naming the file —
    that class otherwise dies later as a raw traceback deep in the pipeline.
    Required-ness stays with the callers' _require (their messages are the
    contract). Strict validation lives in `python -m hearth.config.check`.
    """
    from hearth.config import settings_registry
    try:
        notes = settings_registry.loader_check(kind, data)
    except settings_registry.SchemaError as exc:
        raise ConfigError(f"invalid value in {path}: {exc}") from exc
    for note in notes:
        logger.warning("config: {} — {}", path, note)


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
    persona_name: str = "default"  # which persona file: "default" = persona.md, else persona.<name>.md


# ── individual loaders (composable; used by load_active() below) ──────────────

def load_active_selection() -> dict:
    """Read config/active.toml → {'character', 'model', 'voice', 'persona'}.

    `persona` is optional: absent → "default" (the character's persona.md); a name
    selects the sibling variant file persona.<name>.md."""
    data = _read_toml(ACTIVE_TOML)
    _schema_check("active", data, ACTIVE_TOML)
    persona = str(data.get("persona", "default") or "default")
    return {
        "character": _require(data, "character", ACTIVE_TOML),
        "model": _require(data, "model", ACTIVE_TOML),
        "voice": _require(data, "voice", ACTIVE_TOML),
        "persona": persona,
    }


def load_model(model_name: str) -> dict:
    """Read config/models/<model_name>/model.toml (DATA, else the shipped one). Requires 'id'."""
    path = model_dir(model_name) / "model.toml"
    data = _read_toml(path)
    _schema_check("model", data, path)
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
    vdir = voice_dir(character, voice)
    path = vdir / "voice.toml"
    data = _read_toml(path)
    _schema_check("voice", data, path)
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


def compose_persona(character: str, persona: str | None = None) -> str:
    """Extract the {{persona}} text from characters/<character>/persona.md — or, when
    `persona` names a variant, from the sibling file persona.<variant>.md.

    The file is authored as two labelled sections ('## IDENTITY', '## SOUL')
    with HTML-comment guidance. The composed persona is the IDENTITY body plus a
    blank line plus the SOUL body — reproducing the original prompt's paragraph
    shape (identity paragraph first) byte-for-byte (plan §6, wiring-doc §4).
    """
    path = persona_path(character, persona)
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
    _schema_check("openclaw", oc, OPENCLAW_TOML)
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


def load_memory_config() -> dict | None:
    """Read config/memory.toml — the memory-seam activation gate.

    Returns the [memory] table with defaults applied, or None when the file is
    absent or enabled=false — the load_openclaw_config shape and contract: an
    absent optional file is NOT an error (memory off, engine byte-identical);
    malformed ⇒ ConfigError naming it. Backend selection is per companion:
    ``backend`` is the default for every companion, the [memory.companions]
    sub-table overrides it by name, and the value "none" opts a companion out.
    The seam (hearth.memory.maybe_attach) is the only consumer.

    [memory.intent] (intent-primed boot recall) is normalized here and always
    present in the returned dict, default disabled — with no config change the
    engine stays byte-identical. Its LLM settings fall back to
    [memory.hindsight]'s, which is where the local extraction model is already
    named; the intent lane calls that model directly (backend-independent), so
    floor companions get the feature too.

    [memory.serve] (the facade-lane seam: sessions for the /v1 door) is
    normalized the same way and likewise always present, default disabled. The
    facade is stateless by construction, so the glue that gives it a session
    start and a graceful close needs its own gate and its own boundaries —
    idle_close_voice/idle_close_chat in MINUTES, and whether an open session
    checkpoints after each exchange so a crash leaves a recoverable orphan
    rather than a lost conversation.
    """
    if not MEMORY_TOML.exists():
        return None
    mem = _read_toml(MEMORY_TOML).get("memory")
    if not isinstance(mem, dict) or not mem.get("enabled"):
        return None
    _schema_check("memory", mem, MEMORY_TOML)
    cfg: dict = {
        "backend": "floor",
        "recall_limit": 6,
        "recall_query": "the user's life, preferences, and recent conversations",
        "companions": {},
    }
    cfg.update(mem)
    intent = dict(cfg.get("intent") or {})
    hindsight = dict(cfg.get("hindsight") or {})
    cfg["intent"] = {
        "enabled": bool(intent.get("enabled", False)),
        "expiry_days": int(intent.get("expiry_days", 14)),
        "llm_provider": str(intent.get("llm_provider")
                            or hindsight.get("llm_provider") or "ollama"),
        "llm_model": str(intent.get("llm_model") or hindsight.get("llm_model") or ""),
        "llm_url": str(intent.get("llm_url") or ""),
        "companions": dict(intent.get("companions") or {}),
    }
    serve = dict(cfg.get("serve") or {})
    cfg["serve"] = {
        "enabled": bool(serve.get("enabled", False)),
        # Minutes. Voice: a transport fact — once the voice server's own reaper
        # fires, that conversation cannot continue, so this is grace + margin.
        # Chat: the FALLBACK behind deliberate-closure close, set above the
        # longest plausible waking gap so an errand never splits a day in two.
        "idle_close_voice": int(serve.get("idle_close_voice", 5)),
        "idle_close_chat": int(serve.get("idle_close_chat", 480)),
        "checkpoint": bool(serve.get("checkpoint", True)),
    }
    return cfg


def load_serve_config() -> dict | None:
    """Read config/serve.toml — the /v1 serve-facade activation gate (M8 / convergence P1).

    Returns the [serve] table with defaults applied, or None when the file is
    absent or enabled=false — the load_openclaw_config shape and contract: an
    absent optional file is NOT an error (facade off); malformed ⇒ ConfigError
    naming it. Unlike OpenClaw the facade is strictly I/O-edge — no prompt slot,
    no tool registration — so this gate never participates in prompt composition
    or the drift fingerprint (M8 §modular-gate: off = byte-identical appliance).

    token_source / lm_token_source are PATHS to secrets, never secrets;
    relative paths resolve against the data root (HEARTH_DATA). Env wins: SERVE_TOKEN for the
    facade bearer, LM_API_TOKEN for the LLM server key.

    Optional [serve.identity] table (character + voice, both required if the
    table exists): pins the facade's persona/voice to a FIXED selection instead
    of snapshotting active.toml — the facade then keeps its own identity no
    matter what the live session runs or when the facade was (re)started.
    Resolution happens in serve/app.py start(); here we only validate shape.

    Optional [serve.characters] table (character name → voice bundle name): the
    roster a client may pick from. It widens /v1/models beyond the resolved
    identity and gives the speech route a voice for each listed character;
    every name is validated here, because both halves land in filesystem
    lookups. Absent ⇒ an empty map and the single-identity behavior.
    """
    if not SERVE_TOML.exists():
        return None
    sv = _read_toml(SERVE_TOML).get("serve")
    if not isinstance(sv, dict) or not sv.get("enabled"):
        return None
    _schema_check("serve", sv, SERVE_TOML)
    ident = sv.get("identity")
    if ident is not None:
        if not isinstance(ident, dict):
            raise ConfigError(f"[serve.identity] must be a table: {SERVE_TOML}")
        for key in ("character", "voice"):
            if not str(ident.get(key, "")).strip():
                raise ConfigError(f"[serve.identity] requires non-empty '{key}': {SERVE_TOML}")
        if "tts" in ident and not isinstance(ident["tts"], dict):
            raise ConfigError(f"[serve.identity.tts] must be a table: {SERVE_TOML}")
    chars = sv.get("characters")
    if chars is not None:
        if not isinstance(chars, dict):
            raise ConfigError(f"[serve.characters] must be a table: {SERVE_TOML}")
        for name, bundle in chars.items():
            for label, value in (("character", name), ("voice bundle", bundle)):
                text = str(value or "")
                if not _NAME_RE.match(text) or text.startswith("."):
                    raise ConfigError(
                        f"[serve.characters] invalid {label} name {value!r}: {SERVE_TOML}")
    cfg: dict = {
        "host": "127.0.0.1",
        "port": 65001,
        "token_source": "config/serve-token",
        "lm_base_url": "http://127.0.0.1:8080/v1",   # llama-server default
        "lm_token_source": "",                        # keyless by default; a PATH if the server wants one
        "audio_base_url": "http://127.0.0.1:8555/v1",
        "tts_model": "mlx-community/chatterbox-turbo-fp16",
        "stt_model": "mlx-community/whisper-large-v3-turbo",
        "speech_enabled": True,
        "transcriptions_enabled": False,
        "transcript_tap": True,
        "transcript_dir": "transcripts",
        "characters": {},
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
    tpl_path = _lookup(f"config/models/{model_name}/system-prompt-template.md")
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


def compose_system_instruction(model_name: str, character: str, *, persona: str | None = None,
                               datetime_str: str | None = None) -> str:
    """Render the MODEL template with {{persona}} filled from the CHARACTER.

    The composed string is what OpenAILLMService.Settings.system_instruction
    receives. If the template has a {{datetime}} slot it is filled with the local
    session-start clock (datetime_str=None → live), so system_instruction is NOT
    byte-stable across sessions. Drift detection therefore hashes the datetime-free
    fingerprint (ActiveConfig.prompt_fingerprint = this with datetime_str=""),
    NOT system_instruction — see load_active + session_store.prompt_sha256.
    """
    return compose_with_persona(model_name, compose_persona(character, persona), datetime_str=datetime_str)


# ── top-level entry point ────────────────────────────────────────────────────

def load_active() -> ActiveConfig:
    """Resolve the full active configuration from config/active.toml.

    Raises ConfigError (naming the file) on any missing/malformed input. Call
    once at startup; the result is what bot.py wires into the pipeline.
    """
    sel = load_active_selection()
    model = load_model(sel["model"])
    voice = load_voice(sel["character"], sel["voice"])
    persona = sel["persona"]
    system_instruction = compose_system_instruction(sel["model"], sel["character"], persona=persona)
    # Drift-hash basis: same compose with the {{datetime}} slot emptied, so
    # prompt_sha256 stays stable across sessions (the live clock varies every run).
    prompt_fingerprint = compose_system_instruction(sel["model"], sel["character"], persona=persona,
                                                    datetime_str="")

    model_path = model_dir(sel["model"]) / "model.toml"
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
        persona_name=persona,
    )
