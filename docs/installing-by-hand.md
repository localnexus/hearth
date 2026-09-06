# Installing Hearth by hand

The four chapters `install.sh` runs for you, as commands — for anyone who wants to see each
step, or whose machine needs a detour. The [install guide](installing.md) has the one-command
form and everything after it (model server, first run, microphone, first launch, updating).
Chapter numbers here match the installer's report and the guide's references.

---

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

Then continue with the [install guide](installing.md) from chapter 3, the model server.
