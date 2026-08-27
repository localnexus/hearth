# 6. One-glance cheat

*What this covers: the whole drill on one screen — the `start.sh`/`stop.sh` wrappers and the manual online/offline equivalents.* · Runbook index: [`README.md`](README.md)

**Scripts wrap this drill** (`start.sh` = preflight §1 + launch §2; `stop.sh` = §3). Run `start.sh` in an **iTerm** window (mic grant):
```
./start.sh          preflight, then launch (foreground). Checks the server advertises the
                    selected model (model/prompt/voice come from config/ — see §1).
                    LM_BASE_URL / LM_API_TOKEN / LM_PROVIDER env override the llama-server default.
./start.sh --check  preflight ONLY — "am I ready?" — does not touch the mic
./stop.sh           stop the loop from any shell (your LLM server is left running)
```

Manual equivalent:
```
ONLINE   preflight (§1) → cd <the tree> → .venv/bin/python -m hearth.pipeline.bot
         → wait "pipeline is now ready" (~10-20 s) → speak first → hear a reply (~2-3 s warm)

OFFLINE  Ctrl-C  (or  pkill -f "python[0-9.]* -m hearth\.pipeline\.bot")   → mic released, in-process STT+TTS freed
         full teardown: also stop the LLM server (§4).  No separate TTS app to quit.
```
