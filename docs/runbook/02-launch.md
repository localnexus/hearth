# 2. BRING ONLINE — launch

*What this covers: the one command that starts the loop, what a healthy startup prints, which log lines are cosmetic, and how to confirm it's live.* · Runbook index: [`README.md`](README.md)

```bash
cd <the Hearth tree>          # no absolute path is assumed
./start.sh                    # preflight + launch; manual equivalent: .venv/bin/python -m hearth.pipeline.bot
# first time here? .venv/bin/python -m hearth.init  — templates, token, gates (docs/installing.md §6)
```
(The bot talks to `llama-server` at `http://127.0.0.1:8080/v1` by default — `LM_BASE_URL` / `LM_API_TOKEN` / `LM_PROVIDER` override it, see `start.sh`. Run it in an **iTerm** window — the app that holds the mic grant. Prepend `T4_METRICS=1` to print per-turn STT latency to stderr.)

> ⚠ **Ambient env overrides silently win.** Those same variables — plus `HEARTH_ROOT` / `HEARTH_DATA` —
> are read from the *environment*, so a stray `export` in your shell profile (or an inherited shell)
> re-routes a "default" launch to a different server or data root with no warning; the only visible
> symptom may be a dashed Engine line or a wrong-model reply. If Hearth isn't talking to the server
> you expect, check first: `env | grep -e '^LM_' -e '^HEARTH_'`. Keep machine-specific pins in a
> launch wrapper script, not in your shell profile.

**Healthy startup** prints, in order: `Pipecat 1.4.0` → `Loaded Silero VAD` → `Loaded Local Smart Turn v3.x` → in-process model loads (Whisper warm-up + Chatterbox load + voice conditionals, **~10–20 s warm**) → the `Linking … VADProcessor → MLXWhisperSTTService → … → MLXAudioTTSService → …` chain → **`StartFrame#0 reached the end of the pipeline, pipeline is now ready.`**

**Cosmetic log lines (ignore):**
- `You are using a model of type chatterbox_turbo to instantiate a model of type ''` — transformers registry mismatch.
- `ttfs_p99_latency not set, using default 1.0s` — pipecat metrics default.

**Confirm it's live:** speak a sentence, pause. Within **~2–3 s** you hear a reply (there is **no auto-greeting** — the bot waits for you to speak first). A good turn in the log reads:
```
VADProcessor#0: User started speaking → User stopped speaking
Generating chat from context [{'role':'user','content':'<your words>'}]
Bot started speaking … Bot stopped speaking
```
Speaking over the reply cuts it off — that's **barge-in** working.

> **First-ever synth on a fresh machine** adds a one-time **~15.8 s** MLX Metal-kernel JIT compile (cache persists on disk afterward). On this machine it's already paid — expect fast first turns unless your LLM server has to load the model (turn 1 only).
