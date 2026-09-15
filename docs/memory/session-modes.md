# Memory — session mode and intent-primed boot recall

> Part of [Memory](../memory.md) — cross-session continuity, a backend per companion.

What a single sitting does with the bank, and what the last one leaves for the next.

## The two switches

Two independent switches govern one sitting: **remember the past** (recall — do
they open aware of earlier conversations?) and **keep this conversation**
(retain — does what's said this sitting get added to what they remember?).
They bind at different moments: recall at session **start**, retain at session
**close**.

|  | keep this conversation: **off** | keep this conversation: **on** |
|---|---|---|
| remember the past: **on** | the default — they remember everything, this sitting adds nothing to the shelf | remembers, and this sitting joins what they remember |
| remember the past: **off** | a fresh meeting — no memory in, nothing kept | a first meeting you want remembered afterwards |

```bash
./start.sh                              # default: remember the past, don't keep this one
./start.sh --keep                       # remember the past, and keep this conversation too
./start.sh --keep --keep-name <label>   # keep it, and give it a name
./start.sh --no-recall                  # a fresh meeting — no memory in
./start.sh --no-recall --keep           # a fresh meeting you want remembered afterwards
```

`--memory full|recall-only|off` is the older, one-word form of the same two
switches, kept for one release: `full` is `--keep`, `recall-only` is the
default (recall on, nothing kept), `off` is `--no-recall`.

**The close rule.** An unkept conversation's working file is deleted at a
graceful stop — nothing was asked to be remembered, so nothing durable is left
behind. A kept conversation goes on the shelf: the transcript stays, and what
was said is folded into what the companion remembers.

**The fork rule.** A kept conversation is immutable to sittings: resuming it
copies it into a fresh working file (a fork) rather than writing the original
in place, so the sitting you are having can never corrupt the one you resumed
from. Keep that fork and the keep replaces the original — the older file steps
aside for the newer one. A **watermark** travels with the fork (how much of the
conversation was already remembered before it began), so keeping it later only
adds what's new to what the companion remembers, never doubling up.

**The quarantine.** An unkept conversation that an unclean death leaves behind
is never swept on sight — it waits on the launch page for a week, offered as
keep or delete, then is quietly removed. A start is never blocked by one
waiting.

**Crash safety — the stamp.** A sitting that isn't keeping this conversation
stamps that onto the session file. If the sitting dies uncleanly, the orphan
carries the stamp and a later `--resume` *without* the flag inherits it
(announced at startup) — a conversation that wasn't being kept cannot get
kept just because the resume forgot the flag. An explicit `--keep` or
`--no-recall` always wins and re-stamps.

**Live companion switch.** The switches are the sitting's posture, not the
companion's: a live switch attaches the incoming companion under the same
posture, and the outgoing companion's session-end honors its own. Resuming a
session (via the switch) that was kept under a different posture warns — the
sitting's posture wins.

**Boundaries.** A restart-path switch spawns a new companion process — that is
a new sitting, back to the default (or to the resumed session's stamp). The
serve Hearth's conversations are separate sittings; these flags do not govern
them.

**Seeing it.** The control panel's `Misc` line shows both switches for the
running sitting (`Remembering: on | off`, `Keeping: on | off`; a `—` means
memory isn't configured at all), so mid-conversation you never have to wonder
which way either one is set.

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
  exception: the read lane injects the line unconditionally, but the write
  lane is what consumes it, and only at close — a sitting not keeping this
  conversation injects the line but leaves the slot in place, so "one use"
  means one *retaining* use.)

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
topic, a timestamp, and the source session id — readable only by you (`0600`) in the same `0700` tree as
the records, gitignored, local-only, and deleted on first use. It is a sidecar,
not substrate: losing it loses one hint, never a memory, and it takes no part in
`rebuild`. Capture calls the same local model the extraction lane already uses —
nothing leaves the machine.
