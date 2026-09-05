# Installing Hearth (macOS on Apple Silicon)

This is the full walkthrough behind the README's quickstart: every system prerequisite,
the two failures that look like Python problems and aren't, how the speech models get onto
the machine, and what a healthy first launch looks like. Budget an hour the first time, mostly
waiting on downloads.

Hearth's speech chain (Whisper STT, Chatterbox-Turbo TTS) runs on Apple's MLX framework, so
the **gold tier — an Apple Silicon Mac with generous unified memory — is the only path this
guide covers.** For sizing (memory floor, disk, which chips are fast enough) read
[HARDWARE-REQUIREMENTS](HARDWARE-REQUIREMENTS.md) first; for the CUDA/NVIDIA tier, which is
still a placeholder, the same page says honestly what to expect.

What you'll end up with:

```
mic → VAD → STT (in-process) → your llama-server → TTS (in-process) → speaker
```

Two of the three heavy pieces live *inside* Hearth's process. The third, the language
model, is a server you run yourself.

---

## 0. Before you start

- An Apple Silicon Mac (M-series) on a recent macOS, with **admin rights** (Homebrew needs
  them) and **~60 GB free disk** — ~42 GB of model weights plus the Python environment.
- **Network for the first run.** Hearth downloads nothing at runtime by default (it starts
  in Hugging Face *offline* mode) — the speech models are fetched once, in step 3.
- **A terminal app you'll keep using** — Terminal.app, iTerm, VS Code's terminal. macOS
  grants the microphone to *that app*, not to Python, so pick one and launch from it (step 7).
- A working mic and speaker. Built-in, wired, or USB is safest; Bluetooth has caveats
  (see [HARDWARE-REQUIREMENTS → Audio input](HARDWARE-REQUIREMENTS.md#audio-input)).

## 1. System tools

```bash
xcode-select --install                      # compilers (skip if already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # Homebrew, if absent
brew install portaudio                      # ← REQUIRED before the Python install (see below)
brew install uv                             # the Python/venv manager Hearth is built with
```

**PortAudio is a hard, non-obvious prerequisite.** Hearth's audio transport uses `pyaudio`,
which is compiled from source during the Python install against PortAudio's headers. Without
it the install dies with:

```
fatal error: 'portaudio.h' file not found
```

The installer reports that as a `pyaudio` wheel-build failure, which reads like a Python
problem. It isn't — install PortAudio and rerun step 2.

**Do not use the system Python.** macOS ships 3.9, and the MLX chain needs ≥ 3.11 (Hearth
pins 3.12). `uv` fetches a 3.12 interpreter for you; you never install Python by hand.

## 2. Get Hearth and build its environment

Hearth is **not on PyPI** (that name belongs to an unrelated project). Clone and install from
source, *editable*, so the engine finds its `config/` and `characters/` trees beside the code:

```bash
git clone https://github.com/localnexus/hearth
cd hearth
uv venv -p 3.12 && uv pip install -e ".[mac]"     # ~90 packages; a few minutes
```

(`uv sync --extra mac` is the lockfile-driven equivalent. Plain `uv sync` without the
extra installs the backend-neutral spine only — no TTS/STT — so always name the extra.)

Then prove the pin-critical part took:

```bash
.venv/bin/python - <<'PY'
import importlib.metadata as m
print("transformers", m.version("transformers"))     # must print 5.5.0
from mlx_lm.models.cache import KVCache               # the import that fails when unpinned
from mlx_audio.tts.utils import load_model
print("mlx-audio import OK")
PY
```

**Why `transformers` is pinned to exactly 5.5.0:** newer 5.x releases break the TTS engine's
import (`AttributeError: 'str' object has no attribute '__module__'`), and `mlx-audio`
declares no upper bound, so an unpinned resolve drifts onto a broken version. The pin lives in
`pyproject.toml`; if you ever see that error, something re-resolved the environment — rerun
the install line above rather than upgrading anything by hand.

## 3. Fetch the speech models (one-time, needs network)

Hearth starts with `HF_HUB_OFFLINE=1` — no Hugging Face calls at runtime, ever, weights frozen
at what's on disk. Good for privacy; it also means a **fresh machine's first launch would fail
to load the speech models.** Pull them once, explicitly:

```bash
HF_HUB_OFFLINE=0 .venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
for repo in ("mlx-community/chatterbox-turbo-fp16",       # TTS,  ~3 GB
             "mlx-community/S3TokenizerV2",               # TTS dependency, small
             "mlx-community/whisper-large-v3-turbo"):     # STT,  ~1.6 GB
    print(snapshot_download(repo))
PY
```

They land in `~/.cache/huggingface/hub` and are reused by every later run. (Or launch once
with `HF_HUB_OFFLINE=0 ./start.sh`, which downloads on demand.)

> Use the pre-converted `mlx-community/chatterbox-turbo-fp16` repo — **not** the original
> ResembleAI weights. The raw layout has no `config.json`, and the MLX loader fails on it with
> `FileNotFoundError: Config not found`. The `8bit`/`6bit`/`4bit` variants of the same
> mlx-community repo also work if you want to save ~1–2 GB at some voice-quality cost.

## 4. Smoke-test the voice engine

This proves the TTS engine loads, clones a voice from a reference clip, and streams — in
isolation, before the whole pipeline is involved. It uses the rights-clean default voice that
ships with the repo:

```bash
.venv/bin/python - <<'PY'
import time, wave, numpy as np
from mlx_audio.tts.utils import load_model
model = load_model("mlx-community/chatterbox-turbo-fp16")
ref = "characters/example/voices/default/sample.wav"
text = "Hello from Hearth. If you can hear this, the voice engine works."
for run in range(2):                                  # run 0 may include a one-time compile
    t0 = time.perf_counter(); chunks = []; first = None
    for r in model.generate(text=text, ref_audio=ref, stream=True, streaming_interval=2.0):
        first = first or time.perf_counter() - t0
        chunks.append(np.array(r.audio, dtype=np.float32).reshape(-1))
    audio = np.concatenate(chunks); wall = time.perf_counter() - t0; dur = audio.size / 24000
    print(f"run {run}: first audio {first:.2f}s, {dur:.1f}s of speech in {wall:.1f}s "
          f"(RTF {wall/dur:.2f}), {len(chunks)} chunks")
with wave.open("/tmp/hearth-tts-check.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
    w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
PY
afplay /tmp/hearth-tts-check.wav
```

**What good looks like** (run 1, on a high-end M-series chip): first audio well under a
second, **RTF ≈ 0.35**, more than one chunk, and the file sounds like the reference clip.
(That bar is for this standalone script; the in-process service settles lower, ~0.24 — see
the hardware-requirements doc.)
RTF is wall time divided by audio duration — it must stay **below 1.0** or playback will have
gaps. Slower chips run higher; ~0.6 is still fine.

**The first-ever synth on a machine can take ~15 s longer** — MLX compiles its Metal kernels
on first use and caches them on disk. That's why the script runs twice; judge run 1.

| If you see | It means |
|---|---|
| `AttributeError: 'str' object has no attribute '__module__'` | `transformers` isn't 5.5.0 — step 2. |
| `FileNotFoundError: Config not found …` | Raw ResembleAI weights, not the mlx-community repo — step 3. |
| an offline / "cannot find … in cache" error | The weights weren't fetched — step 3. |
| The log line `You are using a model of type chatterbox_turbo to instantiate a model of type ''` | Cosmetic. Ignore it. |

## 5. Bring an LLM server

Hearth ships **no language model and no inference server** — it talks to any OpenAI-compatible
endpoint. The recommended default is **`llama-server`** from llama.cpp, listening on
`http://127.0.0.1:8080/v1`:

```bash
brew install llama.cpp
# pull a GGUF from Hugging Face and serve it (quant tag optional; -c 0 = the model's own context):
llama-server -hf <user>/<model-repo>[:quant] -c 0 --port 8080 -a my-model
# …or serve a GGUF you already have:
llama-server -m /path/to/model.gguf -c 0 --port 8080 -a my-model
```

Then confirm it answers:

```bash
curl -s http://127.0.0.1:8080/v1/models | python3 -c 'import sys,json;[print(m["id"]) for m in json.load(sys.stdin)["data"]]'
```

**Choosing a model.** Two hard rules, both explained in [the LLM config chapter](config-manual/llm.md):
the model must be *loaded* by the server, and it must emit **no chain-of-thought** — a model
that streams `reasoning_content` while `content` stays empty stalls the voice loop. Use a plain
instruct model, or a hybrid-thinking model (Qwen3.6-class, GLM, …) with thinking forced off
(Hearth sends `reasoning_effort = "none"` from your model config on every request; `llama-server`
also has start-up switches for stubborn templates). For memory sizing — the default stack is
built around a ~35B-parameter, 3B-active MoE at Q8_0, ~37 GB — see HARDWARE-REQUIREMENTS.

`llama-server` is keyless unless you start it with `--api-key`; if you do, export the same
value as `LM_API_TOKEN` when launching Hearth.

> **If you use LM Studio instead.** Start its server (`:1234`), load the model, generate an API
> token, and in step 6 pass `--lm-url http://127.0.0.1:1234/v1`; Hearth reads that server's
> token from the file `lm_token_source` names in `config/serve.toml`, and the terminal path takes
> `LM_BASE_URL` / `LM_API_TOKEN` / `LM_PROVIDER=lmstudio`. LM Studio needs the model id to match
> **verbatim**, and its stack is version-sensitive — the runbook's
> [dependencies chapter](runbook/00-dependencies.md) keeps those notes.

## 6. First run — one command

One command turns the checkout into a configured install:

```bash
.venv/bin/python -m hearth.init
```

It copies the three starter config files into place, creates your access key, switches on
Hearth's web pages, asks whether the companion should remember you between conversations
(default no — memory writes durable records about a person, so it is never turned on silently),
and records your model if the server from step 5 answers.

It prints the key **once**, then offers to **start Hearth right there** — say yes and it becomes
the running program in that terminal (Ctrl-C stops it), showing the address to open. Re-running
is safe — anything in place is left alone and named, and the key is never printed again (it lives
at `config/serve-token`, readable only by you). `--help` lists the unattended flags (`--yes`,
`--memory on|off`, `--lm-url`, `--model-id`, `--serve`/`--no-serve`).

It changes nothing that ships; the templates keep their everything-off defaults for anyone
copying files by hand. Which file does what: [The config layers](the-config-layers.md).

To keep everything you own **outside the checkout**, set `HEARTH_DATA` to any directory *before*
you run it (same `characters/` + `config/` layout; the shipped example stays reachable). Unset,
the checkout is where Hearth keeps your files.

Later: write your own companion ([Authoring a character](authoring-a-character.md)) and add a
voice you have the rights to ([Bring your own voice](bring-your-own-voice.md)).

## 7. Microphone permission (do this before the first launch)

macOS attributes microphone access to the **app that owns the terminal** — not to Python.
A denied mic does **not** raise an error: Hearth simply hears silence, forever.

1. Launch Hearth (step 8) once from your chosen terminal app; macOS prompts for Microphone
   access for that app — allow it.
2. If there was no prompt, or you clicked the wrong thing: **System Settings → Privacy &
   Security → Microphone → enable your terminal app**, then relaunch the app.

To prove the process actually receives signal, independent of the pipeline:

```bash
.venv/bin/python -c "
import pyaudio, numpy as np
p = pyaudio.PyAudio(); s = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1600)
print('peak per 100 ms:', [int(np.abs(np.frombuffer(s.read(1600, exception_on_overflow=False), '<i2')).max()) for _ in range(20)])"
```

Speak while it runs. Speech peaks in the **thousands**; a flat line near zero (< 100) while
you talk means the process is getting silence — a permission problem, not a Hearth bug.

## 8. First launch

If step 6 already started Hearth, skip to the address. Otherwise, from the terminal app that
holds the mic grant:

```bash
.venv/bin/python -m hearth.serve
```

Open **`http://127.0.0.1:65001/admin/launch`** and paste the key from step 6 when asked.
On a fresh install that page offers **First run**: three steps that check your LLM server, record
the model id it advertises, start the companion, and confirm it heard you. After that the launch
page is the front door: **Start** brings the voice loop up (~10–20 s to warm, plus the one-time
kernel compile if step 4 didn't already pay it), the **companion switcher** picks who is live, and
the links lead to settings, memory, the roster, and the companion's own control panel (`:65000`).

**Then speak first** — there is no greeting. A reply comes ~2–3 s after your pause (slower on the
first turn while the server loads the model). Talking over it cuts it off: that's barge-in working.

**The terminal path still works**: `./start.sh --check`, then `./start.sh` (no web pages involved);
`Ctrl-C` or `./stop.sh` stops it.

From here the [runbook](runbook/README.md) is the operating manual, with a symptom → fix table in
[fast recovery](runbook/05-fast-recovery.md).

## 9. Updating

```bash
git pull
uv pip install -e ".[mac]"      # picks up any dependency change; re-applies the pins
```

Your `config/active.toml`, `config/overrides.toml`, `config/serve.toml` + key, `config/memory.toml`, your own characters
and model configs, and every companion's `sessions/` / `captures/` are gitignored (or live
under `HEARTH_DATA`), so a pull never touches them.
Model weights live in the Hugging Face cache and are untouched too.

## Quick troubleshooting

| Symptom | Cause → fix |
|---|---|
| `portaudio.h file not found` during install | PortAudio missing — step 1, then rerun step 2. |
| `'str' object has no attribute '__module__'` | `transformers` drifted off 5.5.0 — rerun step 2's install line. |
| Startup fails loading Chatterbox/Whisper, mentions offline or cache | Weights not fetched — step 3. |
| `./start.sh --check` says the server is unreachable | `llama-server` not running, or on another port — `LM_BASE_URL`. |
| Server returns `401` | It wants a key — `LM_API_TOKEN`. |
| Companion ready, you speak, nothing ever transcribes | Mic permission — step 7. |
| `[Errno -9996] Invalid input device` | The default input is output-only (A2DP earbuds) — pick a real mic in System Settings → Sound. |
| The companion goes quiet after `Generating chat` | The model is thinking out loud with no content — force thinking off ([llm.md](config-manual/llm.md)). |
| Reply arrives but the audio stutters | RTF ≥ 1 on this chip — try the `8bit` TTS variant, and check nothing else is hammering the GPU. |

Anything deeper: [debugging notes](debugging/README.md).
