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

## Not built yet (deliberate)

An idle-time consolidate trigger; a "what she remembers" panel page with
accept/edit/discard cards for extracted facts; topic-shift recall mid-session.
The seam's contracts already leave room for all three.
