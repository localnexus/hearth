# Session continuity faults

### Fault S1 — bare `./start.sh`: chooser (interactive) or exit 2 (automation)

**Symptom:** no-flag `./start.sh` shows a numbered **menu**, or **exits 2** without loading the model.

**By design:**
- **Interactive terminal, ≥1 resumable session:** a metadata-only chooser — `0. new session` + each saved session (named ones tagged `[HELD]`). Number = resume · `0` = fresh · **Enter/Ctrl-C = cancel** (nothing started or discarded). With zero saved sessions (fresh install) there's nothing to choose from — it starts fresh with no menu, same as `--new`.
- **Non-interactive** (launchd / web / piped stdin): starts fresh without discarding anything; the hard **guard** exits 2 only when a **recall-only leftover** (a crashed `--memory recall-only` sitting's file) is present, so automation never silently deletes the privacy tier's one recovery chance — re-run with `--resume <name>` or `--new`.

> Sessions save by default; `0` / `--new` sweep only recall-only leftovers. Saved and held sessions are never removed by a fresh start — only `./stop.sh --discard-held` (or deleting the file) removes them.

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
- `./stop.sh --discard-held [name|--all]` — true-delete a held session.
- Manual: `rm characters/<character>/sessions/<file>.json` for a session you've confirmed you don't need (sessions save by default — a graceful stop no longer deletes anything except a recall-only sitting's transcript).

Each companion's `sessions/` is `0700`, gitignored, never exposed over the `:65000` web endpoint. If it ever gets accidentally un-ignored, re-add `sessions/` to `.gitignore` immediately.
