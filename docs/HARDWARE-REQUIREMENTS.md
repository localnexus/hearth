# HARDWARE REQUIREMENTS

What it takes to run the fully-local, real-time voice loop (STT + model + TTS + VAD, all on one machine). The stack is built around **local sovereignty**: your audio, your text, and your model weights never leave hardware you control. Two tiers deliver that, at different maturity levels.

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
- **model** — served locally by **`llama-server`** (the default; llama.cpp, GGUF weights, Metal-accelerated on Apple Silicon). **LM Studio** works as an alternative front-end for the same job; its MLX inference backend is Apple-Silicon-only.

Silero VAD runs on `onnxruntime`; pipecat and the Python glue are platform-agnostic. But because the three heavy layers are Metal/MLX-bound, an Intel Mac cannot run this path.

**OS / runtime:**

- **macOS** on Apple Silicon (M-series). Recent macOS; verify against your model server's and `mlx-audio`'s release notes before running on an older OS.
- **Python ≥ 3.11** (3.12 recommended, managed by `uv`). The macOS system Python is too old.

### Memory — the sizing that matters

Look up your Mac's memory (Apple menu → About This Mac → *Memory*):

| Your Mac's memory | Can it run Hearth? |
|---|---|
| 16 or 24 GB | **No.** Even the smallest download of the model is bigger than what your Mac can hold in memory alongside everything else. A different, smaller model might work; this page does not cover one yet. |
| 32 GB | **No, not with this model.** The smallest download comes about one gigabyte short of fitting, so Hearth will refuse to load it. |
| 48 GB | **Yes, with a smaller download of the model**, and with the conversation length capped. It will feel tight. |
| 64 GB | **Yes.** Every download of the model fits. The full-quality one runs with a shorter conversation window, which real talks never fill. |
| 96 GB | **Yes, comfortably.** The full-quality download at its full conversation length. |
| 128 GB or more | **Yes, with room to spare** for a second model loaded beside it. |

**Which model.** Every row above describes [Unsloth's build of Qwen3.6-35B-A3B](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-MTP-GGUF), the set of files this project recommends and measured. The author's own companion runs the Q8_0 of [a derivative build of the same model](https://huggingface.co/llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF), which was tested the same way a day earlier and has the same footprint and speed.

That is the whole decision for most readers. Three words explain where it comes from:

- **Quant.** The model comes as one set of files on Hugging Face, and each file is the same model at a different level of compression. The name of each file's compression level is its *quant*: **Q8_0** is the least compressed (biggest, best), **UD-Q3_K_M** the most (smallest, some quality lost). You download one of them, not all.
- **Context.** How much of the conversation the model can hold in mind at once, counted in *tokens* (roughly three-quarters of a word each). 16k means sixteen thousand tokens, about a short talk; 64k a long one; 262k the most this model allows. Holding more costs memory.
- **Resident.** How much of your Mac's memory the model server actually occupies once the file is loaded and a talk is going. It is always more than the file on disk. This is the number that decides whether it fits. Disk space is a separate question (see *Disk* below).

The measured size of every download at every context length, the rule Hearth applies to decide
what fits, and what is still an estimate: [Hardware measurements](hardware-measurements.md).

### Latency (measured on high-end Apple Silicon)

- Time-to-first-audio (TTS): **~0.42 s**
- Time-to-first-token (model): **~0.21 s**
- TTS real-time factor: **~0.24** (TTS layer alone, steady-state — roughly 4x faster than playback; the whole wired loop measures ~0.34 end-to-end)
- model throughput: **~92 tokens/s**

RTF must stay **< 1.0** for gapless streaming — the ~4x headroom above is what absorbs a slower chip. Earlier / smaller M-series chips will run the stack but slower, especially on TTS (MLX Metal performance scales with GPU generation); they have not been formally characterized.

### Disk

First-run weight download is **~42 GB** (model ~37 GB, TTS ~3 GB, STT ~1.6 GB, VAD negligible). Add ~15–20 GB for the Python virtualenv and the MLX kernel cache. Keep **~60 GB free** before installing. <!-- manual-lint: allow: GPU/OS kernel, the technical sense -->

### Lowering the floor

- **A smaller download of the same model.** The mid-size one is 22.7 GB instead of 37.8 GB and sounds nearly the same; the smallest is 17.1 GB. Which one to pick for your memory size: [Hardware measurements](hardware-measurements.md).
- **A shorter conversation window.** Letting the model hold less of the talk at once saves about 5 GB at the full-quality download. Hearth's fit check does this for you when it has to.
- **A lighter model.** A smaller model swaps in via `model =` in `config/active.toml`.
- **A smaller voice engine.** `chatterbox-turbo-{8bit,6bit,4bit}` saves ~1–2 GB at some voice-quality cost.

### Audio input

A working input device is required — the pipeline grabs the macOS default mic and speaker at launch, with no fallback.

- **A2DP** Bluetooth earbuds are output-only (no mic) and produce `Errno -9996` when set as the default input — the pipeline won't start.
- **HFP** Bluetooth provides a mic but forces both directions to low-fi mono ~16 kHz. Whisper accepts 16 kHz, so it works, but quality drops.
- **Safest:** built-in mic, a wired headset, or a USB audio interface. Connect Bluetooth *before* launch; switching devices mid-session needs a restart (the stream does not follow default-device changes live).

---

## Silver tier — rented raw GPU with root

If you don't have a large Apple Silicon machine, the sovereign-adjacent option is an **NVIDIA/CUDA box you rent but fully control** — bare-metal or a VM with **root**, not a managed inference service. As long as you own both ends of the box, the same trust posture applies: weights and conversation stay on hardware you administer.

Honest status: **the CUDA path is early and less-tested.** The `hearth[cuda]` install extra is a **placeholder today** — the Apple/MLX layers (MLX-Whisper, MLX Chatterbox) have no drop-in CUDA equivalent wired up yet, so expect to substitute components (a CUDA-capable TTS/STT and a CUDA model server such as `llama-server` or vLLM) and to do your own integration and verification. Treat this tier as "supported in principle, bring your own elbow grease," not turnkey.

Rough sizing if you go this route: a single modern data-center or high-end consumer GPU with **~24–48 GB of VRAM** covers the model at a mid quant plus a GPU TTS/STT, mirroring the gold-tier memory story. Keep everything resident on one box to preserve the local guarantee.

---

## Provider APIs are not a tier

Calling a hosted model, TTS, or STT API would be the easy path, and it is deliberately excluded. Sending your audio or text to a third-party endpoint breaks the entire premise of this project — local sovereignty, offline capability, and the guarantee that sensitive content never leaves hardware you control. A provider API is not a lower tier of the same thing; it is a different thing. If you need the local guarantee, use gold or silver. If you don't, this stack is not what you want.

---

*Memory numbers are measured on one Mac (an M3 Ultra, 2026-09-08); the verdicts by memory size apply Hearth's own fit rule to them and have not yet been confirmed by hand on smaller machines. Verify with a live memory reading on your own Mac before committing to a deployment.*
