# Misc

| Tweak | Where | How |
|---|---|---|
| **T4 latency logs** | env `T4_METRICS=1` | prints `[T4] <clock> <label> (+N ms)` at STT boundaries (`stt_start`, `stt_done`) + pipecat's own metrics. **Note:** there are **no TTS-side T4 markers** — the live trace shows STT timing only; TTS latency comes from `test_mlx_tts.py`. |
| **Keep alive during pauses** | `PipelineWorker(..., cancel_on_idle_timeout=False)` in `main()` (`~L421`) | already `False`; pipecat's default would cancel the worker after ~5 min of no speech. |
| **HF Hub airgap** | **default ON** — `HF_HUB_OFFLINE=1` set in `bot.py` | no HF calls on startup (no telemetry, no cache-revision HEADs); weights load from cache. To deliberately refresh a model: `HF_HUB_OFFLINE=0 ./start.sh`. |
| **transformers pin** | `.venv` | **`transformers==5.5.0` is mandatory** — newer crashes the TTS import (`'str' object has no attribute '__module__'`). See the [dependencies runbook](../runbook/00-dependencies.md). |
