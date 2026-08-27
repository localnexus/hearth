#!/usr/bin/env bash
# start.sh — bring the Hearth voice loop online (preflight + launch). Mirrors the runbook launch steps.
#   ./start.sh          run preflight, then launch the bot in the FOREGROUND (Ctrl-C or ./stop.sh to stop)
#   ./start.sh --check  run preflight ONLY and exit (are we ready to launch?) — does not touch the mic
#
# Run it in a terminal window — mic permission (macOS TCC) is granted to the terminal app, not to python.
# Preflight auto-syncs to whatever model the bot targets — resolved from config
#   (config/active.toml → config/models/<model>/model.toml → .id), the same source bot.py uses.
#   This logic is model-agnostic.
# NOTE: a hybrid thinking model runs with thinking disabled via the LM Studio Prompt-Template
#   edit ({%- set enable_thinking = false %}) — a persistent LM Studio setting (model.toml.needs_template_edit
#   flags it), not something this script sets. On recent runtimes the per-request
#   reasoning_effort:"none" alone suffices; the edit is kept as belt-and-suspenders.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
VENV_PY="$DIR/.venv/bin/python"
TOKEN_FILE="$HOME/.lmstudio/lm-probe-token"
BASE_URL="http://127.0.0.1:1234/v1"
cd "$DIR"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

CHECK_ONLY=0
BOT_ARGS=()   # forwarded to bot.py (session continuity: --resume / --new)
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

# --- tooling present ---
[ -x "$UV" ]      || fail "uv not found at '$UV' (override with UV=/path/to/uv)"
[ -x "$VENV_PY" ] || fail ".venv missing at '$VENV_PY' — build it with 'uv sync' (see the runbook dependencies step)"

# --- token ---
[ -r "$TOKEN_FILE" ] || fail "token file missing: $TOKEN_FILE (recreate it — see the runbook)"
TOKEN="$(cat "$TOKEN_FILE")"

# --- which model does the bot target? resolve it from config the same way bot.py
#     does (config/active.toml → config/models/<model>/model.toml → .id) ---
MODEL="$("$VENV_PY" -c 'import hearth.config.config_loader as c; print(c.load_model(c.load_active_selection()["model"])["id"])' 2>/dev/null || true)"
[ -n "$MODEL" ] || fail "could not resolve the active model from config (config/active.toml + config/models/<model>/model.toml) — see config-manual/llm.md"

# --- 1. LM Studio up + THAT model loaded ---
LOADED="$(curl -s -m4 "$BASE_URL/models" -H "Authorization: Bearer $TOKEN" \
  | "$VENV_PY" -c 'import sys,json;print("\n".join(m["id"] for m in json.load(sys.stdin).get("data",[])))' 2>/dev/null || true)"
[ -n "$LOADED" ] || fail "LM Studio not reachable at $BASE_URL — is it running, and is the token valid?"
if printf '%s\n' "$LOADED" | grep -qxF "$MODEL"; then
  ok "LM Studio: '$MODEL' loaded"
else
  say "  \033[31m✗\033[0m LM Studio reachable, but '$MODEL' is NOT loaded. Currently loaded:" >&2
  printf '%s\n' "$LOADED" | sed 's/^/        /' >&2
  fail "load '$MODEL' in LM Studio (a hybrid thinking model also needs the persistent Prompt-Template thinking-off edit), or set the model in config/active.toml + config/models/<model>/model.toml to a loaded id"
fi

# --- 2. no stale bot holding the mic ---
# Pattern matches the worker regardless of interpreter name ("python3 bot.py"),
# not just the transient uv wrapper — see stop.sh for the full rationale.
if pgrep -f "python[0-9.]* -m hearth\.pipeline\.bot" >/dev/null 2>&1; then
  fail "a bot is already running. Stop it first: ./stop.sh"
fi
ok "no stale bot"

# --- 3. valid default mic AND speaker (both must resolve — key after a Bluetooth switch) ---
AUDIO="$("$VENV_PY" -c "import pyaudio;pa=pyaudio.PyAudio();print(pa.get_default_input_device_info()['name'],'/',pa.get_default_output_device_info()['name'])" 2>/dev/null || true)"
[ -n "$AUDIO" ] || fail "no valid default mic+speaker (Errno -9996 risk). Set both in System Settings → Sound; connect Bluetooth BEFORE launch (see the runbook)"
ok "audio in/out: $AUDIO"

if [ "$CHECK_ONLY" = "1" ]; then
  say ""
  ok "preflight PASSED — ready to launch (run ./start.sh with no args to go online)"
  exit 0
fi

say ""
say "Launching bot.py — speak first (no auto-greeting) · ~10–20 s to warm · Ctrl-C or ./stop.sh to stop."
say ""
exec env LM_API_TOKEN="$TOKEN" "$UV" run python -m hearth.pipeline.bot ${BOT_ARGS[@]+"${BOT_ARGS[@]}"}
