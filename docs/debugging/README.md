# DEBUGGING PLAYBOOK — in-process TTS

**Debugging knowledge for the voice loop.** The general *method* for cracking a wedged/silent local pipeline is a set of reusable Plays; the case studies below record the concrete faults seen with the in-process TTS engine.

---

## The general Plays

- **Play 1 `sample <pid>`** — is the event loop blocked in a sync MLX call? (The TTS engine is MLX too — see the stream case study below.)
- **Play 2 `lsof`** — did it reach the model server (`:8080` for `llama-server`, `:1234` if you use LM Studio)? spot unexpected outbound (HF fetches on startup are expected).
- **Play 3 raw mic probe** — real signal vs TCC silence.
- **Play 4 curl-then-SDK** — model layer isolation (reasoning-model empty `content`, bad id).
- **Play 5 read the base class** — `SegmentedSTTService`/`TTSService` behavior is ground truth.
- **Play 6 monitor the log** — include terminal states (`Traceback|Error|CANCEL`), not just happy-path markers.
- **Play 7 process lineage** — `uv` wraps a child python; mic permission attaches to the terminal GUI (iTerm).

---

## Topic files

| File | What it covers |
|---|---|
| [tts-audio-cases.md](tts-audio-cases.md) | Case studies 1–10: the MLX thread-local stream crash, cold-vs-warm first synth, barge-in tail, filler/non-word artifacts, false "clipping", verifying a render is speech, silent hybrid-model, `Errno -9996` audio-device death, the transformers-pin import crash, and cue-tag / `*action*` leaks spoken aloud. |
| [session-continuity-faults.md](session-continuity-faults.md) | Faults S1–S5: the bare-`start.sh` chooser/exit-2, resume "not found", persona/model mismatch after resume, malformed session files, and where the transcript lives / how to purge it. |
| [tts-boundary-isolation.md](tts-boundary-isolation.md) | Isolating the TTS boundary now that it's in-process — the standalone `test_mlx_tts.py` unit test that exercises `run_tts` with no mic/model/pipeline. |
| [plain-words-check.md](plain-words-check.md) | `manual-lint` — the standing check that Hearth's reader-facing prose keeps the plain words (Hearth, access key, companion, model server); how to run it, what it reads, what is exempt |
