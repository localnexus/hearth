# Session continuity — the companion's `sessions/` dir and CLI flags

Per-turn transcript saved to `characters/<character>/sessions/<id>.json` under the data root (dir `0700`, file `0600`, gitignored). Sessions are keyed by companion: the choosers only offer the live character's; `python -m hearth.session.session_store list` shows every companion's. Persona prompt NOT stored. No new env vars or deps.

**Saved by default** — `./stop.sh` keeps the session (unclean exits keep it too); deleting is the
explicit act. The one carve-out: a `--memory recall-only` sitting stays **transcript-ephemeral** —
its file is truly deleted on graceful stop (and its crash leftover is swept by the next fresh
start) unless explicitly held.

| Flag | Script | Effect |
|---|---|---|
| `--resume [file\|name]` | `start.sh` | Reload a prior session. Bare = metadata-only picker if >1 candidate. |
| `--new` | `start.sh` | Start fresh. Saved sessions are kept; only recall-only leftovers are swept. |
| `--memory <mode>` | `start.sh` | This sitting's memory posture: `full` (default) · `recall-only` (recalls, retains nothing) · `off` (no seam). Governs the memory **bank**; the transcript layer keys ONE default off it — a recall-only sitting's transcript deletes on graceful stop unless held. Non-full sittings stamp the session file; `--resume` without the flag inherits the stamp. See [memory.md](../memory.md). |
| `--hold [name]` | `stop.sh` | Name/keep: mark the session **held** — sticky, sweep-exempt, named for `--resume <name>`. Also the explicit keep for a recall-only sitting's transcript. |
| `--discard-held <name>` | `stop.sh` | True-delete ONE held session (immediate). Bare/`--all` wipe of **all** held is irreversible → requires typing **`HEARTH`** to confirm (refused non-interactively). |

**Bare `./start.sh`:** interactive TTY → a metadata-only **chooser** (`0`=new · N=resume · Enter=cancel) listing every saved session; non-interactive → falls through to fresh (nothing is discarded), except a hard **exit-2 guard** when a recall-only leftover is present (automation never silently discards the privacy tier's one recovery chance). Resume mismatch → **warns, never blocks**; malformed file → fresh fallback. See the [runbook](../runbook/03.5-session-continuity.md) and [debugging/session-continuity-faults.md](../debugging/session-continuity-faults.md).
