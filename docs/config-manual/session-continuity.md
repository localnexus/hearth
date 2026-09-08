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

## Destroying a session

Destroy is the one verb here that takes something away, and the only one that cannot be undone. It exists for the case archiving does not answer: when a conversation should stop existing on this machine — not moved aside, not in a trash folder waiting to be emptied, gone.

Because confidentiality is the whole reason it exists, it is a **sweep and not just an unlink**. One act removes:

- **the session file** — the conversation itself, on the shelf or in the archive;
- **the session's memory record** and every compaction epoch of it — the canonical per-session file under `characters/<c>/memory/records/`;
- **the facts extracted from it**, excised from the memory bank by the same forget the memory pane runs (see [records and curation](../memory/records-and-curation.md)).

The memory goes first. If the bank cannot be updated, nothing is deleted — the file stays, the record stays, and you can run destroy again once the memory lane is back. The alternative ordering would leave you with the conversation gone and everything extracted from it still banked, which is the exact failure this verb exists to prevent. A sitting that banked nothing (`--memory recall-only`, or `off`) has no record, and the answer says so rather than pretending it swept one; a session that lost its file but kept its record can still be finished off.

**What destroy cannot reach**, said the same way every time, in the preview and again in the answer:

- lines in `logs/bot.log` (timings and ids only, never words)
- the model server's prompt cache in memory (cleared by its next restart)
- copies outside Hearth: Time Machine and APFS snapshots, your own backups, mirrors

The first two are Hearth's own and carry nothing you said; the third is not Hearth's to promise, and a verb that quietly implied otherwise would be worse than no verb at all.

**Confirming.** Destroy asks twice. The first call answers a plan — what would go, what cannot be reached — and touches nothing. It also names the exact word to send back: the session's **title** if it has one, its **name** if it has that, its **id** if it has neither — whichever the shelf is showing you. Anything else is refused and nothing is touched. "OK" is not a confirmation; the point of typing the name is that you have looked at which conversation this is.

**Where it is offered.** By default only from a browser on the machine Hearth itself runs on — the irreversible verb stays off the phone unless you say otherwise. Setting `destroy_for_all = true` under `[serve.sessions]` in `config/serve.toml` offers it to everyone who holds the access key, wherever they are; it ships off, and it lands at the next restart of Hearth. (There is no separate audience or role system yet: past the door every caller is equally trusted, so "the operator" means "at this machine" until there is one.)

As with archiving, **the running companion's files are read-only**: stop the companion before destroying one of its sessions.

## Renaming a session

Renaming a conversation almost always means giving it a **title** — a display name you pick, shown on the shelf wherever the session appears. A title is free: you can change it as often as you like, on any session, at any time, and nothing else in Hearth is affected by it. That is because nothing else reads it. The title lives in the session's own file, beside its stamps, and it is the only thing renaming touches — the conversation itself is carried across untouched, and clearing a title leaves the file exactly as it was before it had one.

Titles are what a person types: anything from one character to 120, no line breaks, and leading and trailing spaces trimmed off. Emptying the box removes the title rather than setting a blank one.

**Changing the id is a different act**, and it is only sometimes offered. The id is the session's filename, and it is also the key other parts of Hearth file things under — so renaming the file can leave those pointing at a name nothing answers to any more. Hearth checks before it offers, and the things that count are:

- **a memory record** for the session (and each of its compaction epochs) — and with it the entry in the memory bank, which is filed under the same id;
- **a queued compaction** naming the session — one waiting to run, running, or one that failed and is still on the queue;
- **the hold marker**, when it is holding this session by name.

If none of those knows the session, the id can be changed and the file simply moves. If any of them does, the rename is refused and Hearth tells you *what* knows it — not as a warning to click past, but as the answer: give the session a title instead, which is the rename that was wanted nearly every time anyway. (A conversation whose memory has been forgotten, or one that never banked any, is free to be renamed outright.)

Things that do *not* count: a line in the log, the panel's own view of the moment, a copy of the conversation you exported somewhere else. Those record what was true when they were written; none of them goes looking for the file later.

As with archiving and destroying, **the running companion's files are read-only** — including their titles. Stop the companion first.

## The Sessions card

Everything above has a button. The launch page (`/admin/launch`) carries a **Sessions** card beside the Conversation picker — the same shelf seen from the other side: the picker chooses which conversation to resume, the card decides which conversations there are. It follows whichever companion is selected, and reloads itself after a start or a stop, because what it may offer changes with them.

One row per saved conversation: the word it goes by (its title, else the name it was held under, else its id), its turns, when it was last written, its memory posture, and whether it is held, archived, or brought in. A **show archived** box adds the archived ones. Then the verbs:

- **Reveal** shows the file in the file manager — and disappears from every row the first time it is refused, because that refusal means this browser is not on the machine Hearth runs on (or the machine is not a Mac). Download is what remains, and it is enough.
- **Download** hands you the file. It is not an ordinary link: a link cannot carry the access key, so the card fetches the file with the key as a header and saves what comes back.
- **Archive** / **Unarchive** put a conversation aside and bring it back.
- **Rename** asks for a title. Changing the **id** is a separate small link, `change id…`, because it is the act that is only sometimes allowed: if anything still knows the session by that id, the answer lists what does, in Hearth's own words, and the title is what you wanted anyway.
- **Destroy** is red and asks twice. The first press shows the plan — the file, how many memory records go with it, and what destroy cannot reach — and a box that will only accept **the word the row is showing you**: its title, its name, or its id, whichever the shelf displays. The second press does it. Where destroy is not offered, the first press says so and the button leaves every row for the rest of the visit.

At the foot of the card, **Bring in** takes a session file from this device and deposits it on the shelf; the answer says how many turns arrived and how many prompt messages were dropped on the way in, and a file that is refused is refused in a sentence about the file.

A **recall-only** conversation is the privacy tier's own case: it is not offered load or rename, so **Destroy is the only verb on its row**.

While the companion is up its whole shelf is read-only, and the card says so once at the top rather than once per refused press.
