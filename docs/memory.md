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
  means "she doesn't recall", never "session down".

## The record is the truth; backends are indexes

Records are Hearth's own format and outlive any backend. Every backend must be
rebuildable by replaying them:

```bash
python -m hearth.memory records            # list a companion's records (metadata only)
python -m hearth.memory rebuild            # replay all records into the active backend
```

That's also how you **switch backends mid-relationship** (edit memory.toml,
rebuild — the new backend inherits the whole history instead of starting
amnesiac), how an A/B between candidates stays fair, and how forgetting works:
delete the record file, rebuild, and the session is gone from every layer.

## Privacy — read this before enabling

Enabling memory **is** the choice to keep a trace. Canonical records contain the
conversation's messages, and they persist even though the session file itself is
ephemeral-by-default and deletes on graceful stop. They are 0600 files in a 0700
directory under your data root, gitignored, local-only — the same sensitivity
class as held sessions. Per-companion opt-out: map that companion to `"none"`.

## Per-session memory mode (`--memory`)

Enrollment says whether a companion *has* memory; the per-session mode says
what **this sitting** does with it. Recall (reading the bank) and retention
(writing it) are independent operations, so the launch flag offers three
postures:

```bash
./start.sh                         # full (default): recall + retain, as configured
./start.sh --memory recall-only    # she remembers everything — this sitting adds nothing to the bank
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

**The mode governs the memory bank only.** The session transcript keeps its own
lifecycle (ephemeral-by-default, `--hold` to keep — see the
[runbook](runbook/03.5-session-continuity.md)). `--memory recall-only` plus
`./stop.sh --hold` is coherent: transcript kept, bank untouched.

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
  agreed to pick up the tea ceremony next time."* She opens aware of the plan.
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

## Per-turn targeted recall

Boot recall (above) queries the bank *once*, at session start. Per-turn recall
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

## The serve facade lane

The `/v1` facade (`config/serve.toml`) is stateless by construction: it resolves
identity once and re-composes `[system] + client turns` on every request, so it
has no session start and no session end — the two anchors the seam needs. Turn
this on and a small in-process **session table** supplies them, and the phone
lane and chat clients get the same continuity the voice appliance has.

Off by default. Absent or disabled, the facade is byte-identical.

```toml
[memory.serve]
enabled = true          # default false
idle_close_voice = 5    # minutes of silence before a voice conversation closes
idle_close_chat = 480   # minutes — the FALLBACK behind deliberate-closure close
checkpoint = true       # snapshot after each exchange so a crash is recoverable
```

**A session is one conversation**, keyed `(character, channel, session-hint)`:

* **character** — who is answering (see *Client-declared companions* below);
* **channel** — the `X-Hearth-Channel` header, `chat` or `voice`, default `chat`;
* **session-hint** — the optional `X-Hearth-Session` header, which lets a client
  that runs several threads at once subdivide its channel. Clients that send
  none degrade to one conversation per channel. The value is sanitized before it
  reaches a filename: anything outside `[A-Za-z0-9._-]{1,64}` is replaced by a
  short digest of itself.

**What each anchor does.** On a conversation's first request the seam recalls,
and the augmented instruction is cached on the session entry — every later turn
of that conversation costs a dict lookup, not a recall. Turns are accumulated
**facade-side, verbatim**: the final request's message list is not a faithful
transcript, because a voice client windows its own history. At close the turns
become a standard record — `session_id = serve-<channel>[-<hint>]-<started>`,
`name = "facade <channel>"` — which every backend, `rebuild`, and the archive
pool then consume with no changes anywhere.

**Four ways a conversation ends**, so a record exists however it finishes:

1. **Deliberate closure** (chat, the primary path). After a reply, a cheap
   filter on the user's line — short, not a question, not the opening exchange —
   decides whether to ask the extraction model whether that was a goodbye. It
   was ⇒ the conversation closes at once, and its record and intent slot land
   immediately rather than hours later. If a newer turn arrived while the model
   was answering, the close is skipped: the conversation continued.
2. **Idle sweep.** A background pass closes voice conversations after
   `idle_close_voice` and chat conversations after `idle_close_chat`. Voice's 5
   minutes is a transport fact; chat's default 8 hours sits above the longest
   plausible waking gap, so an errand never splits a day's thread in two.
3. **Facade shutdown.** Every open conversation is closed gracefully first, so
   stopping the service writes records rather than leaving orphans.
4. **Orphan finalization.** With `checkpoint = true` an open session snapshots
   after every exchange to
   `characters/<c>/memory/checkpoints/serve-<channel>[-<hint>].json` (0600, the
   same atomic write as the records). If the process dies, the next start turns
   each leftover checkpoint into a record — stamped with the checkpoint's own
   mtime, so `ended` is when the facade died, not when it came back — and then
   removes it. Checkpoints are transient: the record is the durable artifact.

Every step is contained. A recall failure means the base instruction; a
checkpoint failure is logged; a close failure leaves the checkpoint for the next
start. Memory absent must mean "she doesn't recall", never "the conversation
dropped".

### Client-declared companions

Memory attribution follows identity, so *who* the facade answers as decides
whose memory a conversation becomes. `[serve.characters]` in `serve.toml` lists
the companions a client may ask for and the voice bundle each one speaks with:

```toml
[serve.characters]
# name = "voice-bundle-name"
```

Listed names join the resolved identity in `GET /v1/models`, so a client picks
one from a roster. When a chat request's `model` field names a real character on
this machine, that companion answers — and the record files under that
companion. Anything else falls back to the identity `[serve.identity]` (or
`active.toml`) resolved at start, exactly as before. The speech route follows
the same roster: a request naming a listed character gets that character's
bundle; every other request keeps the pinned voice untouched.

Note that this widens who a bearer-token holder can talk to: any listed
companion. The facade is loopback-only by default, and the roster is exactly as
long as you make it.

### Notes

* A conversation still open when the archive run fires is archived on the next
  pass instead — records exist only at close.
* Appliance-internal calls (`X-Hearth-Internal: task`, e.g. a rolling
  summarizer) bypass memory entirely, as they already bypass persona injection
  and the transcript tap.
* Close-time work — record, index, consolidate, intent capture — runs on one
  dedicated worker thread, never on the request path. A turn pays a dict lookup.

## Backends

| backend | needs | recall |
|---|---|---|
| `floor` | nothing (ships with base) | dated digest of the last N conversations (recency, not semantic — deterministic, no LLM) |
| `hindsight` | client extra + a sidecar venv + a local extraction model (setup below) | semantic recall over typed, dated facts extracted at session end |

### Hindsight setup — sidecar topology

Hindsight's server closure needs `protobuf>=7`; pipecat pins `protobuf<7`. They can
**never share a venv**, so the split is: the engine gets only the featherweight client
SDK, and the server runs as a **sidecar process from its own venv**, spawned and
terminated by the adapter (same UX as embedded — session start boots it, close stops it).

```bash
# 1. engine venv — the client SDK only (aiohttp/pydantic, no protobuf):
uv pip install -e ".[mac,memory-hindsight]"

# 2. sidecar venv — the server (~1.4 GB closure), anywhere outside the engine venv:
uv venv -p 3.11 /path/to/hindsight-sidecar/.venv
uv pip install --python /path/to/hindsight-sidecar/.venv/bin/python 'hindsight-all==0.9.2'

# 3. memory.toml:
#    [memory.companions]  <name> = "hindsight"
#    [memory.hindsight]   python = "/path/to/hindsight-sidecar/.venv/bin/python"
#                         llm_model = "<local extraction model>"

# 4. inherit the archive:
python -m hearth.memory rebuild --character <name>
```

`mode = "embedded"` (in-process import) remains for non-pipecat hosts.

### Hindsight notes

Run-verified 2026-08-29/30 against fully local models. Be aware:

* **Embedded ≠ free**: it starts a bundled PostgreSQL (~15 processes) on fixed
  port **5432** with data under `~/.pg0`. One instance per machine — a second
  concurrently running bot shares it (per-companion banks stay isolated) but
  can't start its own.
* **Startup cost**: ~5–14 s warm, paid once at session start.
* **Egress**: the engine already sets `HF_HUB_OFFLINE=1`; the adapter sets
  `LITELLM_LOCAL_MODEL_COST_MAP=True`. With both, the stack is verified
  zero-egress. The **first-ever run** must fetch the embedding/reranker models
  once: `HF_HUB_OFFLINE=0 ./start.sh`, then never again.
* **Session-end latency**: extraction on a 30B-class local model takes a few
  seconds at stop; `retain_max_chars` bounds it.
* **Recent-boost (the last-session slot)**: recall is a *single* top-K semantic
  query at session open, so a fact retained only minutes ago can rank below the
  cut and never reach the companion at the next boot. `recent_boost` (default
  **3**, `0` = off) appends that many of the **newest valid facts** past the
  semantic ranking — deduped against what recall already surfaced. Contained: a
  failed boost costs nothing but itself, and the semantic recall stands.
* `consolidate` is a no-op this pass — Hindsight's `reflect` wants a real idle
  trigger, which the engine doesn't have yet.

#### The sidecar logfile

The sidecar is a **child process**, and its own output is the only place its
failures are readable. Everything it writes to stdout (after the
`HINDSIGHT_URL=` handshake line, drained for the process's whole life so the
pipe can never fill and stall the server) and stderr is appended to:

```toml
[memory.hindsight]
# log_file = "/path/to/hindsight-sidecar.log"
```

Unset, it defaults to **`<data root>/logs/hindsight-sidecar.log`** — `HEARTH_DATA`
if you set it, otherwise the engine tree, the same root the canonical records
live under. The file is `0600` in a `0700` directory (records' discipline: it
carries extraction chatter about your conversations), and at each sidecar start
a file past ~5 MB is renamed to `<name>.1`, replacing any previous `.1`. The
resolved path is logged once at start: `[memory] hindsight sidecar log → …`.

#### The env passthrough (`HINDSIGHT_API_*`)

`[memory.hindsight.env]` is a straight passthrough to the sidecar server's own
environment. Every key is applied with `setdefault` just before the child is
spawned, so anything you already export in your shell **wins** — the block only
fills in what you haven't. This is where the sidecar's own **`HINDSIGHT_API_*`**
knobs live: e.g. `HINDSIGHT_API_LLM_BASE_URL` points its extraction model at a
specific local endpoint. Values are never printed. See the block in
`config/memory.toml.example`.

```toml
[memory.hindsight.env]
# HINDSIGHT_API_LLM_BASE_URL = "http://127.0.0.1:11434"
```

#### If the sidecar dies mid-session

It can. On the next recall/store the adapter notices the dead child
(`[memory] hindsight sidecar died (rc=N) — respawning`), retires the stale
client, and starts a fresh sidecar — **once per call**. A second immediate
death is handed up to the seam, which degrades to the floor rather than
stalling the session; the reason will be in the logfile above. A session that
ends after a death logs `sidecar had already exited (rc=N)` instead of
terminating a process that isn't there.

#### pg0 outlives the sidecar (accepted)

Hindsight's bundled PostgreSQL (`pg0`) is started by the sidecar but is **not**
its child in the lifecycle sense: after an unclean sidecar exit, pg0 keeps
running, and the next sidecar simply **reuses the warm instance** (a nice side
effect: no cold-init pause). Bank data lives in `~/.pg0` and is unaffected —
nothing is lost when the python process dies. This is accepted behaviour, not a
leak to fix: `pg0` shutdown is owned by hindsight's own stop path, which runs
when the sidecar exits cleanly (SIGTERM at session close). If you ever need it
gone by hand, stop it yourself; Hearth deliberately does not reach into it.

## Not built yet (deliberate)

An idle-time consolidate trigger; a "what she remembers" panel page with
accept/edit/discard cards for extracted facts. The seam's contracts already leave
room for both.

(Mid-session topic-shift recall — pulling up facts about a subject the opening
didn't anticipate — *did* ship, 2026-09-01: see **Per-turn targeted recall**
above. It re-queries on the cue directly, without a separate drift detector.)
