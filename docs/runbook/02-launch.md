# 2. BRING ONLINE — launch

*What this covers: the one command that starts the loop, what a healthy startup prints, which log lines are cosmetic, and how to confirm it's live.* · Runbook index: [`README.md`](README.md)

```bash
cd <the Hearth tree>          # no absolute path is assumed
LM_API_TOKEN=$(cat ~/.lmstudio/lm-probe-token) $UV run python bot.py
```
(`$UV` = whatever `command -v uv` resolves — `./start.sh` does this for you and is the preferred entry point. Run it in an **iTerm** window — the app that holds the mic grant. Prepend `T4_METRICS=1` to print per-turn STT latency to stderr.)

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

> **First-ever synth on a fresh machine** adds a one-time **~15.8 s** MLX Metal-kernel JIT compile (cache persists on disk afterward). On this machine it's already paid — expect fast first turns unless LM Studio has to JIT-load the model (turn 1 only).
