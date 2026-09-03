# Memory — backends

> Part of [Memory](../memory.md) — cross-session continuity, a backend per companion.

The floor, Hindsight, and what setting one up actually involves.

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
* **Keyed store**: each session is retained as one bank document
  (`document_id` = the session id, replace-on-update, timestamped with the
  session's real end). Re-ending a *resumed* session therefore updates its
  document instead of re-extracting every fact additively, and per-session
  `forget` can cascade-delete precisely. Banks indexed before this existed
  need one `rebuild --clean` to become keyed.
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
