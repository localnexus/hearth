# Settings reference — the on/off files

> **GENERATED — do not hand-edit.** Source of truth: the settings registry
> (`hearth/config/settings_registry/`). Regenerate both pages:
> `python -m hearth.config.check --emit-manual <this directory>`; a test fails on drift.

Companion page: [settings-reference.md](settings-reference.md). **Live path** = how a setting hot-applies at the next turn
boundary: a `config/overrides.toml` dotted key (the panel writes that layer), or the launch page's
*switch intent* for the selection fields (the COMPANION button / `/admin/switch`).
**Restart** (in each
section header) = what must relaunch for a persisted edit to land: *the companion* = the voice
pipeline (`start.sh`, or the launch page) · *Hearth* = the running program (`hearth.serve`) ·
*none* = applies live. Strict validation
of your install: `python -m hearth.config.check`.

## `config/serve.toml` — The Hearth on/off switch

*place scope · operator-owned · on/off switch · restart: Hearth*

Holds the PATH to the access key: manage it, never print it. Off ⇒ byte-identical appliance, no socket.

All keys below live under the `[serve]` table.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `enabled` | bool | `false` |  | — | master switch: off ⇒ nothing loads, no socket (byte-identical appliance) |
| `host` | str | `127.0.0.1` |  | — | bind address — loopback by default; NEVER 0.0.0.0 without an overlay network |
| `port` | int | `65001` | 1–65535 | — | the port Hearth listens on |
| `token_source` | str | `config/serve-token` |  | — | PATH to the access key file — never the key itself |
| `lm_base_url` | str | `http://127.0.0.1:8080/v1` |  | — | upstream OpenAI-compatible model server endpoint |
| `lm_token_source` | str | `` |  | — | PATH to the model server's key file, if it wants one (env LM_API_TOKEN wins) |
| `audio_base_url` | str | `http://127.0.0.1:8555/v1` |  | — | upstream speech server (mlx-audio) |
| `tts_model` | str | `mlx-community/chatterbox-turbo-fp16` |  | — | speech-synthesis model id sent upstream |
| `stt_model` | str | `mlx-community/whisper-large-v3-turbo` |  | — | transcription model id sent upstream |
| `speech_enabled` | bool | `true` |  | — | serve /v1/audio/speech (voice out) |
| `transcriptions_enabled` | bool | `false` |  | — | serve /v1/audio/transcriptions (voice in) — ships OFF |
| `transcript_tap` | bool | `true` |  | — | append each turn to a per-companion plaintext transcript |
| `transcript_dir` | str | `transcripts` |  | — | relative ⇒ inside each companion's own directory; absolute used as-is |
| `identity` | table | — |  | — | fixed identity for Hearth's served lane instead of the active.toml snapshot |
| `characters` | map(str → str) | — |  | — | roster a client may declare: character name → its voice bundle (/v1/models) |
| `supervisor` | table | — |  | — | the launch page: standalone Hearth owns the voice companion as a child process; absent/off ⇒ byte-identical |
| `identity.character` | str | **required** |  | — | pinned character for Hearth's served lane (independent of active.toml) |
| `identity.voice` | str | **required** |  | — | pinned voice bundle for that character |
| `identity.tts` | table | — |  | — | pinned synth knobs; win over client body and live layer |
| `identity.tts.temperature` | float | — | 0.0–2.0 | — | pinned synth temperature (speech served by Hearth) |
| `identity.tts.top_p` | float | — | 0.0–1.0 | — | pinned top_p |
| `identity.tts.top_k` | int | — | 1–10000 | — | pinned top_k |
| `identity.tts.repetition_penalty` | float | — | 0.5–5.0 | — | pinned repetition penalty |
| `identity.tts.speed` | float | — | 0.0– | — | pinned playback-rate multiplier (upstream mlx-audio) |
| `identity.tts.allow_tag_profiles` | bool | `false` |  | — | policy flag: may per-tag profiles overlay the pin? never forwarded upstream |
| `supervisor.enabled` | bool | `false` |  | — | mount the launch page (/admin routes + panel proxy) when Hearth runs standalone |
| `supervisor.panel_url` | str | `http://127.0.0.1:65000` |  | — | the companion's control panel — proxy target + reachability probe |
| `supervisor.stop_grace_s` | float | `15.0` | 0.0– | — | seconds after SIGINT before escalating (memory-consolidation headroom) |
| `supervisor.term_grace_s` | float | `5.0` | 0.0– | — | seconds after SIGTERM before SIGKILL |
| `supervisor.env` | map(str → str) | — |  | — | extra child env for the spawned companion (e.g. LM_PROVIDER) — values never printed |
| `supervisor.watch` | tables | — |  | — | extra watched externals probed on /admin/state (watched, never owned) |
| `supervisor.actuators` | tables | — |  | — | declared external actuators: operator-fixed commands behind the door, never children |
| `supervisor.compact_watch` | bool | `true` |  | — | run the auto-compaction watch: close-time compaction requests (DATA/ops/compact-queue) execute once no companion is alive, arbitrated by the per-character maintenance lock |

## `config/memory.toml` — The memory on/off switch

*place scope · operator-owned · on/off switch · restart: the companion and Hearth*

Cross-session continuity per companion. Records are the truth; backends are derived indexes (`forget --session <id>` deletes one conversation from both; see docs/memory.md).

All keys below live under the `[memory]` table.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `enabled` | bool | `false` |  | — (lands at the next restart of the companion and Hearth) | master switch: off ⇒ engine byte-identical (no recall, no records) |
| `backend` | str | `floor` |  | — (lands at the next restart of the companion and Hearth) | default backend per companion: "floor" | "hindsight" | "none" |
| `recall_limit` | int | `6` | 0– | — (lands at the next restart of the companion and Hearth) | recalled items injected at session start (one dated line each) |
| `recall_query` | str | `the user's life, preferences, and recent conversations` |  | — (lands at the next restart of the companion and Hearth) | what recall asks the backend for (semantic backends only) |
| `companions` | map(str → str) | — |  | — (lands at the next restart of the companion and Hearth) | per-companion backend override (the continuity dial) |
| `intent` | table | — |  | — | intent-primed boot recall |
| `per_turn` | table | — |  | — | per-turn targeted recall (chat lane synchronous; voice lane prefetch-behind; ships OFF) |
| `serve` | table | — |  | — | served-lane session glue |
| `hindsight` | table | — |  | — | Hindsight backend settings |
| `intent.enabled` | bool | `false` |  | — (lands at the next restart of the companion and Hearth) | intent-primed boot recall: a stated next-topic survives the gap |
| `intent.expiry_days` | int | `14` | 0– | — (lands at the next restart of the companion and Hearth) | skip + clear a slot older than this (0 = no expiry) |
| `intent.llm_provider` | str | `` |  | — (lands at the next restart of the companion and Hearth) | extraction transport; falls back to [memory.hindsight]'s |
| `intent.llm_model` | str | `` |  | — (lands at the next restart of the companion and Hearth) | extraction model; falls back to [memory.hindsight]'s |
| `intent.llm_url` | str | `` |  | — (lands at the next restart of the companion and Hearth) | extraction endpoint override |
| `intent.companions` | map(str → bool) | — |  | — (lands at the next restart of the companion and Hearth) | per-companion enable override |
| `per_turn.enabled` | bool | `false` |  | — (lands at the next restart of the companion and Hearth) | per-turn targeted recall (chat lane): re-query the bank with the user's own words each request |
| `per_turn.limit` | int | `3` | 0– | — (lands at the next restart of the companion and Hearth) | targeted extras appended under their own labeled line (deduped against the open block) |
| `per_turn.min_cue_chars` | int | `12` | 0– | — (lands at the next restart of the companion and Hearth) | skip cues shorter than this (bare greetings and closes) |
| `per_turn.voice` | bool | `false` |  | — (lands at the next restart of the companion and Hearth) | ALSO run the voice lane (prefetch-behind: recall after turn N, injected before turn N+1); needs enabled=true; ships OFF |
| `serve.enabled` | bool | `false` |  | — (lands at the next restart of Hearth) | served-lane sessions (idle-gap boundaries, records, recall) |
| `serve.idle_close_voice` | int | `5` | 1– | — (lands at the next restart of Hearth) | voice-lane idle close, MINUTES (grace + margin over the voice server's reaper) |
| `serve.idle_close_chat` | int | `480` | 1– | — (lands at the next restart of Hearth) | chat-lane idle FALLBACK close, MINUTES (behind deliberate closure) |
| `serve.checkpoint` | bool | `true` |  | — (lands at the next restart of Hearth) | checkpoint open sessions each exchange (crash leaves a recoverable orphan) |
| `hindsight.mode` | str | `sidecar` |  | — (lands at the next restart of the companion and Hearth) | "sidecar" (own venv; protobuf wall) or "embedded" (non-pipecat hosts only) |
| `hindsight.python` | str | — |  | — (lands at the next restart of the companion and Hearth) | sidecar venv python — REQUIRED for sidecar mode |
| `hindsight.runner` | str | — |  | — (lands at the next restart of the companion and Hearth) | sidecar runner override (default: bundled sidecar_runner.py) |
| `hindsight.llm_provider` | str | `ollama` |  | — (lands at the next restart of the companion and Hearth) | extraction-model provider |
| `hindsight.llm_model` | str | — |  | — (lands at the next restart of the companion and Hearth) | REQUIRED when any companion selects hindsight: local extraction model |
| `hindsight.llm_api_key` | str | `` |  | — (lands at the next restart of the companion and Hearth) | provider key if the local server wants one |
| `hindsight.db_url` | str | `pg0` |  | — (lands at the next restart of the companion and Hearth) | backend store — pg0 = bundled embedded PostgreSQL |
| `hindsight.retain_max_chars` | int | `6000` | 0– | — (lands at the next restart of the companion and Hearth) | transcript tail handed to extraction at stop |
| `hindsight.recent_boost` | int | `3` | 0– | — (lands at the next restart of the companion and Hearth) | the last-session slot: newest valid facts appended past semantic rank (0 = off) |
| `hindsight.log_level` | str | `warning` |  | — (lands at the next restart of the companion and Hearth) | sidecar log level |
| `hindsight.log_file` | str | — |  | — (lands at the next restart of the companion and Hearth) | sidecar child's own stdout+stderr (default: <data folder>/logs/…) |
| `hindsight.start_timeout_s` | float | — | 0.0– | — (lands at the next restart of the companion and Hearth) | sidecar start timeout override, seconds |
| `hindsight.env` | map(str → str) | — |  | — (lands at the next restart of the companion and Hearth) | extra environment for the server (setdefault; shell wins) |

## `config/openclaw.toml` — The OpenClaw on/off switch

*place scope · operator-owned · on/off switch · restart: the companion*

One switch drives tool registration AND the {{openclaw_tools}} prompt slot, so capability and prompt can never disagree.

All keys below live under the `[openclaw]` table.

| key | type | default | range | live path | what it sets |
|---|---|---|---|---|---|
| `enabled` | bool | `false` |  | — | master switch: off ⇒ no tools registered, prompt byte-identical |
| `gateway_url` | str | `http://127.0.0.1:18789` |  | — | OpenClaw gateway endpoint |
| `agent` | str | `hands` |  | — | gateway agent name dispatched to |
| `token_source` | str | `` |  | — | PATH to the gateway token file — never the token itself |
| `quick_wait_s` | float | `8.0` | 0.0– | — | synchronous wait before handing off to background, seconds |
| `timeout_s` | float | `600.0` | 0.0– | — | dispatch hard timeout, seconds |
| `max_in_flight` | int | `2` | 1– | — | concurrent dispatch cap |
| `prompt_block` | str | `` |  | — | {{openclaw_tools}} capability paragraph injected while enabled |

## Prose layers (deliberately not schema'd)

| file | what it is |
|---|---|
| `characters/<c>/persona.md` | the CHARACTER layer: `## IDENTITY` + `## SOUL` fill the `{{persona}}` slot |
| `config/models/<m>/system-prompt-template.md` | the MODEL layer: envelope, spoken/no-markdown hard rules, `{{persona}}` slot |

## Environment variables the engine reads

| var | default | consumer | meaning |
|---|---|---|---|
| `HEARTH_ROOT` | `the checkout (found from the package)` | config_loader | engine-tree anchor |
| `HEARTH_DATA` | `HEARTH_ROOT` | config_loader | data folder — everything the operator owns |
| `WEB_HOST` | `127.0.0.1` | control panel | panel bind address (0.0.0.0 = LAN) |
| `WEB_PORT` | `65000` | control panel | panel port |
| `LM_BASE_URL` | `http://127.0.0.1:8080/v1` | pipeline (companion) | model server endpoint |
| `LM_API_TOKEN` | `none` | pipeline (companion) | model server access key, only if the server wants one |
| `LM_PROVIDER` | `llama-server` | pipeline + panel | which engine probe the panel uses (llama-server | lmstudio) |
| `T4_METRICS` | `0` | pipeline (companion) | 1 = per-turn latency marks in the log |
| `HEARTH_DEV_RELOAD` | `0` | control panel + Hearth | 1 = re-read page files per request (dev; default reads once at import) |
| `SERVE_TOKEN` | `(unset)` | Hearth (serve) | access key override — wins over token_source |
