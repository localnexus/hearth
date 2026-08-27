# 3. BRING OFFLINE — stop the loop (normal)

*What this covers: the day-to-day stop — Ctrl-C or `pkill` to release the mic and free the in-process STT+TTS weights, plus how to verify it's down.* · Runbook index: [`README.md`](README.md)

The loop is a single foreground python process. Stopping it **releases the microphone** and frees the **in-process STT + TTS weights** (both live inside `bot.py` now); your LLM server keeps running.

- **Foreground:** press **`Ctrl-C`** in the iTerm window running `bot.py`.
- **From another shell / if detached:**
  ```bash
  pkill -f "python[0-9.]* -m hearth\.pipeline\.bot"
  ```
- **Verify it's down** (expect no output):
  ```bash
  pgrep -f "python[0-9.]* -m hearth\.pipeline\.bot"
  ```

That's the whole "offline" step for day-to-day use — the mic indicator turns off and nothing is left listening.
