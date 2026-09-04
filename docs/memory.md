# Memory — cross-session continuity, a backend per companion

Hearth companions can remember earlier conversations. The design is a **seam**,
not an integration: the engine has three hooks (recall at session start, store +
consolidate at session end), and what sits behind them is a **backend chosen per
companion** in `config/memory.toml`. Base Hearth ships complete without any of
it — the gate file absent means nothing loads and the composed prompt is
byte-identical.

## Enabling

```bash
cp config/memory.toml.example config/memory.toml
# edit: enabled = true   (backend = "floor" works on a base install)
```

At the next session start you'll see `[memory] seam attached …` in the log, and
on a graceful stop `[memory] record kept (<session>)`.

## What it does

* **Session start** — the seam asks the companion's backend for up to
  `recall_limit` memories and appends them to the system prompt as a dated,
  clearly-framed block ("You remember these things from your own earlier
  conversations…"). Every line carries provenance (date + source session);
  the framing tells the model that not recalling is better than inventing.
  This happens once, off the per-turn path — the voice loop never waits on
  memory. The prompt fingerprint (drift detection, resume warnings) is
  computed memory-free, so it stays stable.
* **Session end (graceful stop)** — the seam writes the **canonical memory
  record** (`characters/<c>/memory/records/<session>.json`: the conversation's
  messages + metadata) and then lets the backend index it. Extraction cost
  lives here, never mid-conversation.
* **Failure containment** — any backend failure degrades: recall falls back to
  the floor, then to nothing; store/consolidate log and drop. Memory absent
  means "the companion doesn't recall", never "session down".

## Privacy — read this before enabling

Enabling memory **is** the choice to keep a trace. Canonical records contain the
conversation's messages, and they persist independently of the session
transcript's own lifecycle (deleting a saved session does not touch the bank,
and vice versa). They are 0600 files in a 0700
directory under your data root, gitignored, local-only — the same sensitivity
class as held sessions. Per-companion opt-out: map that companion to `"none"`.
And the choice is revocable one conversation at a time — see
[Forgetting one conversation](memory/records-and-curation.md#forgetting-one-conversation).


## The rest of this page

Memory's detail lives in [`memory/`](memory/), one page per concern:

| Page | What it covers |
|---|---|
| [Records, forking, and forgetting](memory/records-and-curation.md) | The record is the truth; backends are indexes · Forking the track at a juncture · Forgetting one conversation |
| [Session mode and boot recall](memory/session-modes.md) | Per-session memory mode (`--memory`) · Intent-primed boot recall |
| [Per-turn targeted recall](memory/per-turn-recall.md) | Recall re-asked on the user's own words, every turn |
| [The serve facade lane](memory/serve-facade-lane.md) | Sessions for the `/v1` door · client-declared companions |
| [Backends](memory/backends.md) | The floor · Hindsight setup (sidecar topology) and its notes |

## Not built yet (deliberate)

An idle-time consolidate trigger; a "what the companion remembers" panel page with
accept/edit/discard cards for extracted facts. The seam's contracts already leave
room for both.

(Mid-session topic-shift recall — pulling up facts about a subject the opening
didn't anticipate — *did* ship, 2026-09-01: see
[Per-turn targeted recall](memory/per-turn-recall.md). It re-queries on the cue directly, without a separate drift detector.)
