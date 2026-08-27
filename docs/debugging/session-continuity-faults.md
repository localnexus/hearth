# Session continuity faults

### Fault S1 — bare `./start.sh`: chooser (interactive) or exit 2 (automation)

**Symptom:** no-flag `./start.sh` shows a numbered **menu**, or **exits 2** without loading the model.

**By design:**
- **Interactive terminal:** a metadata-only chooser — `0. new session` (discard ephemeral orphans, keep held) + each resumable session (held too). Number = resume · `0` = fresh · **Enter/Ctrl-C = cancel** (nothing started or discarded).
- **Non-interactive** (launchd / web / piped stdin): the hard **guard** exits 2 so automation never blocks or silently discards — re-run with `--resume <name>` or `--new`.

> Held sessions appear in the chooser but are **never** removed by `0` / `--new`; only `./stop.sh --discard-held` deletes them.

### Fault S2 — `--resume <name>` shows nothing / "session not found"

**Symptom:** `./start.sh --resume work-chat` reports the session can't be found.

**Check:**
1. Was `./stop.sh --hold work-chat` run **before** the bot stopped?
2. `ls characters/<character>/sessions/` (under the data root) — does `work-chat.json` or `session-work-chat.json` exist?
3. Bare `./start.sh --resume` (no name) shows the full metadata picker — find the file there and use its exact name or path.

### Fault S3 — persona or model seems different after a resume

**Symptom:** resume loads fine but a mismatch warning appears in the log; the configured voice or model changed since the original session.

**Cause:** `model`, `voice`, or `prompt_sha256` in the sidecar differs from the current active config. A mismatch warns but never blocks; the session memory (turn history) loads correctly regardless.

**Fix:** nothing required if intentional. If accidental, confirm the active config matches the original session's values — the selected `model.toml` `id` and the active voice bundle's `tag`, both chosen via `config/active.toml`.

### Fault S4 — malformed or empty session file

**Symptom:** `--resume` silently falls back to a fresh session with a warning in the log.

**Cause:** invalid JSON in the session file (truncated write is unlikely — snapshots use atomic `os.replace`; more likely a hand-edit).

**Confirm:** `python3 -m json.tool characters/<character>/sessions/<file>.json` — fails = corrupt.

**Fix:** `rm characters/<character>/sessions/<file>.json` and start fresh. Fallback to fresh is by design; nothing crashes.

### Fault S5 — where the transcript lives; how to purge it

**Transcript path:** `characters/<character>/sessions/<id>.json` under the data root — plaintext message list, `0600`, gitignored. The persona prompt is NOT stored.

**Purge options:**
- `./stop.sh` — deletes the ephemeral session on graceful stop (default).
- `./stop.sh --discard-held [name|--all]` — true-delete a held session.
- Manual: `rm characters/<character>/sessions/<file>.json` for an orphan you've confirmed you don't need.

Each companion's `sessions/` is `0700`, gitignored, never exposed over the `:65000` web endpoint. If it ever gets accidentally un-ignored, re-add `sessions/` to `.gitignore` immediately.
