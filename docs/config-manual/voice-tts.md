# Voice & TTS (in-process Chatterbox-Turbo)

TTS is `MLXAudioTTSService` in `mlx_tts_service.py`, loaded inside `bot.py`. There is **no separate port** — synthesis runs in-process. (There *is* a per-character / per-voice **profile system** for the live knobs — the panel's `profile.toml` presets; see the prosody section below.)

**Change the voice** → set `voice =` in **`config/active.toml`** (names a self-contained bundle at `characters/<character>/voices/<voice>/`). **Edit + restart** — nothing is hot-swapped. *(`config_loader` reads the bundle's `voice.toml` and passes its `ref_wav` to `MLXAudioTTSService`.)* Chatterbox clones **zero-shot from a reference WAV** — no training, no embedding files, no profile. Requirements: clean speech, **mono, ~24 kHz** (other rates resampled), **> 5 s** (it under-conditions below ~5 s). **Restart required**: `prepare_conditionals(ref_wav)` runs once at init and is cached for the process.
```bash
# sanity-check a candidate WAV (format + duration):
afinfo /path/to/ref.wav | grep -E 'sample rate|channels|duration'
```
> ⚠️ Reference audio is the operator's responsibility — for anything outward-facing, use a voice you have rights to (add a bundle whose `voice.toml` carries the `license`/`source` provenance). See [bring-your-own-voice.md](../bring-your-own-voice.md).

### Adding a voice, or a whole character

Both are pre-runtime, path-discovered (no registration/index step), then a restart.

**Add a VOICE to an existing character** — the voice bundle is self-contained:
1. `mkdir characters/<character>/voices/<new>/`
2. Drop in `sample.wav` (the clone reference — meets the requirements above) **and** `voice.toml` with the minimum keys `tag = "<new>"` and `ref_wav = "sample.wav"` (relative → resolved against the bundle dir; carry `license`/`source` for provenance).
3. Set `voice = "<new>"` in `config/active.toml`. **Restart.**

**Add a CHARACTER** — a character is one folder (persona + at least one voice):
1. `mkdir characters/<new>/` with a `persona.md` holding non-empty `## IDENTITY` and `## SOUL` sections (fills the `{{persona}}` slot).
2. Add at least one voice bundle under `characters/<new>/voices/<voice>/` (as above).
3. Set `character = "<new>"` and `voice = "<voice>"` in `config/active.toml`. **Restart.**

The prompt template is **MODEL-scoped** (character-agnostic), so a new character needs **no** template change. A missing/malformed file fails fast at startup with a `ConfigError` naming the exact file.

**Change the TTS model / quantization** → `MODEL_REPO` (`tts ~L100`, default `mlx-community/chatterbox-turbo-fp16`). fp16 gives RTF ~0.24 at best quality (RAM is a non-constraint here). Smaller/faster variants exist: `mlx-community/chatterbox-turbo-{8bit,6bit,4bit}` at some quality cost. ⚠️ **Must be a pre-converted `mlx-community` repo** (has `config.json` + merged `model.safetensors`). The raw ResembleAI weights have no `config.json` and **will not load** (`FileNotFoundError: Config not found`).

**TTS chunk granularity** → `STREAMING_INTERVAL` (`tts ~L124`, default `2.0` s). Each `GenerationResult` ≈ this many seconds of audio → one `TTSAudioRawFrame`. Lower it for slightly lower time-to-first-audio at the cost of more Metal kernel launches per utterance; raise it for fewer, larger frames. <!-- manual-lint: allow: GPU/OS kernel, the technical sense -->

**Output sample rate** → `SAMPLE_RATE` (`tts ~L121`, `24000`). Chatterbox-Turbo's native rate; `bot.py` imports it for `audio_out_sample_rate`. Don't change unless the engine's rate changes.

**Whole-reply vs per-sentence** → the pipeline uses pipecat's **default SENTENCE aggregation**; `run_tts` fires on the first sentence. There is no TTS floor to buffer around — do not install a text aggregator on `MLXAudioTTSService` (doing so would only add latency).

**Threading (do not touch)** → every MLX call runs on the service's single-worker `ThreadPoolExecutor` (Metal streams are thread-local). If you extend the service, keep all MLX work on that executor; never `asyncio.to_thread` a `generate()`. Why: [debugging/tts-audio-cases.md](../debugging/tts-audio-cases.md) (Case study 1).

**TTS prosody — four live knobs, plus per-tag profiles.** Turbo honors four synthesis knobs: `temperature`, `top_p`, `top_k`, and `repetition_penalty`. (It **ignores** `cfg_weight`/`exaggeration` — source-verified; those belong to *proper* Chatterbox, a different engine.) You do **not** hand-edit `generate()`; the knobs come from config files, in three layers:
- **Baseline** → `config/tts/<engine>/tts.toml` `[live]` (for the shipped engine, `config/tts/chatterbox-turbo/tts.toml`). Every value there equals the engine's own default — `temperature 0.8`, `top_p 0.95`, `top_k 1000`, `repetition_penalty 1.2` — a machine-checked no-op guarantee, so seeding from the file behaves exactly like passing no synth kwargs. Lower `temperature` to `0.7`/`0.6` **here** for steadier intonation (fewer rising/fry tones before `…` pauses) at some expressiveness cost. A baseline edit needs a **restart** (all temps are latency-identical, TTFA ~0.42 s); a data folder copy of the file replaces the shipped one whole.
- **Live layer** → `config/overrides.toml [tts]`, written by the **:65000 panel** and polled **every turn boundary** (no restart). It overlays the baseline — delete a key and it reverts. Don't hand-edit it; the panel owns it. Per-voice presets save/load through the panel's **profile system** (`characters/<c>/voices/<v>/profile.toml`), with `characters/<c>/profile.toml` holding the per-character `[llm]` tier.
- **Per-tag profiles** → `[tag_profiles.<tag>]` in that same `tts.toml`: a calibrated knob delta laid over the **one** synthesis chunk whose text carries a canonical paralinguistic tag, after which the baseline resumes (`mlx_tts_service.py` applies them on the desk path; `serve/tts_prep.py` mirrors it on Hearth). Shipped entries nudge `temperature` to `1.2` for `[crying]`/`[happy]`/`[surprised]`/`[fear]`/`[angry]`/`[sarcastic]`; `[whispering]`/`[dramatic]` ride the baseline by design. Values are **ear-calibrated** (ceiling `temperature 1.4`, loader-clamped) — change them by listening, not by reasoning about the numbers.

Separately, `run_tts` **auto-cleans** three artifact sources — always on, nothing to configure: (1) collapses dot-runs → a single `…`; (2) skips word-less fragments; and (3) **repairs malformed paralinguistic cue tags, then strips every bracketed non-cue** via `paralinguistics.normalize_with_report()` (`tts ~L357`, after the whitespace/ellipsis pass). That third pass rewrites a *bare* cue root in any of `*…*`/`(…)`/`[…]`/`{…}` to the canonical `[tag]` (case/padding/morphology-tolerant — `*sigh*`, `(SIGHS)`, `{sighing}` → `[sigh]`) for the nine cues, plus one curated multi-word entry (`[soft sigh]` → `[sigh]`) — and then, as a **CATCH-ALL** (since `2a36b05`), removes *every* remaining `[bracketed]` token that isn't one of the nine (`[softly]`, `[warmly]`, `[gentle smile]`, `[gentle pause]`, `[soft chuckle]`). Post-repair, a surviving bracketed token can only be something Chatterbox would **speak aloud**, which is always a bug. Still **square-brackets only**, so `*gentle smile*` / `(gentle pause)` RP survives. It is idempotent, no-ops on already-canonical tags, and never touches prose or placement (`test_paralinguistics.py`, 159/159). Every strip is logged to **`logs/paralinguistic-strips.jsonl`** (gitignored), classified `known` vs **UNKNOWN** — the UNKNOWNs are how you find a tag the model wants but the engine can't perform; that log is the discovery surface, not a knob. Favour `…` over ASCII `...` for pauses. *(How those were diagnosed: [debugging/tts-audio-cases.md](../debugging/tts-audio-cases.md) (Case studies 4, 10).)*

**Capture live TTS for debugging** → `bot.py --dump-tts [--dump-dir DIR]` (off by default, zero overhead when off; writes per-utterance WAVs + a `manifest.tsv`). A diagnostic, not a tuning knob — full usage in [debugging/tts-audio-cases.md](../debugging/tts-audio-cases.md) (Case study 4).
