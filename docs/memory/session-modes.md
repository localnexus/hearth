# Memory — session mode and intent-primed boot recall

> Part of [Memory](../memory.md) — cross-session continuity, a backend per companion.

What a single sitting does with the bank, and what the last one leaves for the next.

## Per-session memory mode (`--memory`)

Enrollment says whether a companion *has* memory; the per-session mode says
what **this sitting** does with it. Recall (reading the bank) and retention
(writing it) are independent operations, so the launch flag offers three
postures:

```bash
./start.sh                         # full (default): recall + retain, as configured
./start.sh --memory recall-only    # they remember everything — this sitting adds nothing to the bank
./start.sh --memory off            # no recall, no retention: a fresh meeting
```

* **full** — today's behavior, unchanged. Flag absent means full (or a resumed
  session's own saved mode — see the stamp below).
* **recall-only** — recall runs exactly as configured (the open-time block,
  per-turn targeted recall, an intent line), but at session end **nothing is
  retained**: no canonical record, no backend index or consolidate, no intent
  capture. The shutdown log says `recall-only session — nothing retained`, so
  suppression is never mistakable for a memory failure. An injected intent
  line is *peeked, not consumed* — the plan survives for the next full session.
* **off** — the seam is not attached at all: no recall block, no per-turn
  extras, nothing written. Like mapping the companion to `"none"`, but for one
  sitting instead of forever.

**The mode governs the memory bank.** The session transcript keeps its own
lifecycle (saved-by-default — see the
[runbook](runbook/03.5-session-continuity.md)), with ONE default keyed off the
mode: a `recall-only` sitting's transcript is deleted on graceful stop unless
held, so the privacy tier truly leaves no durable record by default.
`--memory recall-only` plus `./stop.sh --hold` is still coherent: transcript
kept, bank untouched.

**Crash safety — the stamp.** A non-full sitting stamps its mode into the
session file. If the sitting dies uncleanly, the orphan carries the stamp and a
later `--resume` *without* the flag inherits it (announced at startup) — a
recall-only conversation cannot get banked just because the resume forgot the
flag. An explicit `--memory` always wins and re-stamps.

**Live companion switch.** The mode is the sitting's posture, not the
companion's: a live switch attaches the incoming companion under the same mode,
and the outgoing companion's session-end honors its own. Resuming a session
(via the switch) that was saved under a different mode warns — the sitting's
mode wins.

**Boundaries.** A restart-path switch spawns a new bot process — that is a new
sitting, back to the default (or to the resumed session's stamp). The serve
facade's conversations are separate sittings; this flag does not govern them.

**Seeing it.** The control panel's `Misc` line shows the sitting's effective
posture (`Memory: full | recall-only | off`; a `—` means memory isn't
configured at all), so mid-conversation you never have to wonder whether the
sitting retains.

## Intent-primed boot recall

Off by default. Enabled, it makes *"next time, let's talk about X"* actually
land next time:

* **At session close** — after the canonical record is safely written, the seam
  makes **one** call to the local extraction model over the tail of the
  transcript, and that call answers two questions: *did the user deliberately
  end this conversation?* and *did they explicitly state what they want to
  discuss next session?* A stated topic goes into a one-line **intent slot**
  whether or not the user said goodbye — a plan named mid-conversation is still
  the plan. With no topic stated, nothing is kept, whether the conversation was
  deliberately closed or merely trailed off — a stop is not a plan.
* **At the next session start** — the recall query becomes the standing
  `recall_query` **plus** the stated topic (semantic backends surface material
  *about* it), and the memory block gains a dated line: *"On 2026-08-30 you
  agreed to pick up the tea ceremony next time."* The companion opens aware of the plan.
* **Consume-once** — the slot is deleted the moment it has been injected. A
  plan that re-asserts itself for weeks is worse than no plan. An expiry
  backstop (`expiry_days`, default 14) clears one that was never used. (One
  exception: a `--memory recall-only` sitting injects the line but leaves the
  slot in place — "one use" means one *retaining* use.)

This works on **every** backend, the floor included: the capture call goes to
the extraction model directly from the seam, and the injected line doesn't
depend on recall at all.

```toml
[memory.intent]
enabled = true          # default false — absent, nothing changes and no LLM is called
expiry_days = 14        # skip + clear a slot older than this (0 = no expiry)
# llm_provider = "ollama"        # only "ollama" is implemented; falls back to
# llm_model = "qwen3-coder:30b"  # [memory.hindsight]'s provider/model when absent
# llm_url = "http://127.0.0.1:11434"

[memory.intent.companions]
# example = true            # per-companion override of `enabled`, the house pattern
# guest = false
```

**Capture is deliberately conservative.** A wrongly-inferred plan asserted at
boot is a confident wrong memory — the exact failure the dated, provenance-first
framing exists to prevent. So the prompt demands an explicit statement, the
parser rejects anything that isn't a short topic, and every doubt resolves to
"no slot". Missing an intent costs a hint; inventing one costs trust.

**Privacy.** The slot (`characters/<c>/memory/intent.json`) holds the stated
topic, a timestamp, and the source session id — 0600 in the same 0700 tree as
the records, gitignored, local-only, and deleted on first use. It is a sidecar,
not substrate: losing it loses one hint, never a memory, and it takes no part in
`rebuild`. Capture calls the same local model the extraction lane already uses —
nothing leaves the machine.
