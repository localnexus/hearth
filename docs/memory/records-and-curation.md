# Memory — records, forking, and forgetting

> Part of [Memory](../memory.md) — cross-session continuity, a backend per companion.

The substrate and the two verbs that act on it.

## The record is the truth; backends are indexes

Records are Hearth's own format and outlive any backend. Every backend must be
rebuildable by replaying them:

```bash
python -m hearth.memory records                 # list a companion's records (metadata only)
python -m hearth.memory rebuild                 # replay all records into the active backend
python -m hearth.memory forget --session <id>   # delete ONE conversation everywhere (previews first)
python -m hearth.memory rebuild --clean --yes   # wipe the index, replay the surviving records
python -m hearth.memory fork --as <name> --until <when>   # branch the track at a juncture
```

Every verb defaults to the active companion (whichever `active.toml` currently
selects); pass `--character <name>` to point any of them at another one.

That's also how you **switch backends mid-relationship** (edit memory.toml,
rebuild — the new backend inherits the whole history instead of starting
amnesiac) and how an A/B between candidates stays fair (each contender indexes
the same records).

## Forking the track at a juncture

A memory track can **branch**: one line pursues a new path while the original
proceeds down its own route. `fork --as <name> --until <when>` creates a new
character (persona *as it stands today*, plus every voice bundle and the theme)
whose records hold a copy of the shared history up to the juncture — selected
by each record's `ended` timestamp (falling back to `started` when a record
was never closed), never its filename — restamped with the new name and a
`forked_from` provenance key, enrolled at the source's memory tier, and (on an
indexed backend) replayed into its own bank. Banks key on companion name, so
the two tracks can never recall across each other. `--as` must name a
character that doesn't exist yet — fork is create-only, like every other
identity write. Without `--yes` the same command previews the whole plan and
touches nothing.

`--until` takes an ISO date or timestamp; a bare date (`2026-08-30`) is
inclusive of its whole day, so a record that closed at 23:30 that day still
counts as at-or-before the juncture — give a timestamp when you need finer
than a day.

Three things deliberately stay behind: records *after* the juncture, the
consume-once intent slot (it belongs to the track that stated it), and held
transcripts — pass `--include-sessions` when you want those resumable in both
branches. (A fourth class simply can't be placed: a record with no `ended` or
`started` timestamp has no juncture to compare against, so it stays with the
source too — the preview counts these separately and says so.) The persona is
today's text: personas evolve in place, so "as it stood at the juncture" is
your edit after the fork, not the verb's guess. A replayed bank is a faithful
*re-reading* of the shared history, not a byte-identical copy of the source's
index — same records, freshly extracted.

The same verb is reachable from the roster page when the supervisor is
mounted (`POST /admin/roster/fork`, with a Branch card on `/admin/roster`
that picks the juncture from the record listing) — identical plan/execute,
except the replay itself stays a desk command, for the same reason
`rebuild --clean` does: it runs the extraction model over every record.

## Forgetting one conversation

There is an undo for a banked conversation. `forget --session <id>` (the id as
`records` lists it) previews the record's date, name and digest — without
`--yes` it touches nothing. Confirmed, it removes the session from both layers:

* **the backend index** first: sessions are stored *keyed* (each is one
  document in the bank), so a keyed backend cascade-deletes exactly that
  session's facts on the spot. The floor needs no index step at all — it reads
  the record files directly.
* **the record file** second, and only if the index step succeeded — a failed
  backend call keeps the record, so the verb is safely re-runnable. The
  deletion is a true-delete: an archive step would retain what you asked gone.

**Banks written before session-keyed storing** hold facts the server cannot
attribute to a session. `forget` still deletes the record, says so plainly,
and points at the fix: one `rebuild --clean` (wipe the index, replay the
surviving records through the keyed store) migrates the whole bank — after
that, every future `forget` is complete on its own. Granularity is the
session, by design: "forget just this one sentence" is record *editing*, a
different tool.

The same capability is reachable over the serve facade when the supervisor is
mounted: `GET /admin/memory` (per-companion record counts), `GET
/admin/memory/records?character=<c>` (the digest listing), and `POST
/admin/memory/forget {character, session, yes?}` — the identical
preview-then-confirm contract and deletion ordering, behind the facade's
bearer door (`character` is required there; the web has no "active companion"
in view). `rebuild --clean` stays CLI-only: a wipe-then-replay runs the
extraction model over every record and belongs at the desk, not on a request
timeout.

For a browser, `GET /admin/memory/ui` serves the **review-and-prune pane** —
a static shell (auth-exempt like the launch and roster pages; every fact
arrives via the authed routes) that renders the companions with their record
counts, each companion's record digests, and the bank's indexed-fact count
(`GET /admin/memory/facts?character=<c>` — fetched once per selection, since
a count is a real backend call), with the same preview-then-confirm forget
per record. The control panel's Memory status line links over to it.
