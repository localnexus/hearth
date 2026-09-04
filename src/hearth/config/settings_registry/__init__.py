"""settings_registry — one declared schema per Hearth config file.

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
  3. the generated settings forms (supervisor/settings.py, /admin/settings/ui)
     — json_schema() is that contract, carrying an `x-hearth` extra per field:
     a live path (`hot_via`), an effect-time stamp (`effect`/`effect_note`),
     or a `secret` marker (value redacted on read, refused on write).

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

── the package layout ────────────────────────────────────────────────────────

This was one 47 KB file. It is now seven, in dependency order, and THIS module
re-exports every name each of them defines: `from hearth.config import
settings_registry` and every `sr.<name>` in the tree is unchanged, which is the
point — the split is a source-layout change and nothing else.

    knobs          the single-source knob surfaces, the three x-hearth stamp
                   factories (_live / _secret / _effect), and the _Cfg base
    schema_files   the per-FILE schemas: active, model, voice, overrides,
                   tts-baseline, vad, and the profile mirror
    schema_tables  the per-TABLE schemas: [serve], [memory], [openclaw] —
                   each a gate living inside a shared config file
    registry       FileEntry, REGISTRY, ENV_VARS
    validate       SchemaError and the shape checks, plus json_schema()
    manual         the generated settings reference (markdown)
    derived        the honored surfaces the live modules build at import

Imports run strictly downward that list; a part reaching UPWARD is a cycle and
the slice tooling refuses it. Where to add: a new config file is a schema in
`schema_files`/`schema_tables` plus a `FileEntry` in `registry` — the same two
edits as before, now in two files instead of two places in one.
"""

from __future__ import annotations

from .knobs import (
    CANONICAL_TAGS,
    ENGINE_LIVE_KNOBS,
    SERVE_SPEECH_KNOBS,
    TEMP_CEILING,
    TURBO_LIVE_KNOBS,
    _Cfg,
    _NAME,
    _effect,
    _live,
    _secret,
)
from .schema_files import (
    ActiveFile,
    ModelFile,
    OverridesFile,
    ProfileFile,
    TtsBaselineFile,
    VadFile,
    VoiceFile,
    _OvLLM,
    _OvTTS,
    _OvVAD,
    _OvVoice,
    _TagProfile,
    _TtsLive,
    _VadLive,
)
from .schema_tables import (
    MemoryTable,
    OpenclawTable,
    ServeTable,
    _MemHindsight,
    _MemIntent,
    _MemPerTurn,
    _MemServe,
    _PER_TURN_NOTE,
    _SIDECAR_NOTE,
    _ServeIdentity,
    _ServeIdentityTts,
    _ServeSupervisor,
    _SupActuator,
    _SupWatch,
)
from .registry import ENV_VARS, FileEntry, REGISTRY
from .validate import (
    SchemaError,
    _WARN_TYPES,
    _dict_value_model_of,
    _model_of,
    _unknown_keys,
    _validate,
    json_schema,
    loader_check,
    strict_check,
)
from .manual import (
    MANUAL_PAGES,
    _HEADER_ROW,
    _constraints,
    _default_str,
    _field_rows,
    _render_page,
    _type_name,
    generate_manual_pages,
)
from .derived import (
    _field_bounds,
    _field_is_int,
    live_knob_ranges,
    llm_knob_facts,
    vad_fallback,
)

#: The public surface. The underscore names above are re-exported too, and
#: deliberately: supervisor/settings.py walks the declared models through
#: `_model_of` / `_dict_value_model_of`, and the tests reach for the private
#: sub-models by name. Leaving them out would have made this split a breaking
#: change dressed up as a move.
__all__ = [
    "ActiveFile", "CANONICAL_TAGS", "ENGINE_LIVE_KNOBS", "ENV_VARS",
    "FileEntry", "MANUAL_PAGES", "MemoryTable", "ModelFile", "OpenclawTable",
    "OverridesFile", "ProfileFile", "REGISTRY", "SERVE_SPEECH_KNOBS",
    "SchemaError", "ServeTable", "TEMP_CEILING", "TURBO_LIVE_KNOBS",
    "TtsBaselineFile", "VadFile", "VoiceFile", "generate_manual_pages",
    "json_schema", "live_knob_ranges", "llm_knob_facts", "loader_check",
    "strict_check", "vad_fallback",
]
