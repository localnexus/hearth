# TTS / audio case studies (1–10)

## NEW Case study 1 — `There is no Stream(gpu, N) in current thread`

**Symptom:** the moment TTS synthesizes, a hard crash: `std::runtime_error: There is no Stream(gpu, N) in current thread` (or the pipeline dies as soon as `run_tts` runs).

**Cause:** **MLX Metal GPU streams are thread-local.** A stream created (or first used) on the thread that loaded the model does **not** exist on any other thread; any MLX op on the wrong thread aborts. This bites the obvious async fix — wrapping the synchronous `generate()` in `asyncio.to_thread(...)` runs it on an *arbitrary* pool thread with no stream → crash. (Also tried and still crashing: `mx.stream(mx.gpu)` in the worker, and passing `mx.default_stream(mx.gpu)` across threads — the stream is bound to the loader thread, not transferable.)

**Fix (already in `mlx_tts_service.py`):** a **single-worker `ThreadPoolExecutor(max_workers=1)`** that owns *both* the model load *and* every `generate()` call. All MLX work lands on that one OS thread, so the stream context is consistent; audio chunks cross back to the event loop via an `asyncio.Queue` (`loop.call_soon_threadsafe`).

**If it recurs after edits:** you almost certainly added an MLX call (or a model load) that runs somewhere other than that executor thread. Rule: **every MLX op for the TTS engine must go through the service's single-worker executor** — never `asyncio.to_thread`, never a bare thread, never the event loop directly. Confirm with Play 1 (`sample`): a healthy synth shows the MLX frames on the `mlx-tts` worker thread, and the event-loop thread parked in `select`/`kevent` (not blocked in `eval_gpu`).

## NEW Case study 2 — slow first synth: compile, not regression

**Symptom:** the first synth after a fresh machine boot takes **~15.8 s** (vs ~1–2 s), RTF looks terrible.

**Diagnosis:** one-time **MLX Metal-kernel JIT compile**, not a latency regression. The kernel cache persists on disk; every subsequent run/process is warm. **Always measure warm (2nd+ run).** Don't "fix" a cold number — you'll chase a ghost (this is the same class as the Whisper cold-load). An always-on appliance pays it once; only a cold boot's first turn eats it. Relevant to airgap testing. <!-- manual-lint: allow: GPU/OS kernel, the technical sense -->

> Corollary of `import-clean ≠ runtime-correct`: **cold ≠ warm.** Report the steady-state number, and label a one-time cost as one-time.

## NEW Case study 3 — barge-in feels laggy on a long reply

**Symptom:** you speak over a long companion reply; the audio stops, but the *next* turn seems to wait a beat.

**Not a bug — a known limit.** `run_tts`'s `synth_future.cancel()` is best-effort: `concurrent.futures.Future.cancel()` cannot interrupt an **already-running** job, so the in-flight `generate()` finishes on the single executor thread before the next synth starts. pipecat still cancels the model stream and drops the queued `TTSAudioRawFrame`s, so the *audio* stops promptly — but the executor is busy for the tail of the current sentence (≤ ~1.3 s at RTF 0.24). The tighter fix is a **cooperative stop-flag** checked inside the generate loop. To confirm this is what you're seeing (not a wedge): Play 6 shows the interruption fired and the model cancelled; Play 1 shows the `mlx-tts` thread finishing one `generate()` then going idle.

## Case study 4 — filler/non-word sounds between phrases (SOLVED)

**Symptom:** filler/non-word sounds *between phrases* — worst on counting/list output; occasional off intonation (rising/fry).

**Root cause + fix:** `match_endofsentence` (NLTK) split an **ASCII `...`** into a word + a **word-less lone `.`** fragment → Chatterbox improvised ~1 s of filler. Fixed in `run_tts`: `re.sub(r"[.…]{2,}", "…", text)` then skip if `not any(ch.isalnum())`. Live-confirmed clean.

**⚠️ On Turbo, `cfg_weight`/`exaggeration` are IGNORED** — only knobs are `temperature` (0.8) + `repetition_penalty` (1.2). Capture live output with `bot.py --dump-tts` (per-utterance WAVs + `manifest.tsv`) to inspect prosody.

## Case study 5 — "clipping" that isn't: check the player's volume

**Symptom:** a render "sounds loud / like it's clipping" by ear.

**Often NOT the audio.** Double-clicking a WAV opens **Music.app**, which has its **own** volume (can sit at max) — that reads as clipping regardless of the file. Measure before believing it: 0 samples at ±full-scale = no digital clipping. Two renders flagged as "clipping" measured **0 clipped samples** (one was even *quieter* than its reference). **Audition with `afplay <file>` in the terminal** (respects system volume), not double-click. Real clipping shows as samples pinned at ±32767 and raw-float peak ≥ 1.0.

## Case study 6 — verify a TTS render is actually speech (Whisper)

**Symptom:** a new/converted TTS path emits audio — but is it speech or garbage? Hard to tell fast by ear, impossible in a headless run.

**STT the output.** `mlx_whisper.transcribe(audio_float, path_or_hf_repo="mlx-community/whisper-large-v3-turbo")` — reads back the words → speech; returns junk/hallucination (e.g. *"888 888 Thank you"*) → the engine is broken. This objectively caught a bad full-Chatterbox conversion (multilingual weights + English tokenizer → tones). A broken engine still produces audio of the *right length*, so length/RTF alone won't tell you.

## Case study 7 — companion goes silent after you speak: hybrid model is thinking

> **Applicability:** a **hybrid-thinking** model needs thinking forced off. This case is exactly what happens when whatever forces it off stops applying — a chat template that ignores `reasoning_effort`, or (on LM Studio) a lost Prompt-Template edit. A natively non-thinking model is immune; this applies to any hybrid/reasoning-first model you load.

**Symptom:** STT + model fire (logs show the turn), but the companion says nothing — `content` stays empty.

**Cause:** the model is emitting **chain-of-thought** — it streams `reasoning_content` / a `<think>` block and leaves spoken `content` empty until it finishes. A **hybrid** model defaults to thinking **ON**; a reasoning-first model always does. Either starves the TTS.

**Fix:** force thinking off. Hearth already sends **`extra={"reasoning_effort": ...}`** (from the active `model.toml`, into `bot.py`'s model `Settings`) on every request, so start there: set `reasoning_effort = "none"`.

On **`llama-server`** (the default) that request field is the documented lever — upstream states *"`reasoning_effort`: If `none`, reasoning/thinking is disabled."* If your model's template ignores it, restart the server with a thinking-off switch instead: `-rea, --reasoning off`, `--reasoning-budget 0`, or `--chat-template-kwargs '{"enable_thinking": false}'` (a stubborn template can be replaced with `--chat-template-file`). Confirm the switch names against your build's `llama-server --help` — see [config-manual/llm.md](../config-manual/llm.md).

> **If you use LM Studio instead:** on the builds this was validated against, `reasoning_effort` in the request body is the **only** field that reaches the jinja `enable_thinking` var — `chat_template_kwargs` / top-level `enable_thinking` / `reasoning:{enabled:false}` are all **silently ignored**. **⚠️ Third-party uncensored re-quants ignore `reasoning_effort` too**; those need LM Studio's persistent **Prompt-Template** edit (`{%- set enable_thinking = false %}` as the template's first line) — the LM-Studio-specific requirement `model.toml`'s `needs_template_edit` records.

Verify either way with a streaming curl that `content` is non-empty and no `<think>` appears.

## Case study 8 — companion starts, pipeline "ready", but audio dies (`Errno -9996`)

**Symptom:** launch succeeds, `PipelineWorker ... ready`, but `LocalAudioInputTransport` throws `[Errno -9996] Invalid input device (no default output device)` and the companion can't hear or speak.

**Cause:** the transport pins **no device** — it grabs the macOS **default** mic+speaker **at startup**, and PortAudio does **not** follow default-device changes mid-run. Two triggers: (a) started **mid-Bluetooth handoff**, when macOS momentarily has no stable default; (b) the connected device is **output-only** (A2DP earbuds with no mic → *no default input device at all*).

**Fix:** ensure a valid default **input AND output** before launch, then start. To switch Bluetooth devices, set the new default (System Settings → Sound) and **restart** — the stream re-opens on the new default; it will not migrate live. Pre-flight check:
```bash
.venv/bin/python -c "import pyaudio;pa=pyaudio.PyAudio();print(pa.get_default_input_device_info()['name'],'/',pa.get_default_output_device_info()['name'])"
```
**⚠️** A BT earpiece used as the **mic** forces the low-fi **HFP** profile (mono ~16 kHz, both directions). Pinning explicit `input_device_index`/`output_device_index` avoids this.

## Case study 9 — TTS import dies: `'str' object has no attribute '__module__'` (the transformers pin)

**Symptom:** importing/loading the TTS engine aborts with `AttributeError: 'str' object has no attribute '__module__'` — thrown the moment `mlx_audio.tts.models.chatterbox_turbo` imports. The whole TTS stack fails to load; nothing synthesises.

**Root cause (the version archaeology):** `mlx-audio` pulls in `mlx-lm` (origin **0.31.3**), whose `tokenizer_utils` runs, *at import time*, `AutoTokenizer.register("NewlineTokenizer", fast_tokenizer_class=NewlineTokenizer)` — passing a **string** as the first arg. **transformers ≥ 5.13** changed `register()` to require a config **class** and does `key.__module__` on it → the `AttributeError`. `mlx-audio` floors `transformers>=5.5.0` with **no upper bound**, so an unpinned install resolves to the newest 5.x and breaks. `mlx-lm` 0.31.3 is already the latest, so you **cannot** fix it by upgrading mlx-lm.

**Fix:** **pin `transformers==5.5.0`** — it predates the breaking `register()` change and works. `uv add 'transformers==5.5.0'`. Verify the pin took *and* the engine imports:
```bash
uv run python - <<'PY'
import importlib.metadata as m
print("transformers", m.version("transformers"))   # must print 5.5.0
import mlx_lm
from mlx_lm.models.cache import KVCache               # triggers the crash if unpinned
from mlx_audio.tts.utils import load_model
print("mlx-audio import OK")
PY
```
If you still see the `__module__` error, the pin didn't take. **Do not "solve" this by upgrading** — there is no newer transformers that works with mlx-lm 0.31.3; 5.5.0 is the ceiling. It's the single most fragile dep in the stack (see the [config manual](../config-manual/README.md) golden rules).

## Case study 10 — the companion SPEAKS a bracketed word or `*action*` aloud (cue/stage-direction leak)

**Symptom:** the companion says the literal word out loud — *"sigh"*, *"laugh"*, or a whole action like *"pulls you into a tight hug"* — instead of *performing* it.

**Two distinct causes — separate them by what leaked:**

1. **A whitelisted cue tag spoken instead of consumed.** The model emits nine cue tags — `[laugh] [chuckle] [sigh] [gasp] [groan] [sniff] [cough] [shush] [clear throat]` — which **only Chatterbox-Turbo consumes** as breath/sound (verified 9/9). **A deterministic repair layer backstops the FORM problems:** `paralinguistics.normalize_with_report()`, hooked in `run_tts` (`mlx_tts_service.py:364`, after the whitespace/ellipsis normalize), rewrites any *bare* cue root wrapped in `*…*` / `(…)` / `[…]` / `{…}` to the canonical `[tag]`, tolerating case (`[SIGH]`), padding, and morphology (`[sighs]`, `(sighing)`) — so a fumbled-wrapper or morphed cue no longer reaches TTS malformed. It does **not** touch placement or unwrapped prose. **An off-whitelist BRACKETED tag can no longer be spoken at all** — a catch-all strips every `[…]` token that isn't one of the nine (`[whisper]`, `[warmly]`, `[softly]` are removed, not voiced; this closed a real defect — they *were* spoken aloud before). So if a cue is *spoken* now, the remaining causes are: a **non-Turbo TTS** in the slot; a **placement/stacking** violation the repair leaves alone (`[sigh][sigh]`, jammed mid-word — prompt-side rules exist to prevent these, but sampling can still violate); or a leak in a **non-square** wrapper (cause 2 below). **Confirm the engine:** `MODEL_REPO` in `mlx_tts_service.py` must be `mlx-community/chatterbox-turbo-fp16`. **Confirm the tag shape:** lowercase, one-per-bracket, boundary before it. A *buried* tag doesn't get spoken — it degrades to a subdued breath or drops (Result 2), which is a different symptom. **If a bracketed word vanished when you expected a sound**, that's the strip doing its job: check `logs/paralinguistic-strips.jsonl` — an `UNKNOWN` entry names the tag the model reached for and is the evidence for curating a mapping.

2. **An `*asterisk*` / `(paren)` stage direction spoken verbatim — a separable defect, NOT the cues block.** The persona's emote pull can leak `*pulls you into a tight hug*`-style prose **~1/8 turns**, read as normal speech (asterisks silent). This is exactly the failure the "feeling lives in the WORDS, never in stage directions" hard rule (`system-prompt-template.md`) exists to prevent. It is **independent of the cues capability** — the cues block doesn't cause it and doesn't fix it. **The repair layer does NOT sanitize this, by design.** `paralinguistics.py` has one destructive op — the strip — and it is a **catch-all**: *every* bracketed non-cue goes, not just an older, narrower whitelist of tags. But it remains **square-bracket ONLY**, and that boundary is the whole point here: `[…]` is the cue-tag syntax, so a non-cue there is a fumbled tag and always a bug; `*gentle smile*` / `(gentle pause)` may be *desired* stage-direction and survive untouched, left for a voice-native-action layer. So `*pulls you into a hug*` is never stripped — the bracket side does not extend to asterisks or parens. This leak is therefore still **not code-sanitized**; the fix path is a voice-native action style-guide in the persona prose, not a TTS setting. **Diagnostic tell:** a bracketed leak would appear in `logs/paralinguistic-strips.jsonl`; an `*asterisk*` leak never will — that log only sees brackets. Silence there while you're still hearing stage directions by ear confirms you're in cause 2, not cause 1.
