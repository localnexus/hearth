# Listening behavior — VAD, turn-taking, barge-in

All four knobs live in one `VADParams(...)` (`bot ~L211`) — the single VAD source (drives both segmentation and barge-in).

| Param | Now | Raise it → | Lower it → |
|---|---|---|---|
| `confidence` | 0.7 | fewer false triggers; may miss soft speech | more sensitive; more noise-triggers |
| `start_secs` | 0.2 | slower to "hear" you start | snappier start; twitchier |
| `stop_secs` | 0.5 | waits longer before replying (fewer mid-sentence cutoffs) | replies sooner; may cut you off |
| `min_volume` | 0.6 | ignores quiet/background | picks up faint speech + noise |

- **Triggers on noise / itself** → raise `confidence` and/or `min_volume`.
- **Replies before I finish** → raise `stop_secs` (**but** see Smart-Turn note).
- **Barge-in feels laggy** → lower `start_secs` (barge-in fires on speech-start). Note the *tail* limit: an in-flight `generate()` finishes before the next turn (best-effort cancel) — see [debugging/tts-audio-cases.md](../debugging/tts-audio-cases.md) (Case study 3).

> ⚠️ **Turn-END uses TWO signals.** pipecat auto-loads **Smart Turn v3.x** (log: `Loaded Local Smart Turn`) alongside VAD — a semantic endpointer that can end your turn sooner (crisp finish) or hold it past `stop_secs` (mid-thought pause). If "too soon / too late" persists after tuning `stop_secs`, that model is the other lever (loads from `.venv/.../pipecat/audio/turn/smart_turn/data/`; not parameterized in `bot.py` — configuring it is a code change in the aggregator).
