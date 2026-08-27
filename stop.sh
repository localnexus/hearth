#!/usr/bin/env bash
# stop.sh — bring the Hearth voice loop offline. Mirrors the runbook stop steps.
# Stops bot.py → releases the mic + frees the in-process STT + TTS weights.
# LM Studio KEEPS RUNNING (that's intentional). Full teardown / reclaim its memory = eject
# the model in LM Studio (see the runbook); this script never touches LM Studio.
#
# Session continuity (Tier 1):
#   ./stop.sh                     stop; the session is EPHEMERAL → its transcript is truly deleted
#   ./stop.sh --hold [name]       stop, but KEEP this session (held class: sticky, purge-exempt,
#                                 optionally named for `--resume <name>`)
#   ./stop.sh --discard-held <name>         true-delete ONE held session (targeted, immediate)
#   ./stop.sh --discard-held [--all]         true-delete ALL held — irreversible; requires typing HEARTH
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$DIR/.venv/bin/python"
# Matches the worker whatever the interpreter is named. The bot runs as
# ".../python3 bot.py" (uv's child); a bare "python bot.py" pattern only matches
# the transient uv wrapper and MISSES the worker once uv exits → stop silently
# no-ops on a live bot. [0-9.]* covers python / python3 / python3.12.
PATTERN="python[0-9.]* -m hearth\.pipeline\.bot"

MODE="stop"; HOLD_NAME=""; DISCARD_ARG="--all"
case "${1:-}" in
  --hold)          MODE="hold";    HOLD_NAME="${2:-}" ;;
  --discard-held)  MODE="discard"; DISCARD_ARG="${2:---all}" ;;
  "")              ;;
  *) printf "unknown arg '%s'. Usage: ./stop.sh [--hold [name]] [--discard-held [name|--all]]\n" "$1" >&2; exit 1 ;;
esac

# --discard-held is a maintenance verb: it acts on held files, not the running bot.
# Guard: refuse while a bot is live — it would re-create the file on its next per-turn
# snapshot, so the delete silently "comes back" (a false success, caught on-hardware).
if [ "$MODE" = "discard" ]; then
  if pgrep -f "$PATTERN" >/dev/null 2>&1; then
    printf '  \033[31m✗\033[0m a bot is running — a discard would be undone by its next snapshot.\n' >&2
    printf '    Stop it first (plain ./stop.sh keeps held sessions), then discard:\n' >&2
    printf '      ./stop.sh && ./stop.sh --discard-held %s\n' "$DISCARD_ARG" >&2
    exit 1
  fi
  "$VENV_PY" "$DIR/session_store.py" discard-held ${DISCARD_ARG:+"$DISCARD_ARG"}
  exit $?
fi

if ! pgrep -f "$PATTERN" >/dev/null 2>&1; then
  if [ "$MODE" = "hold" ]; then
    # No bot running: promote the newest ephemeral ORPHAN to held directly.
    "$VENV_PY" "$DIR/session_store.py" hold ${HOLD_NAME:+"$HOLD_NAME"}
    exit $?
  fi
  printf 'No bot running — nothing to stop.\n'
  exit 0
fi

# --hold with a live bot: mark the stop-time intent BEFORE signaling, so the bot's
# shutdown `finally` sees the marker and keeps (promotes) its session instead of
# deleting it. Then fall through to the normal graceful stop below.
if [ "$MODE" = "hold" ]; then
  "$VENV_PY" "$DIR/session_store.py" request-hold ${HOLD_NAME:+"$HOLD_NAME"}
fi

printf 'Stopping bot.py (SIGINT → graceful shutdown) …\n'
# SIGINT, not SIGTERM: the WorkerRunner handles SIGINT (handle_sigint=True) but NOT
# SIGTERM (handle_sigterm=False), so only SIGINT runs bot.py's finally — which prints
# the TokenMeter shutdown summary (it appears in the WINDOW RUNNING THE BOT, not here).
pkill -INT -f "$PATTERN" || true

# graceful teardown + summary can take a few seconds — wait up to ~6s
for _ in $(seq 1 20); do
  pgrep -f "$PATTERN" >/dev/null 2>&1 || break
  sleep 0.3
done

# still alive? escalate: SIGTERM, then SIGKILL
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  printf '  … still running — escalating (SIGTERM)\n'
  pkill -TERM -f "$PATTERN" || true
  sleep 1
fi
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  printf '  … still running — SIGKILL\n'
  pkill -9 -f "$PATTERN" || true
  sleep 0.5
fi

if pgrep -f "$PATTERN" >/dev/null 2>&1; then
  printf '  \033[31m✗\033[0m could not stop it. Inspect: pgrep -af "%s"\n' "$PATTERN" >&2
  exit 1
fi

printf '  \033[32m✓\033[0m stopped — mic released, in-process STT+TTS freed. LM Studio still running.\n'
