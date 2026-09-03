# Memory — the serve facade lane

> Part of [Memory](../memory.md) — cross-session continuity, a backend per companion.

Session anchors for a door that never closes: how the `/v1` facade gets a session start and a graceful close.

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
