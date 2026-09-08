# Session continuity — the companion's `sessions/` dir and CLI flags

Per-turn transcript saved to `characters/<character>/sessions/<id>.json` under the data folder (dir `0700`, file `0600`, gitignored). Sessions are keyed by companion: the choosers only offer the live character's; `python -m hearth.session.session_store list` shows every companion's. Persona prompt NOT stored. No new env vars or deps.

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

## Getting a session file out

The control panel offers two ways to reach a saved session's file, and which one you want depends on where you are sitting.

- **Reveal** shows the file in the Finder. It only means something when the browser is on the same machine as Hearth, so from a phone or another computer it is refused with a note saying to download instead.
- **Download** hands you the file itself, from anywhere the panel reaches. The file is your conversation — the persona prompt is never in it — so it leaves unchanged and with nothing stripped out.

Both work from a session id and never from a path you type: a session that is not this companion's own is refused, and the refusal says so without naming any location on disk.

## Bringing a session file in

The other direction: pick a session file — one you downloaded from here before, or one from another install of Hearth — and it joins the companion's shelf, ready to resume like any other saved session.

What is checked before it is kept:

- **It has to be this companion's.** A session file names the companion it belongs to, and a conversation is a record of one companion rather than something transferable between them; a file naming a different one is refused. An older file that names none is stamped with the companion you are depositing it into.
- **Its voice has to be one this companion has.** A session remembers which voice bundle was speaking, and a session that resumed into a voice this companion cannot speak would fail at the first turn instead of at the door. The same goes for the persona variant it names.
- **No prompt is ever accepted.** A session file here never carries the persona, and any system message in the file you pick is dropped rather than stored — so a file cannot put words in the companion's mouth on the way in. The answer tells you how many were dropped.
- **It gets a fresh id, and it is held.** The name comes from the moment of the deposit, never from the file, so bringing a file in can only ever add to the shelf and never overwrite a conversation already on it. The result is marked **held**, so it is sticky and no sweep will take it, and it is stamped as brought in rather than born here.

A file that is not readable as a session — or one larger than a session file is allowed to be — is refused with a reason about the file, and the reason never quotes a line of what it says.

## Archiving a session

Archiving is putting a conversation aside, not throwing it away. The file moves into a hidden archive folder that sits beside the companion's sessions, and everything about it stays exactly as it was — same bytes, same name, same conversation.

What changes is what stops happening to it:

- It leaves the **resume picker**. An archived session cannot be resumed, and it will not appear on the shelf you pick from when a sitting starts.
- It leaves the **fresh-start sweep**. The sweep that clears recall-only leftovers never looks in the archive, so a session put aside stays put aside.

What does not change: the file is still yours, still on this machine, still in the companion's own private tree. **Archiving is not a deletion.** Unarchive moves it straight back onto the shelf, and it resumes like it never left. Nothing is ever overwritten either — if a session with the same name is somehow on both sides, the move is refused and told to you rather than silently resolving it.

You can still list, download, and reveal an archived session; the shelf shows the archived ones when you ask for them, and shows the live ones by default.

**The running companion's files are read-only.** While a companion is up, none of its session files can be archived or unarchived — stop the companion first. The reason is honest rather than cautious: the sitting in progress is being written to a file continuously, and Hearth's supervising half cannot know *which* file that is (a fresh sitting names itself inside the companion, after it starts). Fencing off the whole shelf is the only way to be sure the file being written is not the one being moved. Another companion's sessions are unaffected — nothing is holding those.
