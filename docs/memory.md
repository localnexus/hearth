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

## Intent-primed boot recall

Off by default. Enabled, it makes *"next time, let's talk about X"* actually
land next time:

* **At session close** — after the canonical record is safely written, the seam
  asks the local extraction model **one** question over the tail of the
  transcript: *did the user explicitly state what they want to discuss next
  session?* An explicit answer goes into a one-line **intent slot**; anything
  else answers `none` and nothing is kept.
* **At the next session start** — the recall query becomes the standing
  `recall_query` **plus** the stated topic (semantic backends surface material
  *about* it), and the memory block gains a dated line: *"On 2026-08-30 you
  agreed to pick up the tea ceremony next time."* She opens aware of the plan.
* **Consume-once** — the slot is deleted the moment it has been injected. A
  plan that re-asserts itself for weeks is worse than no plan. An expiry
  backstop (`expiry_days`, default 14) clears one that was never used.

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
# ani = true            # per-companion override of `enabled`, the house pattern
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

Run-verified (survey §5b, 2026-08-29/30) against fully local models. Be aware:

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
accept/edit/discard cards for extracted facts; topic-shift recall mid-session
(the sibling of intent-primed recall — same query composition, plus a drift
detector the engine doesn't have yet).
The seam's contracts already leave room for all three.
