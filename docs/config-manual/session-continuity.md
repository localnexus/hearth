# Session continuity — the companion's `sessions/` dir and CLI flags

Per-turn transcript saved to `characters/<character>/sessions/<id>.json` under the data root (dir `0700`, file `0600`, gitignored). Sessions are keyed by companion: the choosers only offer the live character's; `python -m hearth.session.session_store list` shows every companion's. Persona prompt NOT stored. No new env vars or deps.

**Ephemeral by default** — `./stop.sh` deletes on graceful stop; unclean exits leave a recoverable orphan.

| Flag | Script | Effect |
|---|---|---|
| `--resume [file\|name]` | `start.sh` | Reload a prior session. Bare = metadata-only picker if >1 candidate. |
| `--new` | `start.sh` | Discard ephemeral orphans + start fresh (held files never touched). |
| `--memory <mode>` | `start.sh` | This sitting's memory posture: `full` (default) · `recall-only` (recalls, retains nothing) · `off` (no seam). Memory **bank** only — the transcript flags above are unchanged. Non-full sittings stamp the session file; `--resume` without the flag inherits the stamp. See [memory.md](../memory.md). |
| `--hold [name]` | `stop.sh` | Promote to held class — sticky, purge-exempt, optionally named. |
| `--discard-held <name>` | `stop.sh` | True-delete ONE held session (immediate). Bare/`--all` wipe of **all** held is irreversible → requires typing **`HEARTH`** to confirm (refused non-interactively). |

**Bare `./start.sh`:** interactive TTY → a metadata-only **chooser** (`0`=new · N=resume · Enter=cancel), held sessions included so work-topics are pickable; non-interactive → the hard **exit-2 guard** (automation never blocks or silently discards). Resume mismatch → **warns, never blocks**; malformed file → fresh fallback. See the [runbook](../runbook/03.5-session-continuity.md) and [debugging/session-continuity-faults.md](../debugging/session-continuity-faults.md).
