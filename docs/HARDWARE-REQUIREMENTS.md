# HARDWARE REQUIREMENTS

What it takes to run the fully-local, real-time voice loop (STT + LLM + TTS + VAD, all on one machine). The stack is built around **local sovereignty**: your audio, your text, and your model weights never leave hardware you control. Two tiers deliver that, at different maturity levels.

---

## Tier summary

| Tier | Hardware | Maturity | Sovereignty |
|---|---|---|---|
| **Gold — sovereign local** | Apple Silicon Mac (unified memory) | First-class, measured, the reference path | Full: runs offline, nothing leaves the machine |
| **Silver — rented raw GPU** | An NVIDIA/CUDA box you have **root** on | Early / placeholder — the CUDA path is not yet proven | Conditional: only as sovereign as your control over the box |
| *(not a tier)* Provider APIs | Someone else's inference endpoint | — | None — breaks the local premise |

---

## Gold tier — Apple Silicon (the measured reality)

This is where the stack was built and where every latency and memory number below was measured. The three compute-heavy layers all use Apple's **MLX** framework and run on **Metal**:

- **STT** — MLX-Whisper (`whisper-large-v3-turbo`), Metal-only.
- **TTS** — Chatterbox-Turbo via `mlx-audio`, Metal-only; JIT-compiles its Metal kernels on first synth (~15.8 s, one-time per machine, cached on disk afterward).
- **LLM** — served locally by **LM Studio** or **llama-server**; the MLX inference backend is Apple-Silicon-only.

Silero VAD runs on `onnxruntime`; pipecat and the Python glue are platform-agnostic. But because the three heavy layers are Metal/MLX-bound, an Intel Mac cannot run this path.

**OS / runtime:**

- **macOS** on Apple Silicon (M-series). Recent macOS; verify against current LM Studio and `mlx-audio` release notes before running on an older OS.
- **Python ≥ 3.11** (3.12 recommended, managed by `uv`). The macOS system Python is too old.

### Memory — the sizing that matters

Everything shares one unified-memory pool: resident model weights plus the LLM's active KV context. Approximate resident weight footprint with the default stack:

| Component | Footprint |
|---|---|
| LLM — a ~35B-parameter, 3B-active MoE at Q8_0 | ~37 GB |
| TTS — Chatterbox-Turbo fp16 | ~3 GB |
| STT — Whisper-large-v3-turbo | ~1.6 GB |
| Silero VAD | < 0.1 GB |
| **Weight floor** | **~42 GB** |

Add the LLM's KV cache (1–5 GB in a typical session; the loaded context window is large — 262,144 tokens — but real sessions occupy a small fraction of it) plus macOS, the LM Studio process, and the Python runtime (~8–12 GB). That gives a **comfortable operating floor of roughly 55–60 GB in use**.

| Unified memory | Verdict |
|---|---|
| 16–24 GB | Not viable with the default stack — the OS pages weights to flash, breaking the real-time loop. |
| 32–48 GB | Only with a smaller LLM quant and lighter model (see below); expect memory pressure. |
| **64–96 GB** | **Practical minimum** for the default Q8_0 stack. |
| **128–192 GB** | **Recommended** — no memory-pressure risk, full context window, room to keep a second model loaded for comparison. |
| 256 GB+ | Comfortable headroom; RAM stops being a constraint (fp16 TTS is chosen precisely because memory is not scarce here). |

### Latency (measured on high-end Apple Silicon)

- Time-to-first-audio (TTS): **~0.42 s**
- Time-to-first-token (LLM): **~0.21 s**
- TTS real-time factor: **~0.24** (roughly 3x faster than playback)
- LLM throughput: **~92 tokens/s**

RTF must stay **< 1.0** for gapless streaming — the ~3x headroom above is what absorbs a slower chip. Earlier / smaller M-series chips will run the stack but slower, especially on TTS (MLX Metal performance scales with GPU generation); they have not been formally characterized.

### Disk

First-run weight download is **~42 GB** (LLM ~37 GB, TTS ~3 GB, STT ~1.6 GB, VAD negligible). Add ~15–20 GB for the Python virtualenv and the MLX kernel cache. Keep **~60 GB free** before installing.

### Lowering the floor

- **Smaller LLM quant** — Q4_K_M roughly halves the LLM weights (~18–19 GB) at some quality cost, dropping the operating floor toward ~35–40 GB.
- **Lighter LLM** — a smaller MoE (e.g. a ~24B, 2B-active model) has a lower footprint and swaps in via `model =` in `config/active.toml`.
- **Lower-quant TTS** — `chatterbox-turbo-{8bit,6bit,4bit}` saves ~1–2 GB at some voice-quality cost.

### Audio input

A working input device is required — the pipeline grabs the macOS default mic and speaker at launch, with no fallback.

- **A2DP** Bluetooth earbuds are output-only (no mic) and produce `Errno -9996` when set as the default input — the pipeline won't start.
- **HFP** Bluetooth provides a mic but forces both directions to low-fi mono ~16 kHz. Whisper accepts 16 kHz, so it works, but quality drops.
- **Safest:** built-in mic, a wired headset, or a USB audio interface. Connect Bluetooth *before* launch; switching devices mid-session needs a restart (the stream does not follow default-device changes live).

---

## Silver tier — rented raw GPU with root

If you don't have a large Apple Silicon machine, the sovereign-adjacent option is an **NVIDIA/CUDA box you rent but fully control** — bare-metal or a VM with **root**, not a managed inference service. As long as you own both ends of the box, the same trust posture applies: weights and conversation stay on hardware you administer.

Honest status: **the CUDA path is early and less-tested.** The `hearth[cuda]` install extra is a **placeholder today** — the Apple/MLX layers (MLX-Whisper, MLX Chatterbox) have no drop-in CUDA equivalent wired up yet, so expect to substitute components (a CUDA-capable TTS/STT and a CUDA LLM server such as `llama-server` or vLLM) and to do your own integration and verification. Treat this tier as "supported in principle, bring your own elbow grease," not turnkey.

Rough sizing if you go this route: a single modern data-center or high-end consumer GPU with **~24–48 GB of VRAM** covers the LLM at a mid quant plus a GPU TTS/STT, mirroring the gold-tier memory story. Keep everything resident on one box to preserve the local guarantee.

---

## Provider APIs are not a tier

Calling a hosted LLM, TTS, or STT API would be the easy path, and it is deliberately excluded. Sending your audio or text to a third-party endpoint breaks the entire premise of this project — local sovereignty, offline capability, and the guarantee that sensitive content never leaves hardware you control. A provider API is not a lower tier of the same thing; it is a different thing. If you need the local guarantee, use gold or silver. If you don't, this stack is not what you want.

---

*Numbers here are approximate and drawn from measurements on Apple Silicon plus quantization math. Verify against your actual download sizes and a live memory reading on your target machine before committing to a deployment.*
