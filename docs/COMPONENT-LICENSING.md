# COMPONENT-LICENSING — the voice pipeline

Inventory of every integrated component and its license, plus what the licensing picture reveals about why this stack is rare. See the caveat footer (licenses change).

Pipeline: `mic → Silero VAD → MLX-Whisper-turbo → an LLM (llama-server) → Chatterbox-Turbo (mlx-audio) → speaker`

The **default** LLM server is `llama-server` from llama.cpp (**MIT**), so the default path is permissively licensed end to end. **LM Studio is a supported alternative**, and it is the one component with a proprietary EULA — Callout 1 covers what that does and doesn't allow.

---

## Master table

> **Code license vs. model-weight terms** differ for the TTS and LLM layers; both are listed where relevant.

### Framework & libraries

| Component | Role | Source | License | Commercial? |
|---|---|---|---|---|
| **Pipecat** (`>=1.4.0`) | Pipeline orchestration, barge-in, VAD glue | [pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat/blob/main/LICENSE) | **BSD-2-Clause** | Yes |
| **MLX** (`>=0.31.2`) | Apple-Silicon ML runtime (Metal kernels) | [ml-explore/mlx](https://github.com/ml-explore/mlx/blob/main/LICENSE) | **MIT** | Yes |
| **mlx-audio** (`>=0.4.4`) | In-process TTS host (runs Chatterbox) | [Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio/blob/main/LICENSE) | **MIT** | Yes |
| **mlx-lm** (`==0.31.3`) | MLX model loader (mlx-audio dep) | [ml-explore/mlx-lm](https://github.com/ml-explore/mlx-lm/blob/main/LICENSE) | **MIT** | Yes |
| **transformers** (`==5.5.0`, pinned) | Tokeniser / model-config | [huggingface/transformers](https://github.com/huggingface/transformers/blob/main/LICENSE) | **Apache-2.0** | Yes |
| **NLTK** | Sentence-boundary detection | [nltk/nltk](https://github.com/nltk/nltk/blob/develop/LICENSE.txt) | **Apache-2.0** | Yes |
| **onnxruntime** | ONNX runtime for Silero (no PyTorch) | [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) | **MIT** | Yes |
| **aiohttp** (`>=3.14.1`) | Async HTTP (control server / LLM client) | [aio-libs/aiohttp](https://github.com/aio-libs/aiohttp/blob/master/LICENSE.txt) | **Apache-2.0** | Yes |
| **llama.cpp `llama-server`** (not bundled — you install it) | **The default LLM server**: serves GGUF weights over an OpenAI-compatible API on `:8080` | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) | **MIT** | Yes |

### STT · LLM · TTS · VAD (models & engines)

| Component | Role | Source | License | Commercial? |
|---|---|---|---|---|
| **MLX-Whisper** (`>=0.4.3`) | STT library (Whisper on MLX) | [ml-explore/mlx-examples](https://github.com/ml-explore/mlx-examples/blob/main/LICENSE) | **MIT** | Yes |
| **Whisper-large-v3-turbo** weights (`mlx-community/…`) | STT weights (~1.6 GB) | [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | **MIT** (OpenAI; conversion inherits) | Yes |
| **Qwen3.6-35B-A3B** base (`qwen/…`, a stock default) | LLM content | [Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | **Apache-2.0** (not the custom Qwen license) | Yes |
| **An uncensored re-quant** of the same base | Alt LLM (fewer refusals) | third-party publisher on Hugging Face | **Apache-2.0** (inherited from the base) | Yes |
| **Chatterbox-Turbo** code | TTS engine (clone, streaming) | [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox/blob/main/LICENSE) | **MIT** | Yes |
| **Chatterbox-Turbo** weights (`mlx-community/chatterbox-turbo-fp16`, ~3 GB) | TTS weights (fp16) | [ResembleAI/chatterbox-turbo](https://huggingface.co/ResembleAI/chatterbox-turbo) | **MIT** — *but see watermark note below* | Yes |
| **S3TokenizerV2** (Chatterbox dep, auto-fetched) | Speaker-embedding sub-model | [mlx-community/S3TokenizerV2](https://huggingface.co/mlx-community/S3TokenizerV2) | **MIT** (inherits Chatterbox) | Yes |
| **Silero VAD** (via `pipecat-ai[silero]`) | Turn detection + barge-in trigger | [snakers4/silero-vad](https://github.com/snakers4/silero-vad/blob/master/LICENSE) | **MIT** | Yes |
| **Smart Turn** (optional, semantic end-of-turn) | End-of-turn model (loaded w/ Silero) | [pipecat-ai/smart-turn](https://huggingface.co/pipecat-ai/smart-turn) | **BSD-2-Clause** | Yes |

### The two things that aren't simple permissive licenses

Neither is on the default path: one is an **optional alternative server**, the other is operator-supplied audio.

| Component | Role | Governing framework |
|---|---|---|
| **LM Studio** (desktop app) — *alternative to `llama-server`* | Serves the LLM over an OpenAI-compat API at `:1234` | **Proprietary EULA, closed-source** — Callout 1 |
| **The reference WAV** (operator-supplied) | Voice identity for cloning | **Not a software license — likeness / publicity rights** — Callout 2 |

**TTS watermark (a fixed, model-inherent limit):** every Chatterbox clip carries Resemble AI's **Perth (PerTh) neural watermark** — imperceptible, survives re-encoding, built for synthetic-content detection. It's baked into the model regardless of which voice you clone. MIT doesn't *require* preserving it, but stripping it defeats its responsible-use purpose. This is a limit that no configuration choice removes.

---

## Callout 1 — Closed / EULA-bound: LM Studio (only if you choose it)

**This callout applies only if you run LM Studio.** The default server, `llama-server`, is MIT — pick it and every component in the stack is open-source.

**LM Studio is proprietary, closed-source**, governed by Element Labs' App Terms (effective July 1 2025). If you use it, it is the **only** non-open component in the stack.

- **Commercial use — now free at work** (as of July 2025; prior work-use restriction lifted — [lmstudio.ai/blog/free-for-work](https://lmstudio.ai/blog/free-for-work)). Permitted: personal use, internal business use, local-inference API.
- **Forbidden:** redistribution to third parties, service-bureau / SaaS reselling of its API, reverse-engineering the binary. (The `lmstudio-ai/lms` CLI is MIT, but the app + inference backend are not.)
- **Served weights** keep their own upstream licenses (e.g. Qwen Apache-2.0); the EULA governs only the app.

**It's replaceable — and in fact already replaced.** Because it can't be bundled or hosted, it was the dependency to shed before this stack could ship; `llama-server` is now the documented default and LM Studio is optional. The candidate replacement paths, for the record (two MIT-licensed; MTPLX open-source, license to verify):
- **`mlx_lm.server`** — MLX-native (keeps the stack MLX end-to-end).
- **`llama.cpp` `llama-server`** — serves the same GGUF over the same OpenAI-compat API. **This is the one that was taken.**
- **MTPLX** — MLX-native server doing **native MTP speculative decoding**; a **~2.24× decode** speedup on the Qwen3.6 family, OpenAI-compatible, self-described open-source *(license to verify — see caveat footer)*. Distinct from the two above: a **performance upgrade**, not just a licensing shed — but runs **MLX, not GGUF** (weight conversion needed) + needs on-hardware verification.

The migration was modest and localized, and is done: the token-panel's capacity/identity probe is provider-dispatched (LM Studio's `/api/v0/models` ↔ llama.cpp's `/props`), and the model's thinking-off moves from LM Studio's **GUI prompt-template edit** to a request param Hearth already sends (`reasoning_effort`) or a server start-up flag — an *upgrade* (reproducible, no manual GUI step). Trade-off for choosing `llama-server`: you lose the GUI model-manager's warm multi-model A/B convenience. **Running `llama-server` makes the entire stack open-source and redistributable.**

### Addendum — the runtime backends, opened up

Question: may the LM Studio backends ship with a published copy of this stack?
Method: EULA inspection + per-file inspection of `~/.lmstudio/extensions/backends/`.

**EULA answer (Element Labs App Terms, effective July 1, 2025):
redistribution is flatly prohibited.** "You will not … sublicense, distribute, sell … lease,
rent, loan, or otherwise transfer the Software or the Documentation to any third party"; the
grant is "solely for Your personal and/or internal business purposes." That covers the app
installer **and** the runtime packs (components of the Software; delivered only through the
app/`lms`). Private copying for personal/internal use is squarely inside
"personal/internal" — unaffected.

**What is actually inside the packs** (inspected, this machine):

| Piece | What it actually is | License reality |
|---|---|---|
| llama.cpp pack (2.24.0) | LM Studio's **build of upstream-MIT llama.cpp**, refactored (thin `llama-server` launcher + `libllama-server-impl.dylib`, ggml/llama/mtmd dylibs) **+ proprietary glue** (`llm_engine.node`, `liblmstudio_bindings.node`, `libllm_engine.dylib`). No LICENSE files ship in the pack. | Their build artifacts ride the EULA (their refactor/patches need not be MIT) — don't extract-and-ship. Upstream llama.cpp is MIT **with official macOS-arm64 release binaries** — the clean substitute. |
| mlx-llm pack (1.10.0) | **Thin proprietary glue** (3 files: `libllm_engine.dylib`, `liblmstudio_bindings.node`, `llm_engine_mlx_amphibian.node`) pointing at the vendor tree. | Glue = EULA-bound. All the substance lives one directory over ↓ |
| `vendor/_amphibian` (1.7 GB) | CPython 3.11 runtime (PSF) + a venv of **exact-pinned PyPI wheels** (mlx 0.31.2, mlx_lm 0.31.3, mlx_vlm 0.6.3, outlines, transformers 5.12.1, … — all MIT/Apache/BSD, every one fetchable from PyPI at that exact version) + **vendored `mlx_engine` source** + `openai_harmony 0.0.3+lmstudio` (Apache-2.0 upstream). | **~Entirely OSS.** `mlx_engine` is LM Studio's *own* MLX engine and it is **public + MIT** ([lmstudio-ai/mlx-engine](https://github.com/lmstudio-ai/mlx-engine)). Caveat: that repo has **no tags/releases** — pack numbers (1.10.0/1.11.0) are LM Studio-internal, so a rebuild pins by *commit* (pre-#2250), not tag. Take the source from the public repo, not from the pack, to keep provenance clean. |

**The refinement this forces on the "unreconstructible" finding:** the *packs* are
unreconstructible and unredistributable — but their *OSS substance* is reconstructible from
public sources (PyPI exact versions + the MIT mlx-engine repo + upstream llama.cpp). The only
truly unobtainable pieces are LM Studio's glue binaries, **which exist to talk to the LM Studio
app** — a build that serves via `llama-server` / `mlx_lm.server` (the replacement paths above)
never touches them.

**Publishing verdict: NOT blocked.** Options ladder:
1. **Private copying for personal/internal use** — fine, no license issue ("internal").
2. **Publish with BYO-LM-Studio instructions** — permitted (users install under their own
   EULA acceptance); pinned installers are still served from the vendor's CDN, so instructions can
   pin — but this lane is fragile (the vendor CDN could drop old builds any day) and inherits the
   packs-are-latest-only trap for the MLX lane. Acceptable as an interim stopgap only.
3. **Ship an open-source replacement engine** (llama-server and/or mlx_lm.server / mlx-engine) — the clean,
   durable path; makes the published stack 100 % redistributable OSS. **This is the posture taken:**
   `llama-server` is the documented default, and no inference server is bundled at all.
4. **Ask Element Labs for permission** — only needed to ship their
   packs verbatim. Path 3 makes this unnecessary.

Net for publishing: **publish the code freely; document `llama-server` (MIT, BYO — nothing bundled) as the
run path, with BYO-LM-Studio as a labelled alternative for operators who prefer its workbench.** A published
build need not ship the exact pinned validated stack, so pin-fragility does not gate publishing.

---

## Callout 2 — Not a software license: the reference voice

**Voice cloning sits on a different legal axis from open-source licensing — and it's the operator's axis, not the project's.**

Chatterbox clones a voice acoustically from whatever reference WAV **the operator supplies** — no transcript, no consent workflow; the output sounds like the source speaker. The MIT/Apache licenses on the *code and weights* say nothing about the *likeness* of that source. Whether a given voice may be used, and for what, is a **right-of-publicity / personality-rights / character-IP** question that attaches to the operator's chosen source audio and is **the operator's responsibility.** This project neither asks what audio you use nor recommends any particular source.

- Software licenses govern the generator; they don't grant permission to reproduce a person's (or character's) voice.
- Those rights attach to the *source material*, not to the model doing the cloning — so Chatterbox's MIT license is simply not the relevant instrument here.
- The reference voice's usage posture is recorded in the bundle's `voice.toml` (`license`/`source`); see [bring-your-own-voice.md](bring-your-own-voice.md). Swapping the voice is a `voice =` change in `config/active.toml` (self-contained bundles under `characters/<char>/voices/`).
- For anything outward-facing, the operator uses a voice they have rights to (their own, a consent-licensed clip, or a cleared preset). That's their call and, if needed, their counsel's — not something a license inventory resolves.

---

## Synthesis — why the licensing helps explain the rarity

**Thesis:** the software stack is overwhelmingly permissive (which makes it buildable by one person locally), while the rare/hard parts are a closed serving app and voice-likeness rights. **Verdict: confirmed, with one update.**

**The permissive stratum is near-total.** Every framework, library, and model layer is **MIT, BSD-2-Clause, or Apache-2.0** — Pipecat/Smart-Turn (BSD-2); MLX, mlx-audio, mlx-lm, MLX-Whisper + Whisper weights, Chatterbox code+weights, Silero, onnxruntime (MIT); Qwen weights, NLTK, transformers (Apache-2.0). So **any individual can download, assemble, and run the whole stack at zero licensing cost** — no sign-up, key, cap, or phone-home. That's the enabling condition for "one person builds it locally."

**The friction is exactly where the theory predicts — but it's thinner than it once was:**

1. **LM Studio** *was* a real commercial blocker until **July 2025**, when work-use was unlocked. What remains is only the redistribution/SaaS ban — so it deters *shipping a bundled/hosted product*, not building or internal use. And it was replaceable (Callout 1): with `llama-server` as the default, that deterrent is off the default path entirely.
2. **Voice-likeness rights** are the deeper barrier to shipping *any arbitrary-voice product* — which is why commercial offerings (Cartesia, ElevenLabs, Resemble's own service) use preset or consent-licensed voices rather than arbitrary reference clips. This is a market-wide constraint, independent of our stack.

**Net:** licensing isn't what makes the stack *rare* — it's what makes it *possible*. With LM Studio's 2025 unlock (and its replaceability), the durable reasons few ship this are (a) **Apple-Silicon hardware exclusivity** (see [HARDWARE-REQUIREMENTS.md](HARDWARE-REQUIREMENTS.md)), (b) **voice-likeness rights** for arbitrary-voice products, and (c) the **four-axis assembly difficulty** (genuinely rare on the full four-axis combination). The permissive licensing is the reason it *can* be built, not the reason it isn't.

---

## Caveat footer

- **Licenses change.** Every entry is cited to its upstream source. Re-verify before any deployment — especially **LM Studio**, if you use it (its EULA has changed at least once — Callout 1 addendum), and **Qwen** (Alibaba uses custom licenses for some tiers).
- **Code license ≠ model-weight terms** for the TTS/LLM layers; both are listed where they differ.
- **Descriptive, not legal advice.** This summarises what licenses say; it is not a legal opinion. The voice-likeness question in particular is the operator's, and needs qualified counsel — outside the scope of a licensing inventory.
- **Unverified:** Silero VAD's HF card returned 401 — GitHub `LICENSE` (MIT) used instead, corroborated by PyPI. Smart Turn's active use is optional; its BSD-2-Clause license is verified regardless.
