# compact-model-llama.sh — the llama-server bracket, sourced by the compactor.
# Built 2026-09-05 (design: v2 the design record, kept outside this tree).
#
# THIS FILE IS THE WHOLE llama-server DEPENDENCY, the same posture its sibling
# compact-model-lib.sh holds for LM Studio. A batch job owns its own server
# process — nothing to evict, nothing to restore, no JIT identifier — so this
# bracket is: spawn OUR server on loopback, wait until it is healthy, and take
# it down again on ANY exit. The note generator (compact-note.py) is pointed at
# it with one environment variable and is otherwise untouched.
#
# Auth: the server starts with --api-key-file aimed at the SAME token file
# serve.toml's lm_token_source names, so the note call's existing token
# resolution authenticates unchanged. The token itself never touches argv,
# this script, or any output.
#
# Caller contract: `warn` and `bad` as in the sibling lib; fallbacks defined so
# the file can be sourced and exercised on its own.
command -v warn >/dev/null 2>&1 || warn(){ printf '  ! %s\n' "$*" >&2; }
command -v bad  >/dev/null 2>&1 || bad(){  printf '  x %s\n' "$*" >&2; }

LLAMA=""       # resolved binary
LLAMA_PID=""   # the server WE spawned (empty = nothing to stop)

# llama_cli — resolve llama-server. PATH first, then the brew prefixes (a run
# spawned by the facade inherits launchd's bare PATH — same lesson, 2026-09-04).
llama_cli(){
  local p
  p="$(command -v llama-server 2>/dev/null)" || p=""
  if [ -z "$p" ]; then
    for p in /opt/homebrew/bin/llama-server /usr/local/bin/llama-server; do
      [ -x "$p" ] && break
      p=""
    done
  fi
  [ -n "$p" ] || return 1
  LLAMA="$p"
}

# llama_token_file SERVE_TOML — print the PATH lm_token_source names (never the
# token). Plain sed, no TOML parser: the key is one quoted string on one line.
llama_token_file(){
  [ -n "${1:-}" ] && [ -f "$1" ] || return 0
  local p
  p="$(sed -n 's/^[[:space:]]*lm_token_source[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' "$1" | head -1)"
  case "$p" in "~/"*) p="$HOME/${p#\~/}";; esac
  [ -f "$p" ] && printf '%s' "$p"
  return 0
}

# llama_start GGUF CTX PORT LOG [SERVE_TOML] — spawn our server, wait healthy.
# /health answers 503 while the model loads and 200 when ready (and is exempt
# from the api key). A big GGUF can take minutes to load: 600 s budget, and the
# wait fails fast if the process dies (the log path is the pointer, its content
# is server internals only — model metadata, never companion content).
llama_start(){
  local gguf="$1" ctx="$2" port="$3" log="$4" tok i code
  [ -f "$gguf" ] || { bad "GGUF not found: $gguf"; return 1; }
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    bad "port $port is already in use — pass --port / COMPACT_PORT"
    return 1
  fi
  tok="$(llama_token_file "${5:-}")"
  set -- "$LLAMA" -m "$gguf" --host 127.0.0.1 --port "$port" -c "$ctx" \
    -a hearth-compactor --no-webui --log-file "$log"
  [ -n "$tok" ] && set -- "$@" --api-key-file "$tok"
  "$@" >/dev/null 2>&1 &
  LLAMA_PID=$!
  i=0
  while [ "$i" -lt 600 ]; do
    kill -0 "$LLAMA_PID" 2>/dev/null || {
      bad "llama-server exited during load — see $log"
      LLAMA_PID=""
      return 1
    }
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
      "http://127.0.0.1:$port/health" 2>/dev/null)" || code=""
    [ "$code" = "200" ] && return 0
    sleep 1
    i=$((i + 1))
  done
  bad "llama-server not healthy after 600 s — see $log"
  llama_stop
  return 1
}

# llama_stop — take down the server WE spawned. Safe as a trap: no-op when
# nothing was started, escalates TERM → KILL after 10 s.
llama_stop(){
  [ -n "${LLAMA_PID:-}" ] || return 0
  kill "$LLAMA_PID" 2>/dev/null || true
  local i=0
  while kill -0 "$LLAMA_PID" 2>/dev/null && [ "$i" -lt 100 ]; do
    sleep 0.1
    i=$((i + 1))
  done
  kill -0 "$LLAMA_PID" 2>/dev/null && kill -9 "$LLAMA_PID" 2>/dev/null
  LLAMA_PID=""
  return 0
}
