#!/bin/bash
# compact-companion-session.sh — end-to-end LOCAL compaction of a held companion
# session (2026-09-02, from the first-local-compaction run made durable).
#
#   guard → stats gate → render → 122B continuity note (compact-note.py) →
#   mechanical checks → REVIEW GATE (default) → compact_session.py → report
#
# Flags, privacy posture and the facade contract: README.md, beside this file.
# In one line: the script prints PATHS AND COUNTS ONLY, never companion content.
set -euo pipefail
ORIG_ARGS=("$@")

# The queue-file lifecycle lives beside this script (extracted 2026-09-04):
# claim → success / .failed / defer / decline, and the JSON stamp under them.
# Resolved from THIS file's directory so the pair travels together.
HERE="$(cd "$(dirname "$0")" && pwd)"
for lib in compact-queue-lib.sh compact-model-lib.sh compact-model-llama.sh; do
  [ -f "$HERE/$lib" ] || {
    printf '%s missing beside this script (%s) — half-installed\n' "$lib" "$HERE" >&2
    exit 1
  }
  # shellcheck source=/dev/null
  . "$HERE/$lib"
done

DATA="${HEARTH_DATA:?HEARTH_DATA must name the data root}"
LIVEPY="${HEARTH_PYTHON:-python3}"  # the python that imports hearth (the facade passes its own)
TOOL="$HERE/compact_session.py"
TPL="$HERE/prompts/continuity-note.md"
BODIES="$DATA/ops/compaction/bodies"

# STEP/LAST_ERR: the breadcrumb a failed watch-claimed run leaves behind. The
# queue file is the only thing the launch page can render for a failure, and
# until 2026-09-04 it carried no reason at all — a run that died in its first
# second was indistinguishable, on screen, from one that never started.
STEP="" LAST_ERR=""
say(){ STEP="$*"; printf '%s\n' "$*"; }
ok(){ printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
bad(){ LAST_ERR="$*"; printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }
die(){ bad "$*"; exit 1; }

NAME="" LANE="v2" CHAR="" SDIR="" TAIL=12 BODY="" YES=0 NOTES="" REQUEST_FILE=""
MODEL_KEY="qwen3.5-122b-a10b-mlx"
# Engine bracket: lms (default — auto lane unchanged) or llama (own server,
# needs --gguf). Env defaults let the watch lane switch with no hearth change.
ENGINE="${COMPACT_ENGINE:-lms}" GGUF="${COMPACT_GGUF:-}" PORT="${COMPACT_PORT:-65010}"
RAM_FLOOR_GB="${RAM_FLOOR_GB:-200}"  # loading the model needs headroom; a resident lms model skips the gate
while [ $# -gt 0 ]; do
  case "$1" in
    --lane) LANE="$2"; shift 2;;
    --character) CHAR="$2"; shift 2;;
    --sessions-dir) SDIR="$2"; shift 2;;
    --tail) TAIL="$2"; shift 2;;
    --body) BODY="$2"; shift 2;;
    --yes) YES=1; shift;;
    --model) MODEL_KEY="$2"; shift 2;;
    --engine) ENGINE="$2"; shift 2;;
    --gguf) GGUF="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --notes) NOTES="$2"; shift 2;;
    --ram-floor-gb) RAM_FLOOR_GB="$2"; shift 2;;  # 0 disables the gate

    --request-file) REQUEST_FILE="$2"; shift 2;;  # compact-watch claim: honest ending on exit
    -*) die "unknown flag: $1";;
    *) [ -n "$NAME" ] && die "one session name only (got '$NAME' and '$1')"; NAME="$1"; shift;;
  esac
done
[ -n "$NAME" ] || die "usage: compact-companion-session.sh <session-name> [flags]"
NAME="${NAME%.json}"

if [ -z "$SDIR" ]; then
  case "$LANE" in
    v2) SDIR="$V2/sessions";;
    hearth) [ -n "$CHAR" ] || die "--lane hearth requires --character"
            SDIR="$DATA/characters/$CHAR/sessions";;
    *) die "--lane must be v2 or hearth";;
  esac
fi
SFILE="$SDIR/$NAME.json"
[ -f "$SFILE" ] || die "session not found: $SFILE"
[ -f "$TOOL" ] || die "compact tool missing: $TOOL"

# Character up front — the maintenance lock is keyed on it.
if [ -z "$CHAR" ]; then
  CHAR="$(SFILE="$SFILE" python3 -c 'import json,os;print(json.load(open(os.environ["SFILE"])).get("character") or "")')"
  [ -n "$CHAR" ] || die "session has no 'character' sidecar key — pass --character"
fi

# ── 0. maintenance lock (design: auto-compaction-on-close) ────────────────────
# Re-exec under the per-character flock: held for this process's whole life,
# released by the kernel on ANY death. Held by someone else (a live bot, or
# another compaction) → exit 3 with a human line. The lock is the in-progress
# truth the start doors check — no false negatives, no stale state.
if [ -z "${COMPACT_LOCK_HELD:-}" ]; then
  [ -x "$LIVEPY" ] || die "python that imports hearth not found (the maintenance lock needs it; set HEARTH_PYTHON): $LIVEPY"
  exec /usr/bin/env COMPACT_LOCK_HELD=1 HEARTH_DATA="$DATA" \
    "$LIVEPY" -m hearth.session.maintenance_lock run "$CHAR" --op compact --session "$NAME" \
    -- "$0" "${ORIG_ARGS[@]}"
fi
ok "maintenance lock held: $CHAR (op=compact)"

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/compact-session.XXXXXX")"
cleanup(){
  rc=$?
  llama_stop   # no-op unless the llama bracket spawned a server — dies with us
  rm -rf "$SCRATCH"
  # Honest ending for a compact-watch claim (queue lib: success removes it,
  # anything else becomes .failed carrying why — never auto-retried).
  queue_finish "$rc" "$REQUEST_FILE" "$STEP" "$LAST_ERR"
}
trap cleanup EXIT

# ram_gate — LOADING needs headroom, whichever engine loads. Watch-claimed
# runs defer honestly (re-parked, retried); manual runs halt and say why.
ram_gate(){
  [ "${RAM_FLOOR_GB}" -gt 0 ] || return 0
  AVAIL="$(model_ram_avail_gb)"
  if [ "${AVAIL:-0}" -lt "$RAM_FLOOR_GB" ]; then
    if [ -n "$REQUEST_FILE" ] && [ -e "$REQUEST_FILE" ]; then
      warn "deferred: ${AVAIL}GB available < ${RAM_FLOOR_GB}GB floor — re-parked for the watch"
      queue_defer "$REQUEST_FILE"
      exit 0
    fi
    die "only ${AVAIL}GB RAM available (< ${RAM_FLOOR_GB}GB floor) — free RAM, or --ram-floor-gb 0 to force"
  fi
  ok "RAM floor holds: ${AVAIL}GB available ≥ ${RAM_FLOOR_GB}GB"
}

# ── 1. live-session guard ─────────────────────────────────────────────────────
# (Same lesson as the flip guard: process presence, not port state; and every
# zero-match pipeline gets `|| true` under set -euo pipefail.)
say "1. live-session guard"
HBOT="$(pgrep -f 'python[0-9.]* -m hearth\.pipeline\.bot' || true)"
[ -z "$HBOT" ] || die "a bot is running — compact only while every bot is down (a mid-turn snapshot would race the rewrite)"
ok "no bot process — safe window"

# ── 2. stats gate ─────────────────────────────────────────────────────────────
say "2. stats gate"
STATS="$("python3" "$TOOL" --sessions-dir "$SDIR" stats "$NAME")" || die "stats failed"
read -r S_BYTES S_MSGS S_HELD <<EOF
$(printf '%s' "$STATS" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["bytes"],d["msg_count"],d.get("held"))')
EOF
EST_TOK=$(( S_BYTES / 4 ))
[ "$EST_TOK" -le 60000 ] || die "~${EST_TOK} tokens estimated (${S_BYTES} bytes) — past the 60K single-pass ceiling; chunked map-reduce is not built yet"
[ "$S_HELD" = "True" ] || warn "session is not held — compacting an ephemeral transcript is unusual"
[ "$EST_TOK" -ge 8000 ] || warn "only ~${EST_TOK} tokens — below the ~30-40K trigger; compaction may not be worth it yet"
ok "$S_MSGS messages, ${S_BYTES} bytes (~${EST_TOK} tokens est.) — inside the single-pass zone"

# ── 4. the continuity note ────────────────────────────────────────────────────
if [ -n "$BODY" ]; then
  say "4. note supplied (--body) — skipping the LLM lane"
  [ -f "$BODY" ] || die "--body file not found: $BODY"
else
  say "4. render transcript + assemble prompt"
  RENDER="$SCRATCH/transcript.txt"
  SFILE="$SFILE" CHAR="$CHAR" RENDER="$RENDER" python3 - <<'PY'
import json, os
data = json.load(open(os.environ["SFILE"]))
who_ai = os.environ["CHAR"].upper()
lines = []
for m in data["messages"]:
    role, c = m.get("role"), (m.get("content") or "").strip()
    if not c:
        continue
    who = {"user": "PARTNER", "assistant": who_ai}.get(role, (role or "?").upper())
    lines.append(f"{who}: {c}")
open(os.environ["RENDER"], "w").write("\n\n".join(lines) + "\n")
PY
  PROMPT="$SCRATCH/prompt.md"
  TPL="$TPL" CHAR="$CHAR" RENDER="$RENDER" PROMPT="$PROMPT" python3 - <<'PY'
import os, re
tpl = open(os.environ["TPL"]).read()
tpl = re.sub(r"\A<!--.*?-->\s*", "", tpl, flags=re.S)  # strip provenance header
for ph in ("{{CHARACTER}}", "{{TRANSCRIPT}}"):
    assert ph in tpl, f"template missing {ph}"
out = tpl.replace("{{CHARACTER}}", os.environ["CHAR"]).replace(
    "{{TRANSCRIPT}}", open(os.environ["RENDER"]).read())
open(os.environ["PROMPT"], "w").write(out)
PY
  ok "prompt assembled ($(wc -c < "$PROMPT" | tr -d ' ') bytes) — goes to the model on stdin"

  # model bracket, engine-dispatched. lms: resident-or-load-and-unload (only
  # what WE loaded). llama: our own loopback server — nothing shared.
  say "5. model bracket ($ENGINE) + summarize (this can take a few minutes)"
  NOTE_BASE="" WE_LOADED=0 BEFORE=""
  case "$ENGINE" in
  llama)
    [ -n "$GGUF" ] || die "--engine llama needs --gguf PATH (or COMPACT_GGUF)"
    llama_cli || die "llama-server not found (PATH or a brew prefix)"
    ram_gate
    mkdir -p "$DATA/logs"
    LLOG="$DATA/logs/compact-llama.$(date +%Y%m%d-%H%M%S).log"
    llama_start "$GGUF" 131072 "$PORT" "$LLOG" "$DATA/config/serve.toml" \
      || die "llama-server bracket failed (log: $LLOG)"
    IDENT="hearth-compactor" NOTE_BASE="http://127.0.0.1:$PORT/v1"
    ok "our llama-server is healthy on :$PORT ($(basename "$GGUF"))"
    ;;
  lms)
    model_cli || die "lms CLI not found (PATH, ~/.lmstudio/bin, or a brew prefix)"
    BEFORE="$(model_ps)"
    if model_is_resident "$BEFORE" "$MODEL_KEY"; then
      IDENT="$MODEL_KEY"
      ok "$MODEL_KEY already resident — using it, will NOT unload (RAM gate moot)"
    else
      ram_gate
      IDENT="hearth-compactor"
      model_load "$MODEL_KEY" "$IDENT" || die "lms load $MODEL_KEY failed"
      WE_LOADED=1
      ok "loaded $MODEL_KEY as '$IDENT'"
    fi
    ;;
  *) die "--engine must be lms or llama (got '$ENGINE')";;
  esac

  BODY="$BODIES/$NAME.$(date +%Y.%m.%d).md"
  [ -e "$BODY" ] && BODY="${BODY%.md}.$(date +%H%M).md"
  mkdir -p "$BODIES"
  # The note itself: prompt on stdin (never argv), note on stdout, counts and
  # timings on stderr into the run log. Talks the OpenAI /v1 contract — the one
  # surface both candidate open engines already serve — so a future engine swap
  # leaves this line alone. Needs a 3.11+ interpreter for tomllib; LIVEPY is
  # already a hard requirement above (the maintenance lock runs under it).
  printf '  … sending the prompt to %s — this is the long step\n' "$IDENT"
  # `if ! cmd` cannot report the command's status: $? inside the branch is the
  # negation's, always 0 (the earlier line had the same bug, printing rc=0
  # for every failure). Capture it, and capture the generator's last stderr
  # line too — that is the sentence the panel shows for a .failed run.
  NOTE_ERR="$SCRATCH/note.err"
  set +e
  /usr/bin/env ${NOTE_BASE:+LM_BASE_URL=$NOTE_BASE} \
    "$LIVEPY" "$HERE/compact-note.py" --model "$IDENT" \
    --config "$DATA/config/serve.toml" --telemetry "$SCRATCH/telemetry.json" \
    ${NOTE_ARGS:-} < "$PROMPT" > "$BODY" 2>"$NOTE_ERR"
  RC=$?
  set -e
  cat "$NOTE_ERR" >&2 || true
  if [ "$RC" -ne 0 ]; then
    [ "$WE_LOADED" = 1 ] && { model_unload "$IDENT" || true; }
    llama_stop
    rm -f "$BODY"
    die "note generation failed (rc=$RC): $(tail -n 1 "$NOTE_ERR" 2>/dev/null | cut -c1-160)"
  fi
  ok "note captured → ${BODY/#$HOME/~}"

  if [ "$ENGINE" = "llama" ]; then
    llama_stop
    ok "bracket closed — our server is down"
  elif [ "$WE_LOADED" = 1 ]; then
    model_unload "$IDENT"
    model_restore_evicted "$BEFORE"
    ok "bracket closed — resident set restored"
  fi

  # Telemetry summary (timings under llama-server, counts-only under lms)
  # rides into compaction.notes.
  if [ -f "$SCRATCH/telemetry.json" ]; then
    TSUM="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("summary",""))' \
      "$SCRATCH/telemetry.json" 2>/dev/null || true)"
    [ -n "$TSUM" ] && NOTES="${NOTES:+$NOTES · }[$ENGINE] $TSUM"
  fi
fi

# ── 6. mechanical checks ──────────────────────────────────────────────────────
say "6. mechanical checks on the note"
WORDS="$(wc -w < "$BODY" | tr -d ' ')"
SECTIONS="$(grep -c '^## ' "$BODY" || true)"
WFLAG=0
[ "$WORDS" -ge 100 ] || die "note is only $WORDS words — refusing; body kept at $BODY"
if [ "$WORDS" -lt 350 ] || [ "$WORDS" -gt 1500 ]; then warn "$WORDS words — outside the 600-900-token band"; WFLAG=1; fi
if [ "$SECTIONS" -ne 5 ]; then warn "$SECTIONS '## ' sections (expected 5)"; WFLAG=1; fi
if grep -qi 'as an AI\|I cannot assist\|I can.t help with' "$BODY"; then warn "note smells like a refusal"; WFLAG=1; fi
[ "$WFLAG" = 0 ] && ok "$WORDS words, $SECTIONS sections — in band"

# ── 7. review gate ────────────────────────────────────────────────────────────
if [ "$YES" = 1 ]; then
  [ "$WFLAG" = 0 ] || die "--yes with warnings: NOT applying. Review/edit the note, then re-run with --body '$BODY'"
else
  say "7. REVIEW: read the note before it becomes $CHAR's memory —"
  say "     $BODY"
  printf '   apply compact to %s? [y/N] ' "$NAME"
  read -r ANS || ANS=""   # EOF (non-tty) = decline, not a crash
  case "$ANS" in y|Y) ;; *)
    say "   left un-applied — edit if needed, re-run with: --body '$BODY' --yes"
    # a declined claim goes back to .request (not consumed, not failed)
    queue_decline "$REQUEST_FILE"
    exit 0;; esac
fi

# ── 8. compact + report ───────────────────────────────────────────────────────
say "8. compact (tool baks first, verifies after)"
METHOD="local-122b-scripted"
[ "$ENGINE" = "llama" ] && METHOD="local-llamaserver-scripted"
RESULT="$(python3 "$TOOL" --sessions-dir "$SDIR" compact "$NAME" \
  --from "$BODY" --tail "$TAIL" \
  --method "$METHOD" --notes "${NOTES:-scripted continuity-note compact}")" \
  || die "compact failed — live file untouched or restorable (restore-from-bak)"
printf '%s' "$RESULT" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("  \033[32m✓\033[0m %s → %s messages, live %s bytes (bak %s)" % (d["pre_message_count"], d["post_message_count"], d.get("live_bytes","?"), d.get("bak_bytes","?")))'
say ""
say "COMPACT OK — $NAME now carries the continuity note + last $TAIL verbatim turns."
say "  undo anytime: python3 '$TOOL' --sessions-dir '$SDIR' restore-from-bak '$NAME'"
