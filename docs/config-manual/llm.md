# The model (model, persona, tone, backend)

Hearth talks to **any OpenAI-compatible server**. The documented default is **`llama-server`** (from llama.cpp) on **`http://127.0.0.1:8080/v1`**, keyless unless you started it with `--api-key`. **LM Studio is supported as an alternative** — see the note at the end of each section where the two differ.

**Change the model** → set `model =` in **`config/active.toml`** (names a dir under `config/models/<model>/`; that dir's `model.toml` `id` is the model id Hearth sends). **Edit + restart** — nothing is hot-swapped. *(`config_loader.load_active()` resolves it at startup and fails fast, naming the file, if it's missing/malformed.)*

**What the `id` means depends on the server:**

| Server | `model.toml` `id` | Behaviour |
|---|---|---|
| **`llama-server`** (default) | a label | One model per process — the server answers with **its loaded model regardless** of the `model` field. Preflight *warns* on a mismatch instead of failing. By default `llama-server` reports the **GGUF file path** as the id; `--alias <name>` sets a friendlier one. |
| LM Studio (alternative) | must match **verbatim** | It can serve several models at once, so a wrong id is a hard `model_not_found`. |

List what your server advertises (access key header only if the server wants a key):
```bash
BASE_URL=${LM_BASE_URL:-http://127.0.0.1:8080/v1}
curl -s "$BASE_URL/models" ${LM_API_TOKEN:+-H "Authorization: Bearer $LM_API_TOKEN"} \
  | python3 -c 'import sys,json;[print(m["id"]) for m in json.load(sys.stdin)["data"]]'
```

**Default:** a ~35B-parameter, 3B-active MoE instruction model (depth + speed), run as a hybrid-thinking model with thinking forced off — see the rules below. Some large hybrid-attention models keep no reusable per-token KV, so cross-turn caching can't work and TTFB climbs sharply at depth; a model that supports prefill caching is preferred for interactive use (`model.toml`'s `no_kv_reuse` records that fact per model). On `llama-server`, prompt caching is on by default (`--cache-prompt`) and `--cache-reuse N` enables KV-shifted reuse of matching chunks — neither helps a model with no reusable per-token KV. A stock (non-re-quant) build of the same model makes a good fallback baseline.

⚠️ **Two hard rules:** (1) the model must actually be **loaded** by the server; (2) it must emit **no chain-of-thought**. A model that streams `reasoning_content`/`<think>` and leaves `content` empty stalls the loop. So: a pure instruct / natively-non-thinking model, **or** a *hybrid* (Qwen3.6, GLM, …) with thinking forced off.

### Forcing hybrid thinking OFF

Hearth already sends **`reasoning_effort`** from the active `model.toml` on **every request** (`config_loader` → the model `Settings` `extra`). Set `reasoning_effort = "none"` there and that is normally the whole job. Like `temperature`, this is **live-hot**: the panel can write `[llm] reasoning_effort` into **`config/overrides.toml`** (overlaid at the next turn boundary, no restart) and snapshot it to a companion's **`characters/<character>/profile.toml`** preset — the `model.toml` value is just the at-rest default.

- **On `llama-server`** the request field is documented as: *"`reasoning_effort`: If `none`, reasoning/thinking is disabled. Otherwise, the value is made available to the jinja template."* So Hearth's per-request field is the supported lever. If a template ignores it, `llama-server` also has **server-side** switches you can start it with — `-rea, --reasoning off` (*"Use reasoning/thinking in the chat ('on', 'off', or 'auto', default: 'auto' (detect from template))"*), `--reasoning-budget 0` (*"token budget for thinking: … 0 for immediate end"*), `--chat-template-kwargs '{"enable_thinking": false}'`, and `--reasoning-format none` (which leaves any thoughts unparsed in `message.content` rather than hiding them). A stubborn template can be replaced outright with `--chat-template` / `--chat-template-file`. Check your build's own `llama-server --help` / the upstream `tools/server/README.md` for the exact set — these switches have moved before.
- A natively non-thinking model needs none of this.

Sanity-check that the server streams non-empty `content`:
```bash
BASE_URL=${LM_BASE_URL:-http://127.0.0.1:8080/v1}
curl -s -N "$BASE_URL/chat/completions" ${LM_API_TOKEN:+-H "Authorization: Bearer $LM_API_TOKEN"} \
  -H 'Content-Type: application/json' \
  -d '{"model":"<id>","messages":[{"role":"user","content":"hi"}],"max_tokens":20,"stream":true}' \
  | grep -o '"content":"[^"]*"' | head
```

> **If you use LM Studio instead.** On the LM Studio builds this was validated against, `reasoning_effort` in the request body is the **only** field that reaches the jinja `enable_thinking` var — `chat_template_kwargs` / a top-level `enable_thinking` / `reasoning:{enabled:false}` are all **silently ignored**. ⚠️ Some third-party **uncensored re-quants** ignore even `reasoning_effort`; those need LM Studio's persistent **Prompt-Template** edit (`{%- set enable_thinking = false %}` as the template's first line). That requirement is what `needs_template_edit = true` in a `model.toml` records — it is an **LM-Studio-specific** flag, and it is a manual GUI step, so it does not survive a reinstall. On `llama-server` the equivalent is a start-up flag or a replacement chat template (above), which is reproducible.

**Persona / behavior** → edit **`characters/<character>/persona.md`** (the CHARACTER layer — `## IDENTITY` + `## SOUL`) and/or the active model's **`config/models/<model>/system-prompt-template.md`** (the MODEL layer — output-shaping hard rules + model-whispering, with a `{{persona}}` slot). At startup `config_loader` composes them into `system_instruction` (template with `{{persona}}` filled by IDENTITY-body + `\n\n` + SOUL-body). Keep the "short, spoken, no markdown" hard rules — they live in the template — or replies read badly aloud. ⚠️ Any non-byte-equivalent edit changes the prompt's `sha256` and warns on resume of pre-edit sessions (by design). **Restart** to apply.

**Voiced-breath cues (model-layer capability)** → the template's hard rules instruct the model to emit a whitelist of **nine paralinguistic cue tags** — `[laugh] [chuckle] [sigh] [gasp] [groan] [sniff] [cough] [shush] [clear throat]` — which **Chatterbox-Turbo consumes as breath/sound events, never spoken aloud**. The no-stage-directions rule carries a carve-out for exactly these nine; the prompt also states the placement law (set a cue off with punctuation, keep the turn short — never bury it mid-phrase) and a restraint-first framing. This is a **prompt capability, not a code knob** — it lives in `config/models/<model>/system-prompt-template.md` (a non-byte-equivalent change, so it warns on resume of pre-edit sessions). A deterministic **TTS-side repair layer backstops it** — `paralinguistics.normalize()` in `mlx_tts_service.run_tts` rewrites a fumbled-wrapper / wrong-case / morphed cue (`*sigh*`, `[SIGHS]`) to the canonical `[tag]` and strips un-performable `[gentle smile]`/`[gentle pause]` beats before synthesis (see [voice & TTS](voice-tts.md)). **Restart** to apply.

**Creativity** → `temperature`. The **at-rest default** is in the active **`config/models/<model>/model.toml`** (~0.7 now; an edit there needs a restart). It is also a **live knob**: the control panel writes `[llm] temperature` into **`config/overrides.toml`**, which overlays the model default at the next turn boundary — no restart — and a panel **profile preset** snapshots it per companion to **`characters/<character>/profile.toml`**, so it travels with them. Add `top_p`/`max_tokens`/etc. inside `OpenAILLMService.Settings(...)` in `bot.py`.

**Context-budget gauge line** → `reliable_context` in the active **`config/models/<model>/model.toml`** (`128000` today). The control panel's `Tokens` line gauges held-tokens against this **measured usable ceiling**, not the context the server actually allocated — with ok (`<75%`) / warn (`75–100%`) / over (`≥100%`) zones and a pre-emptive "approaching reliable context line" banner. It's a **PROVISIONAL cost/prudence cap**, NOT a measured recall cliff (a needle sweep found none to ~207k). Rides `/engine` as `engine_info["reliable"]`; if omitted the panel falls back to gauging against the window the server reports. Note `context_length` is deliberately *not* a `model.toml` key — the **live server value wins**, and on `llama-server` that is whatever `-c, --ctx-size N` allocated (`0` = taken from the model).

### Where the weights live (enrollment)

Hearth keeps **roots** (directories it may look in) and **enrolled weights** (a resolved real path bound to one `config/models/<model>/model.toml`). It is **enrollment, not storage**: Hearth never downloads, moves, copies, or deletes a weights file, and un-enrolling removes the reference only.

- **`config/weights.toml`** (`[weights]`, optional; absent → defaults) declares `roots = [...]` (your own directories, `~` expanded, symlinks resolved), `product_dirs = true` (built-in **pointers** at `~/.lmstudio/models`, `~/.ollama/models`, `~/.cache/huggingface/hub` — each included only if it exists and is not already inside one of your roots), `landing` (Hearth's own folder for new weights, subdivided `llm/` `tts/` `stt/`; defaults to `<first root>/hearth`, and only `llm/` is scanned today), and `llama_server` (the door binary; defaults to the `llama-server` on `PATH`, then `/opt/homebrew/bin/llama-server`). Full commentary: [`config/weights.toml.example`](../../config/weights.toml.example).
- **Pointers are paths, nothing more.** The readers touch files and GGUF headers only — never another program's background service, command line, or private index/metadata cache. A scan returns the same result with LM Studio and Ollama quit, uninstalled, or their folders renamed. Three layouts are read: plain/LM-Studio trees (`<publisher>/<repo>/*.gguf`, shards `-00001-of-000NN.gguf` grouped, `mmproj-*.gguf` paired), Ollama (`manifests/**` JSON → `blobs/sha256-*`, accepted only when the blob's magic reads `GGUF`, which skips safetensors imports), and the Hugging Face cache (`models--<org>--<repo>/snapshots/<rev>/`, symlinks resolved).
- **`[weights]` in a `model.toml`** holds `path` (resolved real path), `mmproj`, `root`, `layout`, `display_key`, `size_bytes`, `identity` (`sha256(size ‖ first 1 MiB)[:16]`), `enrolled`, and a `[weights.header]` record of the GGUF facts. **`[server]`** in the same file holds this model's door flags — keys are llama-server **long flags without the dashes** (`c`, `parallel`, `kv-unified`, `mmproj`, `chat-template-file`, `spec-type`, `alias`, …), each checked against the door's own `--help`. Door-level facts (port, api-key-file, load-mode, threads) do **not** belong there.
- **The commands:** `python -m hearth.weights roots | scan [--root NAME] [--ctx N] [--json] | list | enroll <model> (--path P | --key KEY) [--mmproj auto|none|PATH] [--yes] | unenroll <model> [--yes] | check [<model>]`. Writes preview first and need `--yes`.
- **Fit is an estimate for the display.** Weights (Σ tensor bytes) + projector + KV at the context + a 1 GiB margin, against this machine's working set (`llama-server --list-devices`, else `sysctl hw.memsize` × 0.75/0.93) minus ~4.6 GB held by the speech stages. The KV term reads `full_attention_interval`: on a hybrid build only every Nth block caches, so 262 144 tokens costs ~5.4 GB on the incumbent, not ~21.5. The server's own `--fit` decides the load; `/props` after load is the truth.
- **Missing weights are a reported state**, never a fallback: `check` prints `[ERROR] … weights missing — <path> is not there any more` and exits 1. Nothing goes looking for another copy.

### The door's own facts, and the unit rendered from them

A `[server]` table says what one MODEL needs; **`[weights.door]`** in `config/weights.toml` says what the DOOR is, and stays true across model swaps: `label` (the launchd label, `com.hearth.llm` by default — it names the unit file too), `host`/`port`, `api_key_file` (a **PATH**, passed to `--api-key-file` and never opened, printed, or logged), `threads`, `load_mode` (`auto | none | mmap | mlock | mmap+mlock | dio` — the door's own `--load-mode`, which replaces the deprecated `--mlock` / `--mmap` / `--direct-io` trio), `log_file`, `webui = false` (renders `--no-webui`), and `args = []` for anything not modelled. Full commentary: [`config/weights.toml.example`](../../config/weights.toml.example).

`python -m hearth.weights render <model>` composes the three sources into one command line — `[weights]` → `--model` / `--mmproj`, `[server]` → this model's flags (a `true` boolean is a bare flag, `false` is omitted, and a valued flag like `flash-attn = "on"` stays valued; a one-letter key renders as a short flag), `[weights.door]` → the door's own — and writes the launchd unit to `DATA/render/<label>.plist`. The unit's shape is today's: `RunAtLoad`, `KeepAlive {SuccessfulExit = false}`, `ThrottleInterval 10`, both streams to `<log_file>`'s sibling `<name>.launchd.log`, `WorkingDirectory` = the data folder, keys in a fixed order so two renders of one config are byte-identical.

`render --diff [--against <plist>]` (default `~/Library/LaunchAgents/<label>.plist`) compares the rendered argv with a unit already on the machine as **sets of (flag, value)** — order-free, short and long spellings folded together, and two paths that resolve to the same file (a product's symlink vs the enrolled real path) counted equal. Each difference is classified: **`placement`** (`n-gpu-layers`, `main-gpu`, `tensor-split`, `split-mode` — the door's `--fit` manages these, so a hand-written unit that pins them is not in conflict), **`deprecated-form`** (`--mlock` / `--no-direct-io` against `--load-mode`), or **`real`**. Exit 1 on any `real`; that check is what an apply is held behind.

`python -m hearth.weights apply <model>` previews by default and writes only with `--yes`: it copies the rendered unit to `~/Library/LaunchAgents/<label>.plist` (`HEARTH_LAUNCH_AGENTS` points that elsewhere), **archives** whatever was there as `<label>.plist.prev-<date>` rather than deleting it, and prints the two lines for you to run —

```bash
launchctl bootout gui/$UID/com.hearth.llm
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.hearth.llm.plist
```

— which it never runs itself. It refuses while a companion is running (process truth from the running program's `/admin/state` when it answers; otherwise the door's own `/health` and a plain warning). Rollback is the archived file plus the same two lines. The full order is **enroll → render `--diff` → apply → load**.

Felt version, for the person rather than the spec: [`users-manual/weights-and-where-they-live.md`](../../src/hearth/config/users-manual/weights-and-where-they-live.md).

**Different backend / token** → three env vars, read in `bot.py`'s configuration block:

| Env | Default | Meaning |
|---|---|---|
| `LM_BASE_URL` | `http://127.0.0.1:8080/v1` | any OpenAI-compatible server |
| `LM_API_TOKEN` | *(unset)* | access key key, **only if the server requires one** (`llama-server --api-key`) |
| `LM_PROVIDER` | `llama-server` | which engine probe the control panel uses; the other value is `lmstudio` |

LM Studio is one env triple away:
```bash
LM_BASE_URL=http://127.0.0.1:1234/v1 LM_API_TOKEN=<its token> LM_PROVIDER=lmstudio ./start.sh
```
Keep it local ("sensitive text stays local"). *(A headless `mlx_lm.server` is another drop-in alternative — an operational choice, not a speed one; the model isn't the bottleneck.)*

**A live session owns its model's residency (LM Studio only).** At start-up the companion checks whether the configured model is loaded and, if not, loads it itself through the `lms` command-line tool (found on `PATH`, at `~/.lmstudio/bin/lms`, or wherever `LMS_BIN` points) — the wait lands at start-up, where a wait is expected, not on the first sentence spoken. This matters more than it sounds: an LM Studio build was observed unloading a just-in-time-loaded model the second a reply finished, which turned every turn into a full load (~15 s). An explicit load carries no such timer. The load's output goes to `logs/model-load.log` in the data folder; if the tool is missing or the server does not answer, the companion says so in one line and proceeds as before. Nothing is unloaded at session end — warm stays the default; the launch page's unload actuator is the explicit cold stop, and it is held behind a confirm while a companion is running (`guard = "companion"`). Under `llama-server` the model is the process, so none of this applies.
