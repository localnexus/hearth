#!/usr/bin/env bash
# install.sh — Hearth, from a bare Apple Silicon Mac to the first-run setup, in one command.
#
#   curl -fsSL https://raw.githubusercontent.com/localnexus/hearth/main/install.sh | bash
#   git clone https://github.com/localnexus/hearth && cd hearth && ./install.sh   # same script
#
# Every step checks first and does only what is missing, then prints one line:
#   +  done now      ·  already there      !  a note      -  skipped      x  failed
# Re-running repairs; it never pulls the repo unless you say --update.
#
# Flags:  --yes            take every default, ask nothing (speech models yes; no start)
#         --dir DIR        where to put Hearth (default $HEARTH_HOME or ~/hearth)
#         --no-weights     do not fetch the speech models (~4.6 GB)
#         --model REPO     the Hugging Face GGUF repo for your llama-server line
#         --lm-url URL     your model server, if not http://127.0.0.1:8080/v1
#         --memory on|off  answer the memory question up front
#         --update         git pull --ff-only, then reinstall (the guide's update recipe)
#         --no-init        stop before the first-run setup and print what to run
#         --quiet          no banner
# Exit codes: 0 done · 1 a step failed · 2 stopped for something only you can do.
# Two stops are honest ones: the Xcode command-line tools and Homebrew. Both ask you
# for a password or a click; this script prints their command and gets out of the way.
# Nothing here runs long-lived — the model server is your own process in another window.
set -euo pipefail

REPO_URL="https://github.com/localnexus/hearth"
DEFAULT_LM_URL="http://127.0.0.1:8080/v1"
# ── the banner (byte-identical to hearth/init/banner.py ART; tests/test_banner.py checks) ──
IFS= read -r -d '' BANNER <<'ART' || true
            (
           ) )
          ( ( )
         _)  (_
        (   __  )      H E A R T H
         \ (  ) /      a voice at home
          `----'
ART

YES=0; DIR=""; WEIGHTS=1; MODEL=""; LM_URL="$DEFAULT_LM_URL"; MEMORY=""; UPDATE=0; INIT=1; QUIET=0
usage() { sed -n '2,22p' "$0" 2>/dev/null || echo "see the comment block at the top of install.sh"; }

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) YES=1 ;;
      --dir) DIR="$2"; shift ;;
      --no-weights) WEIGHTS=0 ;;
      --model) MODEL="$2"; shift ;;
      --lm-url) LM_URL="$2"; shift ;;
      --memory) MEMORY="$2"; shift ;;
      --update) UPDATE=1 ;;
      --no-init) INIT=0 ;;
      --quiet|-q) QUIET=1 ;;
      -h|--help) usage; exit 0 ;;
      *) fail "unknown flag $1 (try --help)" ;;
    esac
    shift
  done

  # A person at a terminal? stdout is the tell — under `curl | bash` stdin is the script.
  TTY=0; [ -t 1 ] && [ -e /dev/tty ] && TTY=1
  ASK=$TTY; [ "$YES" = 1 ] && ASK=0
  COLOUR=$TTY; [ -n "${NO_COLOR:-}" ] && COLOUR=0

  banner
  say "Hearth install — a voice on your own machine. Every step is reported; nothing is hidden."
  preflight
  tools
  code
  pyenv
  weights
  model_server
  first_run
}

# ── output ────────────────────────────────────────────────────────────────────────────
say()  { printf '%s\n' "$*"; }
did()  { printf '  + %s\n' "$*"; }
have() { printf '  · %s\n' "$*"; }
note() { printf '  ! %s\n' "$*"; }
skip() { printf '  - %s\n' "$*"; }
fail() { printf '  x %s\n' "$*" >&2; exit 1; }
stop() { printf '  x %s\n' "$1" >&2; printf '    run this, then run install.sh again:\n      %s\n' "$2" >&2; exit 2; }
ask()  { # ask "question" default(y|n) → 0 = yes
  local q="$1" d="$2" a hint="[Y/n]"
  [ "$ASK" = 1 ] || { [ "$d" = y ]; return; }
  [ "$d" = y ] || hint="[y/N]"
  printf '%s %s ' "$q" "$hint"; read -r a </dev/tty || a=""
  a="$(printf '%s' "$a" | tr '[:upper:]' '[:lower:]')"
  [ -z "$a" ] && { [ "$d" = y ]; return; }
  [ "$a" = y ] || [ "$a" = yes ]
}
banner() {
  [ "$TTY" = 1 ] && [ "$QUIET" = 0 ] || return 0
  if [ "$COLOUR" = 0 ]; then printf '%s\n' "$BANNER"; return 0; fi
  while IFS= read -r line; do
    printf '\033[38;5;208m%s\033[0m' "${line:0:23}"
    [ ${#line} -gt 23 ] && printf '\033[38;5;220m\033[1m%s\033[0m' "${line:23}"
    printf '\n'
  done <<< "${BANNER%$'\n'}"
  printf '\n'
}

# ── 1. preflight ──────────────────────────────────────────────────────────────────────
preflight() {
  say "preflight"
  [ "$(uname -s)" = Darwin ] || fail "this installer covers macOS on Apple Silicon only — see docs/HARDWARE-REQUIREMENTS.md"
  [ "$(uname -m)" = arm64 ] || fail "an Apple Silicon (M-series) Mac is needed — see docs/HARDWARE-REQUIREMENTS.md"
  [ "$(id -u)" != 0 ] || fail "run this as yourself, not as root — Homebrew and the mic grant are per user"
  have "macOS $(sw_vers -productVersion 2>/dev/null || echo '?') on $(uname -m)"
  local free; free="$(df -g "$HOME" | awk 'NR==2 {print $4}')"
  if [ -n "$free" ] && [ "$free" -lt 60 ]; then
    note "${free} GB free — the guide budgets ~60 GB (model weights are most of it)"
  else have "${free:-?} GB free"; fi
}

# ── 2. system tools ───────────────────────────────────────────────────────────────────
tools() {
  say "system tools"
  if xcode-select -p >/dev/null 2>&1; then have "Xcode command-line tools"
  else stop "the Xcode command-line tools are missing (macOS asks you with a dialog)" "xcode-select --install"; fi
  if command -v brew >/dev/null 2>&1; then have "Homebrew"
  else stop "Homebrew is missing (its installer asks for your password)" \
    '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'; fi
  brew_have portaudio "" "PortAudio (the audio library pyaudio is built against)"
  brew_have uv uv "uv (builds the Python environment; fetches Python 3.12 itself)"
  brew_have llama.cpp llama-server "llama.cpp (llama-server, the model server)"
}
BREW_LOG="${TMPDIR:-/tmp}/hearth-install-brew.log"
brew_have() { # formula command-or-empty "label" — a command from any installer counts
  if [ -n "$2" ] && command -v "$2" >/dev/null 2>&1; then have "$3"; return 0; fi
  if brew list --versions "$1" >/dev/null 2>&1; then have "$3"; return 0; fi
  HOMEBREW_NO_ENV_HINTS=1 brew install "$1" >>"$BREW_LOG" 2>&1 \
    || fail "brew install $1 failed — see $BREW_LOG"
  did "$3"
}

# ── 3. the code ───────────────────────────────────────────────────────────────────────
code() {
  say "Hearth"
  local here=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [ -f "$here/pyproject.toml" ] && grep -q '^name = "hearth"' "$here/pyproject.toml" || here=""
  fi
  if [ -n "$here" ] && [ -z "$DIR" ]; then DIR="$here"; fi
  DIR="${DIR:-${HEARTH_HOME:-$HOME/hearth}}"
  if [ -d "$DIR/.git" ]; then
    have "checkout at $DIR"
    if [ "$UPDATE" = 1 ]; then
      git -C "$DIR" pull --ff-only -q || fail "git pull --ff-only failed in $DIR (local changes? see git status)"
      did "updated to $(git -C "$DIR" rev-parse --short HEAD)"
    fi
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    fail "$DIR exists and is not a Hearth checkout — pick another place with --dir"
  else
    git clone -q "$REPO_URL" "$DIR" || fail "git clone failed — is the network up?"
    did "cloned to $DIR"
  fi
  cd "$DIR"
}

# ── 4. the Python environment ─────────────────────────────────────────────────────────
PIN_CHECK='import importlib.metadata as m
assert m.version("transformers") == "5.5.0", m.version("transformers")
from mlx_lm.models.cache import KVCache
from mlx_audio.tts.utils import load_model'
pyenv() {
  say "Python environment"
  if [ -x .venv/bin/python ]; then have ".venv (python $(.venv/bin/python -c 'import sys;print("%d.%d"%sys.version_info[:2])'))"
  else uv venv -q -p 3.12 || fail "uv venv failed"; did ".venv with Python 3.12"; fi
  if [ "$UPDATE" = 0 ] && .venv/bin/python -c "$PIN_CHECK" >/dev/null 2>&1; then
    have "packages installed, speech pins hold (transformers 5.5.0, MLX imports)"
  else
    say "    installing ~90 packages — a few minutes the first time"
    uv pip install -q -e ".[mac]" || fail "the package install failed — 'portaudio.h not found' means step 1's PortAudio is not visible to the compiler; see docs/installing-by-hand.md"
    .venv/bin/python -c "$PIN_CHECK" >/dev/null 2>&1 || fail "installed, but the speech pins do not hold — see docs/installing-by-hand.md §2"
    did "packages installed, speech pins hold"
  fi
}

# ── 5. the speech models ──────────────────────────────────────────────────────────────
HF_REPOS="mlx-community/chatterbox-turbo-fp16 mlx-community/S3TokenizerV2 mlx-community/whisper-large-v3-turbo"
weights() {
  say "speech models"
  if HF_HUB_OFFLINE=1 .venv/bin/python - $HF_REPOS >/dev/null 2>&1 <<'PY'
import sys
from huggingface_hub import snapshot_download
for r in sys.argv[1:]:
    snapshot_download(r, local_files_only=True)
PY
  then have "already in ~/.cache/huggingface"; return 0; fi
  if [ "$WEIGHTS" = 0 ]; then skip "not fetched (--no-weights) — Hearth cannot speak or hear until they are; docs/installing-by-hand.md §3"; return 0; fi
  if ! ask "Fetch the speech models now? (~4.6 GB, once; they stay in ~/.cache/huggingface)" y; then
    skip "not fetched — run install.sh again when ready"; return 0; fi
  HF_HUB_OFFLINE=0 .venv/bin/python - $HF_REPOS <<'PY' || fail "the fetch failed — network? run install.sh again; it resumes"
import sys
from huggingface_hub import snapshot_download
for r in sys.argv[1:]:
    print("    " + r, flush=True); snapshot_download(r)
PY
  did "speech models in ~/.cache/huggingface"
}

# ── 6. the model server ───────────────────────────────────────────────────────────────
model_server() {
  say "model server"
  if curl -fsS -m 3 "${LM_URL%/}/models" >/dev/null 2>&1; then have "answering at $LM_URL"; return 0; fi
  local line="llama-server -hf ${MODEL:-<user>/<model-repo>} -c 0 --port 8080 -a my-model"
  note "nothing answers at $LM_URL yet. In another terminal window, serve a model:"
  say "      $line"
  say "    (or -m /path/to/model.gguf for one you have; how to choose: docs/installing.md)"
}

# ── 7. hand over to the first-run setup ───────────────────────────────────────────────
first_run() {
  say "first run"
  local args=()
  [ "$YES" = 1 ] && args+=(--yes)
  [ -n "$MEMORY" ] && args+=(--memory "$MEMORY")
  [ "$LM_URL" != "$DEFAULT_LM_URL" ] && args+=(--lm-url "$LM_URL")
  if [ "$INIT" = 0 ]; then
    skip "not run (--no-init). Next:  cd $DIR && .venv/bin/python -m hearth.init ${args[*]:-}"; return 0; fi
  say ""
  export HEARTH_BANNER_SHOWN=1
  if [ "$TTY" = 1 ]; then exec .venv/bin/python -m hearth.init ${args[@]+"${args[@]}"} </dev/tty; fi
  exec .venv/bin/python -m hearth.init ${args[@]+"${args[@]}"}
}

main "$@"
