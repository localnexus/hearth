# 4. BRING OFFLINE — full teardown (reclaim memory)

*What this covers: the extra steps to get the weights out of memory — stop the consumer, then stop the LLM server — and what durable config to leave in place.* · Runbook index: [`README.md`](README.md)

Only if you want the weights out of memory. Order: stop the consumer first, then the LLM server.

1. Stop `bot.py` (§3). This frees the in-process **MLX-Whisper *and* Chatterbox-Turbo** weights in one shot (both are in-process — no separate TTS app to quit).
2. **The LLM server** — stop `llama-server` (Ctrl-C in its window). Frees the largest GPU-memory chunk. (LM Studio: eject the model or quit the app; `lms unload <id>` also works.)
3. Leave your GGUF weights, the `.venv`, and the HF weight cache in place — durable config, not runtime.

Nothing needs `sudo`; nothing is a service/daemon. There is no cloud tier (this is local-only by construction — sensitive text stays on the box).
