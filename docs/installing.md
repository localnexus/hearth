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
  in Hugging Face *offline* mode) — the speech models are fetched once, by `install.sh` or by hand.
- **A terminal app you'll keep using** — Terminal.app, iTerm, VS Code's terminal. macOS
  grants the microphone to *that app*, not to Python, so pick one and launch from it (chapter 5).
- A working mic and speaker. Built-in, wired, or USB is safest; Bluetooth has caveats
  (see [HARDWARE-REQUIREMENTS → Audio input](HARDWARE-REQUIREMENTS.md#audio-input)).

## 1. One command

```bash
curl -fsSL https://raw.githubusercontent.com/localnexus/hearth/main/install.sh | bash
```

`install.sh` does chapters 1–4 of the by-hand page for you, then hands over to the first-run
setup (chapter 4 below). Every step checks first, does only what is missing, and prints one line:

| mark | meaning |
|---|---|
| `+` | done now |
| `·` | already there — a re-run repairs, it repeats nothing |
| `!` | a note (for example: no model server answering yet) |
| `-` | skipped (a flag, or you said no) |
| `x` | stopped — the line says why and what to run |

What it does, in order: checks the Mac (Apple Silicon, not root, disk) · installs PortAudio,
`uv` and `llama.cpp` with Homebrew · clones Hearth to `~/hearth` (or `--dir`) · builds the Python
environment and proves the speech pins hold · fetches the speech models (~4.6 GB, **asks first**)
· tells you if no model server answers · runs `hearth.init`.

It stops, honestly, at two things only you can do — the Xcode command-line tools (a macOS dialog)
and Homebrew itself (asks for your password). It prints their command and exits; run it, then run
`install.sh` again. Nothing it starts keeps running: the model server is your own process.

Prefer to read it first? Clone and run the same script: `git clone
https://github.com/localnexus/hearth && cd hearth && ./install.sh`. Flags: `--yes` (every
default, asks nothing, does not start Hearth) · `--dir DIR` · `--no-weights` · `--model REPO`
(fills in the `llama-server` line) · `--lm-url URL` · `--memory on|off` · `--no-init` · `--quiet`.

## 2. By hand

Every step the script takes, as commands you run yourself — system tools, the clone and the
Python environment, the speech models, a standalone voice-engine smoke test:
[Installing by hand](installing-by-hand.md). Come back here for chapter 3.

## 3. Bring a model server

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

**Choosing a model.** Two hard rules, both explained in [the model config chapter](config-manual/llm.md):
the model must be *loaded* by the server, and it must emit **no chain-of-thought** — a model
that streams `reasoning_content` while `content` stays empty stalls the voice loop. Use a plain
instruct model, or a hybrid-thinking model (Qwen3.6-class, GLM, …) with thinking forced off
(Hearth sends `reasoning_effort = "none"` from your model config on every request; `llama-server`
also has start-up switches for stubborn templates). For memory sizing — the default stack is
built around a ~35B-parameter, 3B-active MoE at Q8_0, ~37 GB — see HARDWARE-REQUIREMENTS.

`llama-server` is keyless unless you start it with `--api-key`; if you do, export the same
value as `LM_API_TOKEN` when launching Hearth.

> **If you use LM Studio instead.** Start its server (`:1234`), load the model, generate an API
> token, and in chapter 4 pass `--lm-url http://127.0.0.1:1234/v1`; Hearth reads that server's
> token from the file `lm_token_source` names in `config/serve.toml`, and the terminal path takes
> `LM_BASE_URL` / `LM_API_TOKEN` / `LM_PROVIDER=lmstudio`. LM Studio needs the model id to match
> **verbatim**, and its stack is version-sensitive — the runbook's
> [dependencies chapter](runbook/00-dependencies.md) keeps those notes.

## 4. First run — one command

One command turns the checkout into a configured install:

```bash
.venv/bin/python -m hearth.init
```

It copies the three starter config files into place, creates your access key, switches on
Hearth's web pages, asks whether the companion should remember you between conversations
(default no — memory writes durable records about a person, so it is never turned on silently),
and records your model if the server from chapter 3 answers.

It prints the key **once**, then offers to **start Hearth right there** — say yes and it becomes
the running program in that terminal (Ctrl-C stops it), showing the address to open. Re-running
is safe — anything in place is left alone and named, and the key is never printed again (it lives
at `config/serve-token`, readable only by you). `--help` lists the unattended flags (`--yes`,
`--memory on|off`, `--lm-url`, `--model-id`, `--serve`/`--no-serve`, `--quiet` for no banner).

It changes nothing that ships; the templates keep their everything-off defaults for anyone
copying files by hand. Which file does what: [The config layers](the-config-layers.md).

To keep everything you own **outside the checkout**, set `HEARTH_DATA` to any directory *before*
you run it (same `characters/` + `config/` layout; the shipped example stays reachable). Unset,
the checkout is where Hearth keeps your files.

Later: write your own companion ([Authoring a character](authoring-a-character.md)) and add a
voice you have the rights to ([Bring your own voice](bring-your-own-voice.md)).

## 5. Microphone permission (do this before the first launch)

macOS attributes microphone access to the **app that owns the terminal** — not to Python.
A denied mic does **not** raise an error: Hearth simply hears silence, forever.

1. Launch Hearth (chapter 6) once from your chosen terminal app; macOS prompts for Microphone
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

## 6. First launch

If chapter 4 already started Hearth, skip to the address. Otherwise, from the terminal app that
holds the mic grant:

```bash
.venv/bin/python -m hearth.serve
```

Open **`http://127.0.0.1:65001/admin/launch`** and paste the key from chapter 4 when asked.
On a fresh install that page offers **First run**: three steps that check your model server, record
the model id it advertises, start the companion, and confirm it heard you. After that the launch
page is the front door: **Start** brings the voice loop up (~10–20 s to warm, plus the one-time
kernel compile if the by-hand smoke test didn't already pay it), the **companion switcher** picks who is live, and <!-- manual-lint: allow: GPU/OS kernel, the technical sense -->
the links lead to settings, memory, the roster, and the companion's own control panel (`:65000`).

**Then speak first** — there is no greeting. A reply comes ~2–3 s after your pause (slower on the
first turn while the server loads the model). Talking over it cuts it off: that's barge-in working.

**The terminal path still works**: `./start.sh --check`, then `./start.sh` (no web pages involved);
`Ctrl-C` or `./stop.sh` stops it.

From here the [runbook](runbook/README.md) is the operating manual, with a symptom → fix table in
[fast recovery](runbook/05-fast-recovery.md).

## 7. Updating

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
| `portaudio.h file not found` during install | PortAudio missing — run `install.sh` again (or by-hand chapters 1–2). |
| `'str' object has no attribute '__module__'` | `transformers` drifted off 5.5.0 — run `install.sh` again (it re-applies the pins). |
| Startup fails loading Chatterbox/Whisper, mentions offline or cache | Weights not fetched — run `install.sh` (or by-hand chapter 3). |
| `./start.sh --check` says the server is unreachable | `llama-server` not running, or on another port — `LM_BASE_URL`. |
| Server returns `401` | It wants a key — `LM_API_TOKEN`. |
| Companion ready, you speak, nothing ever transcribes | Mic permission — chapter 5. |
| `[Errno -9996] Invalid input device` | The default input is output-only (A2DP earbuds) — pick a real mic in System Settings → Sound. |
| The companion goes quiet after `Generating chat` | The model is thinking out loud with no content — force thinking off ([llm.md](config-manual/llm.md)). |
| Reply arrives but the audio stutters | RTF ≥ 1 on this chip — try the `8bit` TTS variant, and check nothing else is hammering the GPU. |

Anything deeper: [debugging notes](debugging/README.md).
