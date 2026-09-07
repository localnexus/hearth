# Settings reference — selection, models, voices, live knobs, weights roots

> **GENERATED — do not hand-edit.** Source of truth: the settings registry
> (`hearth/config/settings_registry/`). Regenerate both pages:
> `python -m hearth.config.check --emit-manual <this directory>`; a test fails on drift.

Companion page: [settings-reference-gates.md](settings-reference-gates.md). **Live path** = how a setting hot-applies at the next turn
boundary: a `config/overrides.toml` dotted key (the panel writes that layer), or the launch page's
*switch intent* for the selection fields (the COMPANION button / `/admin/switch`).
**Restart** (in each
section header) = what must relaunch for a persisted edit to land: *the companion* = the voice
pipeline (`start.sh`, or the launch page) · *Hearth* = the running program (`hearth.serve`) ·
*none* = applies live. Strict validation
of your install: `python -m hearth.config.check`.

## `config/active.toml` — The selection pointer

*place scope · operator-owned · selection · restart: the companion and Hearth*

Your one deliberate lever for who is live. Read once at startup; the launch page's switch button writes it and applies it live at the next turn boundary (or via a warm restart) — hand-edit + restart keeps working. Hearth re-reads it when restarted (a [serve.identity] pin keeps its own voice regardless).

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `character` | str | **required** |  | `switch intent (turn boundary)` | who is live — dir under characters/ |
| `model` | str | **required** |  | `switch intent (turn boundary; resident models only)` | model config — dir under config/models/ |
| `voice` | str | **required** |  | `switch intent (turn boundary)` | voice bundle — dir under characters/<character>/voices/ |
| `persona` | str | `default` |  | `switch intent (turn boundary)` | persona variant: "default" = persona.md, else persona.<name>.md |

## `config/models/<model>/model.toml` — Model load facts

*model scope · operator-owned · load facts · restart: the companion*

Per-model request facts. context_length is deliberately absent — the live server's loaded value wins. Hearth re-reads it when restarted.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `id` | str | **required** |  | — | model id your inference server advertises, verbatim |
| `temperature` | float | **required** | 0.0–2.0 | `llm.temperature` | request-side sampling temperature |
| `reasoning_effort` | enum(none | low | medium | high) | **required** |  | `llm.reasoning_effort` | request-side reasoning control; "none" is a harmless no-op on non-reasoning models |
| `needs_template_edit` | bool | `false` |  | — | model needs a persistent chat-template edit (e.g. thinking off) |
| `no_kv_reuse` | bool | `false` |  | — | true if prefix KV-cache reuse is unsafe for this model |
| `reliable_context` | int | — | 1– | — | measured usable-context ceiling the panel's token gauge counts against |
| `weights` | table | — |  | — | the enrolled weights file — a REFERENCE, written by `python -m hearth.weights enroll` and never by hand |
| `server` | map(str → Any) | — |  | — | door flags for this model: keys are llama-server LONG FLAGS without the leading dashes (c, parallel, kv-unified, mmproj, chat-template-file, spec-type, alias, ...), validated against the door's own --help by `python -m hearth.weights check` and consumed by the unit renderer. Door-level facts (port, api-key-file, load-mode, threads) do NOT belong here |
| `weights.path` | str | **required** |  | — | resolved REAL path of the weights file (symlinks followed once, at enroll) |
| `weights.mmproj` | str | — |  | — | resolved real path of the projector, if the model has one |
| `weights.root` | str | `` |  | — | which root it was found under (provenance) |
| `weights.layout` | str | `` |  | — | the reader that found it: plain | ollama | hf |
| `weights.display_key` | str | `` |  | — | how the scan names it |
| `weights.size_bytes` | int | `0` | 0– | — | size on the day it was enrolled (all shards summed) |
| `weights.identity` | str | `` |  | — | sha256(size ‖ first 1 MiB)[:16] — the duplicate/drift key |
| `weights.enrolled` | str | `` |  | — | ISO date the reference was written |
| `weights.header` | table | — |  | — | header facts as read that day |
| `weights.header.architecture` | str | — |  | — | GGUF general.architecture |
| `weights.header.name` | str | — |  | — | GGUF general.name |
| `weights.header.block_count` | int | — | 0– | — | transformer blocks |
| `weights.header.context_length` | int | — | 0– | — | context the file was trained for |
| `weights.header.head_count` | int | — | 0– | — | attention heads |
| `weights.header.head_count_kv` | int | — | 0– | — | key/value heads (KV-cache cost) |
| `weights.header.key_length` | int | — | 0– | — | key head dimension |
| `weights.header.value_length` | int | — | 0– | — | value head dimension |
| `weights.header.full_attention_interval` | int | — | 1– | — | hybrid builds: only every Nth block holds a KV cache |
| `weights.header.expert_count` | int | — | 0– | — | mixture-of-experts count |
| `weights.header.nextn_predict_layers` | int | — | 0– | — | speculative (MTP) layers |
| `weights.header.file_type` | int | — | 0– | — | GGUF general.file_type (quantisation) |
| `weights.header.tensor_bytes` | int | — | 0– | — | sum of tensor bytes = the weights themselves |

## `characters/<character>/voices/<voice>/voice.toml` — Voice bundle descriptor

*identity scope · operator-owned · descriptor · restart: the companion*

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
| `llm.temperature` | float | — | 0.0–2.0 | — | live model temperature |
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

*place scope · shipped-owned · calibration · restart: the companion*

Every [live] value equals the engine's own default (machine-checked no-op guarantee). [tag_profiles.*] deltas are ear-calibrated — change by listening. A copy in your data folder wins whole-file. Hearth re-reads it per speech request.

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

*place scope · shipped-owned · calibration · restart: the companion*

Mic, room, and speech-habit calibration — plumbing, never character texture; profiles never carry it. A copy in your data folder wins whole-file.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `live` | table | — |  | — | mic/room/operator calibration — never carried by character or voice profiles |
| `live.confidence` | float | `0.7` | 0.0–1.0 | `vad.confidence` | how sure the VAD must be a sound is speech |
| `live.start_secs` | float | `0.2` | 0.05–1.0 | `vad.start_secs` | sustained sound before 'you started talking' |
| `live.stop_secs` | float | `0.5` | 0.2–3.0 | `vad.stop_secs` | silence after speech before 'you finished' |
| `live.min_volume` | float | `0.6` | 0.0–1.0 | `vad.min_volume` | loudness floor to count as speech |

## `config/weights.toml` — Weights roots

*place scope · operator-owned · load facts · restart: none*

WHERE Hearth is willing to look for model weights — directories, nothing more. The product pointers are exactly that: paths to the folders LM Studio, Ollama and the Hugging Face cache keep files in. No other program's background service, command line, or private cache is ever used, so a scan answers the same with all of them quit or uninstalled. Enrollment itself lives in each model directory's [weights] table; Hearth never downloads, moves, or deletes a weights file. See `python -m hearth.weights`.

All keys below live under the `[weights]` table.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `roots` | list | — |  | — | directories Hearth may look in for weights (~ expanded) |
| `product_dirs` | bool | `true` |  | — | also point at LM Studio / Ollama / Hugging Face cache directories when they exist — POINTERS ONLY: no other program is ever run, asked, or read for its cache |
| `landing` | str | — |  | — | Hearth's own folder for weights that arrive from here on, subdivided by role (`llm/` `tts/` `stt/`); defaults to <first root>/hearth |
| `llama_server` | str | — |  | — | the door binary, used to read this machine's memory budget and to validate [server] keys; defaults to the one on PATH |

## `characters/<c>[/voices/<v>]/profile.toml (+ overrides.toml mirrors)` — Companion knob presets

*identity scope · panel-owned · preset · restart: none*

PANEL-MANAGED snapshots of the override deltas for one companion or voice; they travel with the companion's directory. An empty preset == baseline. One key is yours, not the panel's: `voice` in a CHARACTER profile pins the bundle the switch pickers offer when you move to that character — hand-edit it, and a panel save carries it through untouched.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `voice` | str | — |  | — | character scope only: that character's remembered voice bundle — what the switch pickers offer when you move to them (else first-in-list) |
| `llm` | table | — |  | — | character preset: deltas from the model baseline |
| `tts` | table | — |  | — | voice preset: deltas from the engine baseline |
| `llm.temperature` | float | — | 0.0–2.0 | — | live model temperature |
| `llm.reasoning_effort` | enum(none | low | medium | high) | — |  | — | live reasoning control |
| `llm.persona` | str | — | ≤ 16000 chars | — | live {{persona}} slot text (template hard-rules stay pinned) |
| `tts.temperature` | float | — | 0.0–2.0 | — | synth temperature (intonation looseness) |
| `tts.top_p` | float | — | 0.0–1.0 | — | synth nucleus sampling |
| `tts.top_k` | int | — | 1–10000 | — | synth top-k |
| `tts.repetition_penalty` | float | — | 0.5–5.0 | — | synth repetition penalty |
