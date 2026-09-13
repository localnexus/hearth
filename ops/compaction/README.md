# ops/compaction — session compaction

When a long talk closes, this writes a short continuity note from it — in the
companion's own voice, covering the arc, the facts, the open threads, the
feelings, and the callbacks worth keeping — and folds that note back into the
session in place of the transcript it summarizes. It runs under the
per-character maintenance lock, so nothing else touches the session while it
works. The facade's compact watch runs this at close for sessions that ran
long, and looks for it first at `<data root>/ops/compaction/`, then here.

## Files

| File | What it does |
|---|---|
| `compact-companion-session.sh` | The whole procedure, start to finish: checks the session is safe to touch, writes the note, checks the note looks right, asks for a look before applying it (unless told not to), then applies it. |
| `compact-model-door.sh` | Talks to the companion's own resident model server — the default. Nothing is loaded or unloaded; this just confirms the server is there. |
| `compact-model-llama.sh` | Starts and stops a separate, own-purpose model server for the opt-in engine below. |
| `compact-queue-lib.sh` | Keeps the queue file honest — parked, claimed, deferred, declined, or failed — so the auto-compaction panel can show what happened. |
| `compact-note.py` | The one call that turns a prompt into a note: prompt in on standard input, note out on standard output, nothing else. |
| `compact_session.py` | Backs up the live session, rewrites it with the note in place, and can restore the backup if needed. |
| `prompts/continuity-note.md` | The note-writing prompt itself. |

## Engines

- **door** — the default. The companion's own resident model server answers; nothing is loaded first and nothing is restored after.
- **llama** — an own-purpose server started just for this, for a different or larger model than the resident one. Opt in with `--engine llama --gguf PATH`, or the `COMPACT_ENGINE` / `COMPACT_GGUF` environment variables. The memory floor check only applies to this engine — the resident door never needs to load anything.

## Running it by hand

```
compact-companion-session.sh <session-name> --character <name>
```

This walks through the checks, writes the note, and pauses for a look before
applying it. Pass `--yes` to skip that pause for an unattended run. If you
already have a note you like — hand-edited or written some other way — pass
it back in with `--body <file>` instead of writing a new one. To undo an
applied compaction, use `restore-from-bak` on the same session name.

## Where things land

- Notes are kept under `<data root>/ops/compaction/bodies/`.
- Backups of the session as it stood before compaction go beside the session
  itself, under `pre-compaction-bak/<day>/`.
- Each unattended run appends a line to `<data root>/logs/compact-auto.log`.
- Nothing here lands anywhere under this tree — it all stays under the data
  root.

## Privacy

The note is written by a model running on your own machine. The conversation
it summarizes never leaves that machine. Notes and backups are exactly as
private as the sessions they come from: kept local, and never committed
anywhere.
