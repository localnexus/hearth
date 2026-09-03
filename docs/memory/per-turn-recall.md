# Memory — per-turn targeted recall

> Part of [Memory](../memory.md) — cross-session continuity, a backend per companion.

Recall that re-asks the bank on the user's own words, mid-conversation.

## Per-turn targeted recall

Boot recall ([intent-primed](session-modes.md#intent-primed-boot-recall))
queries the bank *once*, at session start. Per-turn recall
re-asks it **on every turn**, with the user's own latest words as the query, and
appends whatever new surfaces to that turn's memory block — so a mid-conversation
topic the opening didn't anticipate can still pull the relevant facts up. Off by
default; it ships behind `[memory.per_turn]` and runs only where the whole seam
is already on.

```toml
[memory.per_turn]
enabled = false         # default; on ⇒ chat-lane per-turn recall
limit = 3               # targeted extras appended past the open-time block
min_cue_chars = 12      # skip cues shorter than this (bare greetings/closes)
voice = false           # ALSO run the voice loop (prefetch-behind); needs enabled = true
```

The re-query itself is `augment_turn`: it recalls against the cue, **dedupes the
result against the open-time block** (nothing already recalled repeats), keeps at
most `limit` genuinely-new items, and lists them under their own labeled line —
*"Also surfaced by what the user just said…"* — inside the same MEMORY block. The
open-time lines and any intent line stay exactly as they were; the intent slot is
**not** touched here (it is consumed once, at boot). Every guard and every failure
falls back to the byte-identical open-time composition — gate off, a cue below
`min_cue_chars`, or nothing new surfaced all yield the same block boot recall
produced. Containment is the seam's usual ladder: backend → floor → empty.

The feature has two lanes, one per surface:

* **Chat lane (synchronous).** In the serve facade's session glue
  (`_turn_instruction`), the re-query runs **in-line** on the close-worker thread
  under a hard deadline (`PER_TURN_DEADLINE_S`, 5 s); a one-slot cache short-circuits
  an identical repeated cue. Chat only, deliberately — a synchronous recall on the
  voice path would tax first-token latency every turn. A recall that raises, times
  out, or is superseded costs the turn nothing: the cached open-time instruction
  serves.
* **Voice lane (prefetch-behind).** Set `voice = true` (needs `enabled = true`
  too — the chat gate alone stays chat-only) and the voice loop gets a
  latency-free variant: after the user's turn *N* is transcribed, its recall runs
  **in the background, off the event loop**; the extras it finds are injected into
  the system instruction **before turn *N+1***. Zero added latency, a one-turn lag —
  *ask, she checks, next turn she knows*. The background recall carries the same 5 s
  deadline (`PREFETCH_DEADLINE_S`) and is discarded if a newer turn or a live
  companion switch supersedes it. Synchronous voice recall is rejected outright by
  the latency doctrine (~0.3 s before first-token, every turn).

Cost is one embedding-search recall per qualifying turn (no LLM, no extraction).
On the `floor` backend, which ignores the query, the re-query simply returns the
same recency digest and dedupes away against the open block — the gate is a no-op
there rather than a cost.

The extras themselves cost **context** every turn they ride, and that adds up in
a long sitting. So the voice lane has a live valve: in a sitting that started
with `voice = true`, the control panel's Memory line shows a **pause/resume
voice recall** button (`POST /memory/per-turn-voice`). It is a runtime-only
poke — effect next turn, already-applied extras cleared, nothing written; this
file stays the between-sessions truth and a restart or live switch returns to
it. A sitting that started voice-off has no prefetch processor to light, and
the route says so instead of pretending.
