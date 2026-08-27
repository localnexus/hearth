#!/usr/bin/env bash
# start.sh — bring the Hearth voice loop online (preflight + launch).
#   ./start.sh          run preflight, then launch the bot in the FOREGROUND (Ctrl-C or ./stop.sh to stop)
#   ./start.sh --check  run preflight ONLY and exit (are we ready to launch?) — does not touch the mic
#   ./start.sh --resume [file|name] · --new    session continuity (forwarded to the bot)
#
# Run it in a terminal window — mic permission (macOS TCC) is granted to the terminal app, not to python.
#
# The LLM is an OpenAI-compatible server YOU run. Default = llama-server (llama.cpp) on its
# default port. Env overrides (also read by the bot itself):
#   LM_BASE_URL    default http://127.0.0.1:8080/v1   (LM Studio: http://127.0.0.1:1234/v1)
#   LM_API_TOKEN   bearer key, only if your server requires one (llama-server --api-key)
#   LM_PROVIDER    control-panel engine probe: llama-server (default) | lmstudio
#   HEARTH_DATA    where your companions/selections/sessions live (default: this checkout)
#   HEARTH_ROOT    the engine tree (only needed for a non-editable install)
# Preflight resolves the model id the bot will request the same way the bot does
# (config/active.toml → config/models/<model>/model.toml → .id) and checks the server
# advertises it. llama-server serves whatever it was launched with regardless of the id
# in the request, so a mismatch is a WARNING there; LM Studio needs an exact match.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${VENV_PY:-$DIR/.venv/bin/python}"
BASE_URL="${LM_BASE_URL:-http://127.0.0.1:8080/v1}"
TOKEN="${LM_API_TOKEN:-}"
PROVIDER="${LM_PROVIDER:-llama-server}"
cd "$DIR"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

CHECK_ONLY=0
BOT_ARGS=()   # forwarded to the bot (session continuity: --resume / --new)
while [ $# -gt 0 ]; do
  case "$1" in
    --check|-n) CHECK_ONLY=1; shift ;;
    --new) BOT_ARGS+=("--new"); shift ;;
    --resume)
      BOT_ARGS+=("--resume")
      # optional value: a following token that is NOT itself a flag
      if [ $# -ge 2 ] && [ "${2#-}" = "$2" ]; then BOT_ARGS+=("$2"); shift 2; else shift; fi
      ;;
    *) fail "unknown arg '${1}'. Usage: ./start.sh [--check] [--resume [file|name]] [--new]" ;;
  esac
done

say "Preflight — Hearth voice loop"

# --- venv present, with Hearth installed in it ---
[ -x "$VENV_PY" ] || fail ".venv missing at '$VENV_PY' — create it: uv venv -p 3.12 && uv pip install -e \".[mac]\"  (see README)"
"$VENV_PY" -c 'import hearth' 2>/dev/null || fail "hearth is not installed in $VENV_PY — run: uv pip install -e \".[mac]\""

# --- where things live: the engine tree and the data root (HEARTH_ROOT / HEARTH_DATA) ---
ROOTS="$("$VENV_PY" -c 'import hearth.config.config_loader as c; print(c._ROOT); print(c.DATA_DIR)' 2>&1)" \
  || fail "$ROOTS"
ok "engine tree: $(printf '%s\n' "$ROOTS" | sed -n 1p)"
ok "data root:   $(printf '%s\n' "$ROOTS" | sed -n 2p)   (companions, sessions, selections — set HEARTH_DATA to relocate)"

# --- which model does the bot request? resolve it from config the same way the bot does ---
MODEL="$("$VENV_PY" -c 'import hearth.config.config_loader as c; print(c.load_model(c.load_active_selection()["model"])["id"])' 2>/dev/null || true)"
[ -n "$MODEL" ] || fail "could not resolve the active model from config — copy config/active.toml.example to config/active.toml and set config/models/<model>/model.toml (see docs/the-config-layers.md)"

# --- 1. LLM server up, and what it advertises ---
AUTH=()
[ -n "$TOKEN" ] && AUTH=(-H "Authorization: Bearer $TOKEN")
LOADED="$(curl -s -m4 "$BASE_URL/models" ${AUTH[@]+"${AUTH[@]}"} \
  | "$VENV_PY" -c 'import sys,json;print("\n".join(m["id"] for m in json.load(sys.stdin).get("data",[])))' 2>/dev/null || true)"
[ -n "$LOADED" ] || fail "no OpenAI-compatible server answering at $BASE_URL — start llama-server (or set LM_BASE_URL / LM_API_TOKEN for the server you run)"
if printf '%s\n' "$LOADED" | grep -qxF "$MODEL"; then
  ok "LLM server at $BASE_URL advertises '$MODEL'"
elif [ "$PROVIDER" = "llama-server" ]; then
  warn "server is up but does not advertise '$MODEL' (it serves: $(printf '%s\n' "$LOADED" | head -6 | tr '\n' ' ')$( [ "$(printf '%s\n' "$LOADED" | wc -l)" -gt 6 ] && printf '…' )). llama-server answers with its loaded model regardless — fine, but set model.toml .id to match to keep the panel honest"
else
  printf '  \033[31m✗\033[0m %s\n' "server reachable, but '$MODEL' is NOT loaded. Currently loaded:" >&2
  printf '%s\n' "$LOADED" | sed 's/^/        /' >&2
  fail "load '$MODEL' in your server, or set config/active.toml + config/models/<model>/model.toml to a loaded id"
fi

# --- 2. no stale bot holding the mic ---
if pgrep -f "python[0-9.]* -m hearth\.pipeline\.bot" >/dev/null 2>&1; then
  fail "a bot is already running. Stop it first: ./stop.sh"
fi
ok "no stale bot"

# --- 3. valid default mic AND speaker (both must resolve — key after a Bluetooth switch) ---
AUDIO="$("$VENV_PY" -c "import pyaudio;pa=pyaudio.PyAudio();print(pa.get_default_input_device_info()['name'],'/',pa.get_default_output_device_info()['name'])" 2>/dev/null || true)"
[ -n "$AUDIO" ] || fail "no valid default mic+speaker (Errno -9996 risk). Set both in System Settings → Sound; connect Bluetooth BEFORE launch"
ok "audio in/out: $AUDIO"

if [ "$CHECK_ONLY" = "1" ]; then
  say ""
  ok "preflight PASSED — ready to launch (run ./start.sh with no args to go online)"
  exit 0
fi

say ""
say "Launching — speak first (no auto-greeting) · ~10–20 s to warm · Ctrl-C or ./stop.sh to stop."
say ""
exec env LM_BASE_URL="$BASE_URL" LM_PROVIDER="$PROVIDER" ${TOKEN:+LM_API_TOKEN="$TOKEN"} \
  "$VENV_PY" -m hearth.pipeline.bot ${BOT_ARGS[@]+"${BOT_ARGS[@]}"}
