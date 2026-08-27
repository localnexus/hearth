# CONFIG MANUAL — tuning the voice loop (in-process TTS)

**How to change the common things — what to edit, where, how to verify.** The three most common — **model, system prompt, voice** — are **externalized into config files**; the rest of the knobs still live in `bot.py`/`stt_service.py`/`mlx_tts_service.py`. Pairs with the [runbook](../runbook/README.md) (start/stop).

### Orientation — where config lives

Model / prompt / voice = **data files** now (edit + restart; `config_loader.load_active()` resolves them at startup, fail-fast on a missing/malformed file). The remaining knobs are still code.

| Location | Holds |
|---|---|
| **`config/active.toml`** | **the live selection pointer**: `character`, `model`, `voice` (edit + restart; nothing hot-swapped) |
| **`config/models/<model>/model.toml`** | **the model's load facts**: `id`, `temperature`, `reasoning_effort`, `needs_template_edit`, `no_kv_reuse`, `reliable_context` (the panel's gauge line — see [LLM](llm.md)) (`context_length` is deliberately NOT here — the live server value wins) |
| **`config/models/<model>/system-prompt-template.md`** | **the MODEL layer of the prompt**: envelope + output-shaping hard rules + `{{persona}}` slot |
| **`characters/<character>/persona.md`** | **the CHARACTER layer**: `## IDENTITY` + `## SOUL` → fills `{{persona}}` |
| **`characters/<character>/voices/<voice>/`** | **a self-contained voice bundle**: `voice.toml` (`tag` + `ref_wav`, + `license`/`source`) and the vendored `sample.wav` reference clip |
| `bot.py` **Configuration block** (`~L120–L166`) | LLM endpoint/token/provider — module-level constants `LM_BASE_URL` / `LM_API_TOKEN` / `LM_PROVIDER` (model id now `_CFG.model_id` from config) |
| `bot.py` **`build_pipeline()`** (`~L172–L377`) | VAD knobs, audio params, pipeline order (LLM persona/temperature/reasoning_effort/model/voice `ref_wav` now sourced from `_CFG`) |
| `bot.py` **`main()`** (`~L380–L486`) | metrics, idle-timeout |
| **`stt_service.py`** — the MLX-Whisper STT stage | STT model constant `MLX_WHISPER_MODEL`, `run_stt()` language + hallucination guard, the `_t4_mark` T4 marker (peer to `mlx_tts_service.py`) |
| **`mlx_tts_service.py`** constants (`~L108–L143`) | **TTS: model repo, sample rate, streaming interval** (the voice `ref_wav` now comes from the active bundle) |
| **env** `LM_BASE_URL` · `LM_API_TOKEN` · `LM_PROVIDER` | the LLM server: base URL (default `http://127.0.0.1:8080/v1`, `llama-server`), a bearer key **only if the server wants one** (`llama-server --api-key`), and which engine probe the panel uses (`llama-server` · `lmstudio`). Not in any config file. |
| **env** `transformers==5.5.0` (the `.venv` pin) | **mandatory** — see [voice & TTS](voice-tts.md) and the runbook dependencies step |

> **For code knobs, line numbers drift — the CONSTANT/PARAM NAME is the stable anchor.** Grep the name. For config, the FILE + KEY is the anchor.
> **No hot reload.** Every change needs a stop + relaunch (see the [runbook](../runbook/README.md)). Changing the **voice** especially requires a restart — conditionals are precomputed once at startup.
> **Verify after any change:** relaunch, one live turn, watch the log.

---

## Quick map — request → where

| I want to… | Edit (anchor) | File · location | Also needs |
|---|---|---|---|
| Change the **voice** | `voice =` | `config/active.toml` | the named bundle `characters/<char>/voices/<voice>/` (`sample.wav` + `voice.toml`); **restart** |
| **Add a voice / character** | new bundle dir + `active.toml` | `characters/…` + `config/active.toml` | see [voice & TTS](voice-tts.md) → "Adding a voice, or a whole character"; **restart** |
| Change the **TTS model / quant** | `MODEL_REPO` | tts · L108 | a pre-converted `mlx-community` repo (has `config.json`) |
| Tune **TTS chunk granularity** | `STREAMING_INTERVAL` | tts · L143 | — |
| Steady **TTS intonation** (uptalk/fry) | `temperature` (see [voice & TTS](voice-tts.md)) | tts · `generate()` L405 | **restart**; quality trade-off |
| **Capture live TTS** (debug) | `--dump-tts` flag | bot CLI → [debugging/tts-audio-cases.md](../debugging/tts-audio-cases.md) (Case study 4) | diagnostic, off by default |
| Change the **LLM model** | `model =` | `config/active.toml` → `config/models/<model>/model.toml` `id` | loaded by your LLM server (`llama-server` serves its one loaded model whatever the id says; LM Studio needs it verbatim); **no emitted CoT** — instruct, or hybrid w/ thinking off ([LLM](llm.md)); **restart** |
| Force **hybrid thinking OFF** | `reasoning_effort = "none"` | `config/models/<model>/model.toml` | sent on every request; needed for hybrid-thinking models. If a template ignores it: on `llama-server` use a start-up switch (`--reasoning off`, `--reasoning-budget 0`, `--chat-template-kwargs`); on LM Studio, its Prompt-Template edit (`needs_template_edit`) ([LLM](llm.md)) |
| Change the **persona / behavior** | `## IDENTITY` / `## SOUL` (character) · hard rules (template) | `characters/<char>/persona.md` · `config/models/<model>/system-prompt-template.md` | **restart**; non-byte-equiv edit warns on resume |
| Make replies more/less **creative** | `temperature` | `config/models/<model>/model.toml` | **restart** |
| Set the **panel's context-budget line** | `reliable_context` | `config/models/<model>/model.toml` | measured usable ceiling the `Tokens` gauge counts against (`128000` today, PROVISIONAL); absent → panel falls back to the window the server reports. See [LLM](llm.md) |
| Give the model **voiced-breath cues** | 9-tag block + carve-out | `config/models/<model>/system-prompt-template.md` | model-layer prompt capability (Chatterbox-Turbo consumes the tags); **restart**; non-byte-equiv → warns on resume. See [LLM](llm.md) |
| Tune **barge-in / turn-taking** feel | `VADParams(...)` | bot · L211 | — |
| Change the **STT model** | `MLX_WHISPER_MODEL` | stt_service · L32 | auto-downloads on first use |
| Force / set **STT language** | `run_stt` transcribe call | stt_service · L146 | — |
| Adjust **hallucination filter** | guard block in `run_stt` | stt_service · L162 | — |
| Point at a **different LLM backend** | `LM_BASE_URL` / `LM_API_TOKEN` / `LM_PROVIDER` | bot · `~L126` | LM Studio = `http://127.0.0.1:1234/v1` + its token + `LM_PROVIDER=lmstudio` |
| Reach the **control panel from a phone** | `WEB_HOST` | control.py · L54 | `0.0.0.0` = LAN-reachable at `<mac-ip>:65000` (control only — mute/PTT/text; **audio stays on the Mac**); `127.0.0.1` = local only |
| Change the **control-panel port** | `WEB_PORT` | control.py · L55 | default `65000` (high dynamic range, low collision risk); e.g. `WEB_PORT=51000 ./start.sh` |
| Pick a specific **mic/speaker** | `LocalAudioTransportParams` | bot · L194 | device index |
| **Session resume / hold** | `--resume` · `--new` · `--hold` · `--discard-held` | `start.sh` · `stop.sh` → [session continuity](session-continuity.md) | — |
| **Metrics / T4 / idle-timeout** | see [misc](misc.md) | — | — |

---

## Golden rules

1. **Restart to apply** — no hot reload; relaunch after every edit. Voice changes especially (conditionals precompute once at startup).
2. **No emitted chain-of-thought** — pure instruct, or a hybrid with thinking forced off (Hearth sends `reasoning_effort:"none"` from `model.toml` on every request; a template that ignores it needs a server-side switch or a replaced chat template). A model that streams `reasoning_content`/`<think>` with empty `content` silently stalls the loop.
3. **Keep `transformers==5.5.0`** — the single most fragile dep; an unpinned upgrade breaks TTS import.
4. **Keep MLX on the executor thread** — never move a TTS-engine MLX call off the service's single-worker executor (thread-local Metal streams).
5. **Keep it 16 kHz in** — the mic-side rate is fixed by Whisper.
6. **Verify live after changing** — one real turn, watch the log; import-clean can still be runtime-wrong.

---

## Topic files

| File | What it covers |
|---|---|
| [voice-tts.md](voice-tts.md) | §A — Voice & TTS: the in-process Chatterbox-Turbo engine, changing the voice/model/quant, streaming interval, sample rate, prosody `temperature`, `--dump-tts`. |
| [llm.md](llm.md) | §B — The LLM: model selection and id semantics, the no-chain-of-thought rules, hybrid thinking off, persona, creativity, backend/token/provider. |
| [listening-vad-barge-in.md](listening-vad-barge-in.md) | §C — Listening behavior: the four `VADParams` knobs, barge-in feel, Smart Turn v3.x endpointing. |
| [stt.md](stt.md) | §D — STT: Whisper model, language pinning, hallucination guard. |
| [audio-devices.md](audio-devices.md) | §E — Audio devices & sample rates: mic/speaker pinning, the fixed 16 kHz input. |
| [misc.md](misc.md) | §F — Misc: T4 latency logs, idle-timeout keep-alive, HF Hub airgap, the transformers pin. |
| [session-continuity.md](session-continuity.md) | §G — Session continuity: the `sessions/` dir and the `--resume`/`--new`/`--hold`/`--discard-held` CLI flags. |
