# 1. BRING ONLINE — preflight (run before launching)

*What this covers: the three copy-paste checks (LM Studio + model, no stale bot, valid default mic/speaker) that must pass before launch — and how to read/fix each.* · Runbook index: [`README.md`](README.md)

```bash
cd <the Hearth tree>          # no absolute path is assumed — or just run ./start.sh --check
TOKEN=$(cat ~/.lmstudio/lm-probe-token)

# 1. LM Studio up + the model the ACTIVE CONFIG selects, loaded (MODEL-AGNOSTIC — resolves the id
#    from config/active.toml → the chosen model.toml, so it never re-stales on a swap/revert).
M="$(grep -E '^model[[:space:]]*=' config/active.toml | head -1 | sed -E 's/^[^"]*"([^"]+)".*/\1/')"
export MODEL="$(grep -E '^id[[:space:]]*=' config/models/$M/model.toml | head -1 | sed -E 's/^[^"]*"([^"]+)".*/\1/')"
curl -s -m4 http://127.0.0.1:1234/v1/models -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json,os;d=json.load(sys.stdin);m=os.environ["MODEL"];print(f"LM ok, {m}:",any(x["id"]==m for x in d["data"]))'

# 2. No stale bot already holding the mic  (expect: no output)
pgrep -f "python[0-9.]* bot\.py"

# 3. A valid default mic AND speaker resolve (both must print a name — key after a Bluetooth switch)
.venv/bin/python -c "import pyaudio;pa=pyaudio.PyAudio();print('audio in/out:',pa.get_default_input_device_info()['name'],'/',pa.get_default_output_device_info()['name'])"
```

| Check | PASS looks like | If it FAILS |
|---|---|---|
| LM Studio | `LM ok, <model>: True` (the id ends in `: True`) | open LM Studio; load the id the active config names. A hybrid-thinking model must have thinking forced off (`reasoning_effort = "none"` in the model's `model.toml`, plus the persistent LM Studio Prompt-Template edit for uncensored re-quants that ignore it). Change the selection via `model =` in `config/active.toml` (then restart). Token file missing → recreate `~/.lmstudio/lm-probe-token`. |
| stale bot | *(nothing printed)* | a previous run is live — stop it (§3) before relaunching, or you'll contend on the mic. |
| **audio in/out** | prints a real **input AND output** name | either errors / wrong device → select it in System Settings → Sound. **Bluetooth: connect the earpiece *before* launch; to switch devices mid-session you must restart** — the stream grabs the default at startup and won't follow. An **output-only** device (A2DP earbuds, no mic) → no default input → `Errno -9996`. |
| mic (first run only) | — | if STT hears nothing later, verify the iTerm mic grant with the probe in §5. |

*(No separate-TTS-service check — TTS is in-process.)*
