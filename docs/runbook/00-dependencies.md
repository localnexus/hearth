# 0. What the loop depends on

*What this covers: everything that must already be running/granted for `bot.py` to talk — and what is explicitly NOT needed.* · Runbook index: [`README.md`](README.md)

```
                        ┌─────────────────────────────────────────────┐
   bot.py (python) ─────┤ REQUIRED to talk:                            │
   under iTerm.app      │   • LM Studio  :1234   (loaded model + token)
        │               │   • mic permission → iTerm.app (macOS TCC)   │
        │               │   • the uv venv (.venv, transformers==5.5.0) │
        ▼               │   TTS + STT run IN-PROCESS (no external app) │
   mic → VAD → STT → LLM → TTS → speaker      └────────────────────────┘
```

| Dependency | Needed? | Note |
|---|---|---|
| **LM Studio** `:1234` | **Yes** | must have the model the bot targets loaded — resolved from config (`config/active.toml` → `config/models/<model>/model.toml` → `.id`); a hybrid-thinking default needs thinking forced OFF via `reasoning_effort:"none"` (`model.toml`) **and**, for some uncensored re-quants, the persistent LM Studio Prompt-Template edit (`model.toml.needs_template_edit`) + a valid API token |
| **A default mic + speaker** | **Yes** | transport grabs the macOS **default** in/out at startup (no device pinned). Esp. after a Bluetooth switch — see §1 check 3 |
| **In-process TTS** (Chatterbox-Turbo) | auto | loads inside `bot.py` at startup from the `.venv`; **no app, no port**. Weights cached under `~/.cache/huggingface` |
| **In-process STT** (MLX-Whisper) | auto | same — loads inside `bot.py` at startup |
| **mic permission** | **Yes** | granted to **iTerm.app** (or whichever terminal launches python), not python itself (macOS TCC) |
| **`.venv`** | **Yes** | already built; `uv sync` if rebuilding. **`transformers` must be pinned `==5.5.0`** |
| A separate TTS service/port | **No** | not used — TTS is in-process. |
| Ollama `:11434` | **No** | not used (LLM comes from LM Studio). Ignore. |

> LM Studio is a shared, always-on desktop app — "online/offline" below means *the conversation loop* (`bot.py`). You normally leave LM Studio running for other work. §4 covers reclaiming its memory.

## Version pins — the LM Studio stack

The serving stack is version-sensitive; these pins are deliberate, not neglect. Run a
pre/post-update test before advancing any of them — the live voice loop rides this server.

| Component | Pinned at | Kind | Why / lift condition |
|---|---|---|---|
| **mlx-llm runtime pack** | **1.10.0** | **HARD** | 1.11.0 carries a context-auto-fit override — ignores explicit `-c`/API context on MLX qwen3_5-family models and inflates it to the RAM-fitted max (silent KV bloat, eviction/OOM risk). Lift only when the upstream bug is fixed. |
| llama.cpp runtime pack | 2.24.0 | soft — cleared → 2.28.1 | No Metal-relevant regressions found in 2.26→2.28.1; run the test checklist before advancing. |
| LM Studio app | 0.4.19+2 | soft — optional → 0.4.21 | 0.4.21 is ergonomics (load errors, mmap/mlock/direct-IO knobs — the knobs need llama.cpp ≥ 2.28.1). Scratch-test first: the live voice loop rides this server. |
| `transformers` (`.venv`) | 5.5.0 | HARD | Pre-existing pin (dependency table above). |
| MTP speculative decode, agent/coding work | OFF | policy | Upstream inter-request draft state → nondeterminism, unfixed at any released version. Not a speed issue — GGUF embedded-head measured +24–26%. |

> ⚠ **`lms runtime update` advances ALL packs**, including past the MLX hard pin — never run
> it blindly. Recovery: `lms runtime select mlx-llm-mac-arm64-apple-metal-advsimd@1.10.0`
> (superseded packs stay installed and selectable).
