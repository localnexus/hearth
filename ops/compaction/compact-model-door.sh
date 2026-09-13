# compact-model-door.sh — the resident-door "bracket": nothing to load,
# nothing to restore. The companion's own model server answers; this file
# only proves it is there and re-homes the RAM gate the own-server bracket
# needs.

# model_ram_avail_gb — free+inactive+purgeable+speculative pages, in GB.
# Carried over verbatim from the dropped lib: the llama bracket's ram_gate
# calls this by name.
model_ram_avail_gb(){ vm_stat 2>/dev/null | awk 'NR==1{ps=$8} /Pages (free|inactive|purgeable|speculative):/ {gsub("\\.","",$NF); s+=$NF} END{if(ps=="")ps=4096; printf "%d", s*ps/1073741824}'; }

# door_base — the base URL for the resident server: $LM_BASE_URL if set;
# else lm_base_url out of "$DATA/config/serve.toml" (same sed idiom
# llama_token_file uses for lm_token_source); else the loopback default.
door_base(){
  if [ -n "${LM_BASE_URL:-}" ]; then
    printf '%s' "$LM_BASE_URL"
    return 0
  fi
  local p
  p="$(sed -n 's/^[[:space:]]*lm_base_url[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' \
    "$DATA/config/serve.toml" 2>/dev/null | head -1)"
  if [ -n "$p" ]; then
    printf '%s' "$p"
  else
    printf '%s' "http://127.0.0.1:8080/v1"
  fi
}

# door_up BASE_URL — is the resident server there? Any HTTP status back from
# <BASE_URL>/models (an HTTPError included — 401 still means the server
# answered) is up; unreachable (URLError/OSError/timeout) is not. No token is
# sent, and this prints nothing — it only sets the exit code.
door_up(){
  "$LIVEPY" - "$1" <<'PY'
import sys
import urllib.error
import urllib.request

base = sys.argv[1]
try:
    urllib.request.urlopen(base.rstrip("/") + "/models", timeout=5)
except urllib.error.HTTPError:
    pass
except (urllib.error.URLError, OSError, TimeoutError):
    sys.exit(1)
PY
}
