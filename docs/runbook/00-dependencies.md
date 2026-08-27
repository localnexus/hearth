# 0. What the loop depends on

*What this covers: everything that must already be running/granted for `bot.py` to talk — and what is explicitly NOT needed.* · Runbook index: [`README.md`](README.md)

```
                        ┌─────────────────────────────────────────────┐
   bot.py (python) ─────┤ REQUIRED to talk:                            │
   under the terminal   │   • an OpenAI-compatible LLM server (llama-server :8080)
        │               │   • mic permission → iTerm.app (macOS TCC)   │
        │               │   • the uv venv (.venv, transformers==5.5.0) │
        ▼               │   TTS + STT run IN-PROCESS (no external app) │
   mic → VAD → STT → LLM → TTS → speaker      └────────────────────────┘
```

| Dependency | Needed? | Note |
|---|---|---|
| **LLM server** (`llama-server` on `:8080` — the default; LM Studio on `:1234` + its token as the alternative) | **Yes** | must serve the model the bot targets — resolved from config (`config/active.toml` → `config/models/<model>/model.toml` → `.id`). `llama-server` serves its one loaded model regardless of the id; LM Studio needs it verbatim. A hybrid-thinking model needs thinking forced OFF via `reasoning_effort:"none"` (`model.toml`, sent every request); if the template ignores it, a `llama-server` start-up switch — or, under LM Studio, its persistent Prompt-Template edit (`model.toml.needs_template_edit`). A key is needed only if the server asks for one (`llama-server --api-key`; LM Studio always). |
| **A default mic + speaker** | **Yes** | transport grabs the macOS **default** in/out at startup (no device pinned). Esp. after a Bluetooth switch — see §1 check 3 |
| **In-process TTS** (Chatterbox-Turbo) | auto | loads inside `bot.py` at startup from the `.venv`; **no app, no port**. Weights cached under `~/.cache/huggingface` |
| **In-process STT** (MLX-Whisper) | auto | same — loads inside `bot.py` at startup |
| **mic permission** | **Yes** | granted to **iTerm.app** (or whichever terminal launches python), not python itself (macOS TCC) |
| **`.venv`** | **Yes** | `uv venv -p 3.12 && uv pip install -e ".[mac]"` (README). **`transformers` must be pinned `==5.5.0`** |
| A separate TTS service/port | **No** | not used — TTS is in-process. |
| Ollama `:11434` | **No** | not used (the LLM comes from the server above). Ignore. |

> The LLM server is a separate, long-running process — "online/offline" below means *the conversation loop* (`bot.py`). You normally leave the server running. §4 covers reclaiming its memory.

## Version pins — if you use LM Studio instead of `llama-server`

**This whole section is LM-Studio-specific** and does not apply to the default `llama-server` path
(there you pin a llama.cpp release yourself, and the app/runtime-pack layer simply doesn't exist).
LM Studio's serving stack is version-sensitive; these pins are deliberate, not neglect. Run a
pre/post-update test before advancing any of them — the live voice loop rides this server.

| Component | Pinned at | Kind | Why / lift condition |
|---|---|---|---|
| **mlx-llm runtime pack** (LM Studio) | **1.10.0** | **HARD** | 1.11.0 carries a context-auto-fit override — ignores explicit `-c`/API context on MLX qwen3_5-family models and inflates it to the RAM-fitted max (silent KV bloat, eviction/OOM risk). Lift only when the upstream bug is fixed. |
| llama.cpp runtime pack (LM Studio's) | 2.24.0 | soft — cleared → 2.28.1 | No Metal-relevant regressions found in 2.26→2.28.1; run the test checklist before advancing. |
| LM Studio app | 0.4.19+2 | soft — optional → 0.4.21 | 0.4.21 is ergonomics (load errors, mmap/mlock/direct-IO knobs — the knobs need llama.cpp ≥ 2.28.1). Scratch-test first: the live voice loop rides this server. |
| `transformers` (`.venv`) | 5.5.0 | HARD | Pre-existing pin (dependency table above) — this one applies on **every** path, LM Studio or not. |
| MTP speculative decode, agent/coding work | OFF | policy | Upstream inter-request draft state → nondeterminism, unfixed at any released version. Not a speed issue — GGUF embedded-head measured +24–26%. |

> ⚠ **LM Studio only:** `lms runtime update` advances ALL packs, including past the MLX hard pin —
> never run it blindly. Recovery: `lms runtime select mlx-llm-mac-arm64-apple-metal-advsimd@1.10.0`
> (superseded packs stay installed and selectable). `lms` is LM Studio's CLI; there is no
> equivalent on the `llama-server` path.
