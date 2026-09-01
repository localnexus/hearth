# Settings reference — selection, models, voices, live knobs

> **GENERATED — do not hand-edit.** Source of truth: the settings registry
> (`hearth/config/settings_registry.py`). Regenerate both pages:
> `python -m hearth.config.check --emit-manual <this directory>`; a test fails on drift.

Companion page: [settings-reference-gates.md](settings-reference-gates.md). **Live path** = how a setting hot-applies at the next turn
boundary: a `config/overrides.toml` dotted key (the panel writes that layer), or the supervisor's
*switch intent* for the selection fields (the COMPANION button / `/admin/switch`, ADR 007 stroke 3).
**Restart** (in each
section header) = what must relaunch for a persisted edit to land: *bot* = the desk pipeline
(`start.sh`) · *facade* = the serve facade (kickstart) · *none* = applies live. Strict validation
of your install: `python -m hearth.config.check`.

## `config/active.toml` — The selection pointer

*place scope · operator-owned · selection · restart: bot+facade*

Your one deliberate lever for who is live. Read once at startup; the supervisor's switch button writes it and applies it live at the next turn boundary (or via a warm restart) — hand-edit + restart keeps working. The facade re-reads at kickstart (a [serve.identity] pin keeps its own voice regardless).

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `character` | str | **required** |  | `switch intent (turn boundary)` | who is live — dir under characters/ |
| `model` | str | **required** |  | `switch intent (turn boundary; resident models only)` | model config — dir under config/models/ |
| `voice` | str | **required** |  | `switch intent (turn boundary)` | voice bundle — dir under characters/<character>/voices/ |
| `persona` | str | `default` |  | `switch intent (turn boundary)` | persona variant: "default" = persona.md, else persona.<name>.md |

## `config/models/<model>/model.toml` — Model load facts

*model scope · operator-owned · load facts · restart: bot*

Per-model request facts. context_length is deliberately absent — the live server's loaded value wins. The facade re-reads at kickstart.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `id` | str | **required** |  | — | model id your inference server advertises, verbatim |
| `temperature` | float | **required** | 0.0–2.0 | `llm.temperature` | request-side sampling temperature |
| `reasoning_effort` | enum(none | low | medium | high) | **required** |  | `llm.reasoning_effort` | request-side reasoning control; "none" is a harmless no-op on non-reasoning models |
| `needs_template_edit` | bool | `false` |  | — | model needs a persistent chat-template edit (e.g. thinking off) |
| `no_kv_reuse` | bool | `false` |  | — | true if prefix KV-cache reuse is unsafe for this model |
| `reliable_context` | int | — | 1– | — | measured usable-context ceiling the panel's token gauge counts against |

## `characters/<character>/voices/<voice>/voice.toml` — Voice bundle descriptor

*identity scope · operator-owned · descriptor · restart: bot*

A voice is a self-contained bundle: descriptor + reference clip in one directory. The clip conditions once at startup.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `tag` | str | **required** |  | — | human-readable voice tag, recorded with sessions (voice-drift warning) |
| `ref_wav` | str | **required** |  | `voice.ref_wav (session-scoped)` | clone reference clip; relative = beside this descriptor |
| `license` | str | — |  | — | clip license (provenance) |
| `source` | str | — |  | — | clip source (provenance) |
| `model_repo` | str | — |  | — | synth model the clip is prepared for (doc) |
| `sample_rate` | int | — | 1– | — | clip sample rate, Hz (doc) |
| `streaming_interval` | float | — | 0.0– | — | synth chunk interval, s (doc) |

## `config/overrides.toml` — The live override layer

*place scope · panel-owned · live overrides · restart: none*

PANEL-MANAGED. Polled every turn boundary; values overlay the baselines (delete a key and it reverts). [voice].ref_wav is session-scoped.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `llm` | table | — |  | — | per-character reasoning/sampling overrides |
| `tts` | table | — |  | — | per-voice synthesis overrides |
| `vad` | table | — |  | — | listening calibration overrides |
| `voice` | table | — |  | — | live voice-clip audition |
| `llm.temperature` | float | — | 0.0–2.0 | — | live LLM temperature |
| `llm.reasoning_effort` | enum(none | low | medium | high) | — |  | — | live reasoning control |
| `llm.persona` | str | — | ≤ 16000 chars | — | live {{persona}} slot text (template hard-rules stay pinned) |
| `tts.temperature` | float | — | 0.0–2.0 | — | synth temperature (intonation looseness) |
| `tts.top_p` | float | — | 0.0–1.0 | — | synth nucleus sampling |
| `tts.top_k` | int | — | 1–10000 | — | synth top-k |
| `tts.repetition_penalty` | float | — | 0.5–5.0 | — | synth repetition penalty |
| `vad.confidence` | float | — | 0.0–1.0 | — | how sure the VAD must be a sound is speech |
| `vad.start_secs` | float | — | 0.05–1.0 | — | sustained sound before 'you started talking' |
| `vad.stop_secs` | float | — | 0.2–3.0 | — | silence after speech before 'you finished' |
| `vad.min_volume` | float | — | 0.0–1.0 | — | loudness floor to count as speech |
| `voice.ref_wav` | str | — |  | — | live voice audition (SESSION-SCOPED: scrubbed at next startup) |

## `config/tts/<engine>/tts.toml` — TTS engine baseline

*place scope · shipped-owned · calibration · restart: bot*

Every [live] value equals the engine's own default (machine-checked no-op guarantee). [tag_profiles.*] deltas are ear-calibrated — change by listening. A data-root copy wins whole-file. The facade re-reads per speech request.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `live` | table | — |  | — | knobs the engine honors live; every value == the engine's own default (no-op guarantee) |
| `tag_profiles` | tables | — |  | — | per-tag knob deltas for the one chunk carrying the tag; keys must be canonical tags |
| `inert` | map(str → Any) | — |  | — | documentation-only: knobs this engine accepts-but-drops; never passed |
| `live.temperature` | float | `0.8` | 0.0–2.0 | `tts.temperature` | baseline synth temperature (== engine default) |
| `live.top_p` | float | `0.95` | 0.0–1.0 | `tts.top_p` | baseline nucleus sampling (== engine default) |
| `live.top_k` | int | `1000` | 1–10000 | `tts.top_k` | baseline top-k (== engine default) |
| `live.repetition_penalty` | float | `1.2` | 0.5–5.0 | `tts.repetition_penalty` | baseline repetition penalty (== engine default) |

## `config/vad.toml` — Listening calibration

*place scope · shipped-owned · calibration · restart: bot*

Mic, room, and speech-habit calibration — plumbing, never character texture; profiles never carry it. A data-root copy wins whole-file.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `live` | table | — |  | — | mic/room/operator calibration — never carried by character or voice profiles |
| `live.confidence` | float | `0.7` | 0.0–1.0 | `vad.confidence` | how sure the VAD must be a sound is speech |
| `live.start_secs` | float | `0.2` | 0.05–1.0 | `vad.start_secs` | sustained sound before 'you started talking' |
| `live.stop_secs` | float | `0.5` | 0.2–3.0 | `vad.stop_secs` | silence after speech before 'you finished' |
| `live.min_volume` | float | `0.6` | 0.0–1.0 | `vad.min_volume` | loudness floor to count as speech |

## `characters/<c>[/voices/<v>]/profile.toml (+ overrides.toml mirrors)` — Companion knob presets

*identity scope · panel-owned · preset · restart: none*

PANEL-MANAGED snapshots of the override deltas for one companion or voice; they travel with the companion's directory. An empty preset == baseline.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `llm` | table | — |  | — | character preset: deltas from the model baseline |
| `tts` | table | — |  | — | voice preset: deltas from the engine baseline |
| `llm.temperature` | float | — | 0.0–2.0 | — | live LLM temperature |
| `llm.reasoning_effort` | enum(none | low | medium | high) | — |  | — | live reasoning control |
| `llm.persona` | str | — | ≤ 16000 chars | — | live {{persona}} slot text (template hard-rules stay pinned) |
| `tts.temperature` | float | — | 0.0–2.0 | — | synth temperature (intonation looseness) |
| `tts.top_p` | float | — | 0.0–1.0 | — | synth nucleus sampling |
| `tts.top_k` | int | — | 1–10000 | — | synth top-k |
| `tts.repetition_penalty` | float | — | 0.5–5.0 | — | synth repetition penalty |
