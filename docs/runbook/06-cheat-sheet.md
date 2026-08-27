# 6. One-glance cheat

*What this covers: the whole drill on one screen — the `start.sh`/`stop.sh` wrappers and the manual online/offline equivalents.* · Runbook index: [`README.md`](README.md)

**Scripts wrap this drill** (`start.sh` = preflight §1 + launch §2; `stop.sh` = §3). Run `start.sh` in an **iTerm** window (mic grant):
```
./start.sh          preflight, then launch (foreground). Checks the selected model is loaded.
                    (model/prompt/voice now come from config/ — see §1; ⚠️ start.sh's own model
                     auto-check still greps the old bot.py LM_MODEL literal → needs a code update;
                     §1's config-based check is the reliable one post-externalization.)
./start.sh --check  preflight ONLY — "am I ready?" — does not touch the mic
./stop.sh           stop the loop from any shell (LM Studio left running)
```

Manual equivalent:
```
ONLINE   preflight (§1) → cd <the tree> → LM_API_TOKEN=$(cat ~/.lmstudio/lm-probe-token) uv run python bot.py
         → wait "pipeline is now ready" (~10-20 s) → speak first → hear a reply (~2-3 s warm)

OFFLINE  Ctrl-C  (or  pkill -f "python[0-9.]* bot\.py")   → mic released, in-process STT+TTS freed
         full teardown: also eject model in LM Studio (§4).  No separate TTS app to quit.
```
