# compact-queue-lib.sh — the queue-file lifecycle, sourced by the compactor.
# Extracted 2026-09-04 from compact-companion-session.sh (which had grown past
# the 15 KB script limit). This is the half that implements the contract the
# facade depends on; the compactor proper is the compaction itself.
#
# One JSON file per requested compaction, in DATA/ops/compact-queue/:
#
#   <character>.<session>.request   parked  — waiting for a safe window
#   <character>.<session>.running   claimed — a compactor owns it
#   <character>.<session>.failed    failed  — NEVER auto-retried; a human clears it
#
# The facade's compact_watch does the parking and the claim (.request →
# .running, stamping claimed_ts). From the claim onward the file belongs to the
# spawned compactor, which is handed its path as --request-file. Every path out
# of that ownership lives here, and there are exactly four:
#
#   queue_finish   the exit trap — success removes the file, anything else
#                  becomes .failed carrying WHY (see the stamp note below)
#   queue_defer    the RAM floor did not hold — back to .request + deferred_ts,
#                  which the watch re-checks after DEFER_RECHECK_S. A defer is
#                  not a failure: it is retried, a .failed never is.
#   queue_decline  the operator said no at the review gate — back to .request,
#                  not consumed, not failed
#   queue_stamp    the primitive under all three
#
# Mirror of: hearth src/hearth/supervisor/compact_watch.py (queue_status reads
# the keys stamped here; _QUEUE_STATES is the suffix table above). Change one,
# read the other.
#
# A manual run (no --request-file) passes an empty path to every function here
# and they all no-op — the desk lane carries no queue file at all.

# queue_stamp FILE [KEY VALUE]... — merge keys into a queue file's JSON.
# Values that parse as JSON are stored typed (3 → int); everything else is
# stored as a string. Missing or corrupt file → treated as {}, so a stamp
# never loses the ending it is trying to record.
queue_stamp(){
  local f="$1"; shift
  [ -n "$f" ] && [ -e "$f" ] || return 0
  QF="$f" python3 - "$@" <<'PY' || true
import json, os, sys
p = os.environ["QF"]
try:
    d = json.load(open(p))
    if not isinstance(d, dict):
        d = {}
except Exception:
    d = {}
a = sys.argv[1:]
for k, v in zip(a[0::2], a[1::2]):
    try:
        d[k] = json.loads(v)
    except Exception:
        d[k] = v
open(p, "w").write(json.dumps(d, indent=1))
PY
}

# queue_finish RC FILE [STEP] [ERR] — the honest ending for a claim.
# rc 0 removes the file; anything else renames it .failed and stamps why.
# The launch page can only render what is in this file: until the reason was
# stamped here (2026-09-04) a run that died in its first second looked, on
# screen, exactly like one that never started. Short messages and paths only —
# every bad/die line in the compactor is counts and paths by construction,
# never companion content.
queue_finish(){
  local rc="$1" f="$2" step="${3:-}" err="${4:-}" failed
  [ -n "$f" ] && [ -e "$f" ] || return 0
  if [ "$rc" -eq 0 ]; then rm -f "$f"; return 0; fi
  failed="${f%.running}.failed"
  mv "$f" "$failed" 2>/dev/null || return 0
  queue_stamp "$failed" error "$err" step "$step" exit_code "$rc" \
                        failed_ts "$(date +%s)"
}

# queue_defer FILE — re-park a claim the RAM floor turned away. Restored as
# .request with deferred_ts, so the watch probes again later instead of
# spawning-and-deferring every tick.
queue_defer(){
  local f="$1" req
  [ -n "$f" ] && [ -e "$f" ] || return 0
  req="${f%.running}.request"
  mv "$f" "$req" 2>/dev/null || return 0
  queue_stamp "$req" deferred_ts "$(date +%s)"
}

# queue_decline FILE — the operator read the note and said no. Back to
# .request unchanged: not consumed, not failed, no breadcrumb to clear.
queue_decline(){
  local f="$1"
  [ -n "$f" ] && [ -e "$f" ] || return 0
  mv "$f" "${f%.running}.request" 2>/dev/null || true
}
