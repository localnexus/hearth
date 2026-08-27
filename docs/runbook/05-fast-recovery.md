# 5. Fast recovery (runtime symptom → fix)

*What this covers: the runtime faults you can hit while the loop is running and the one action that clears each — plus pointers to where deeper (build-time / post-edit) failures are handled.* · Runbook index: [`README.md`](README.md)

Faults you can hit **while running**, and the one action that clears each. Anything deeper is out of scope for the drill: **build-time** import crashes (transformers pin, raw-weights `Config not found`) and **post-code-edit** regressions (`There is no Stream(gpu, N)`) plus **barge-in internals** → see the [debugging](../debugging/README.md) notes; running **airgapped** (`HF_HUB_OFFLINE=1`) → [config-manual/misc.md](../config-manual/misc.md).

| Symptom | Cause | Fix |
|---|---|---|
| Speaks nothing; log shows `Generating chat` then hangs | LLM is emitting chain-of-thought (`content` stays empty → TTS starved) | A hybrid-thinking model needs thinking **off** — confirm the persistent LM Studio Prompt-Template edit (`{%- set enable_thinking = false %}`) is still applied for an uncensored re-quant that ignores `reasoning_effort`; otherwise `reasoning_effort = "none"` in the active `model.toml` handles it. A natively-non-thinking / pure-instruct model needs neither. Never a reasoning-first model. |
| Bot ready, but no audio / `[Errno -9996] Invalid input device` | transport grabbed an invalid default device (started mid-**Bluetooth** switch, or output-only earbuds with no mic) | ensure a valid default **mic + speaker** (§1 check 3), then relaunch — devices are grabbed at startup and won't migrate live |
| STT never transcribes; OS mic meter *does* move | iTerm lacks mic permission (macOS TCC) | System Settings → Privacy & Security → Microphone → enable **iTerm**, relaunch. |
| `model_not_found` in LM logs | wrong model id | fix `id` in the active `config/models/<model>/model.toml` to the id LM Studio reports **verbatim** (live model dir chosen by `model =` in `config/active.toml`), then restart |
| `401` / auth error to `:1234` | bad/expired token | refresh `~/.lmstudio/lm-probe-token`, relaunch |
| Two mic-using apps fight / no input | a stale `bot.py` still running | `pkill -f "python[0-9.]* bot\.py"`, then relaunch |
| Set a character/voice in `config/active.toml` but a **different (previously-auditioned) voice** plays | a sticky `[voice] ref_wav` left in `config/overrides.toml` — the control panel writes it when you audition a voice live, and that live layer **overrides `active.toml`'s voice every turn** (`desired = baseline ⊗ overrides`) | remove the `[voice]` section from `config/overrides.toml` (or blank the file / POST the knob null via the panel), then **restart** — `active.toml` is read only at startup, so its selection becomes the baseline (and the override reverts) only on relaunch. First place to look when a voice pick "won't take." |
