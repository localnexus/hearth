# RUNBOOK — bring the fully-local voice loop online / offline

**Operational procedure for `bot.py` (the conversational loop).** This is the switch-on / switch-off drill — *how to run it*, nothing more. To **tune** knobs (voice, VAD, persona, STT) see the [config manual](../config-manual/README.md); to **debug** a wedged/silent pipeline see the [debugging](../debugging/README.md) notes.

> **TTS is in-process** (Chatterbox-Turbo via `mlx-audio`) — there is no separate TTS app to run. This removes audio echo and a multi-second latency floor.

This dir: the tree you are reading from — **no absolute path is assumed**. `cd` to it and the commands in these chapters work from any account or location. `$UV` = whatever `command -v uv` resolves (`start.sh` resolves it the same way; override with `UV=/path/to/uv`).

> **This runbook is a directory** — one self-contained chapter per numbered section. This `README.md` is the introduction + authoritative chapter index.

---

## The lifecycle in one glance

The loop goes **online → (control) → offline**, with an opt-in memory tier and a recovery lane:

- **Depends on** — what must already be up/granted before anything: **§0** ([`00-dependencies.md`](00-dependencies.md)).
- **Bring online** — preflight checks **§1** ([`01-preflight.md`](01-preflight.md)) → launch **§2** ([`02-launch.md`](02-launch.md)).
- **Control while up** — the `:65000` panel & live status **§2.5** ([`02.5-control-panel.md`](02.5-control-panel.md)).
- **Bring offline** — normal stop **§3** ([`03-stop.md`](03-stop.md)) → full teardown / reclaim memory **§4** ([`04-teardown.md`](04-teardown.md)).
- **Continuity** — resume a conversation across a restart **§3.5** ([`03.5-session-continuity.md`](03.5-session-continuity.md)).
- **Recovery** — runtime symptom → one-action fix **§5** ([`05-fast-recovery.md`](05-fast-recovery.md)).
- **Cheat** — the whole drill on one screen **§6** ([`06-cheat-sheet.md`](06-cheat-sheet.md)).

---

## Chapter index

| § | Topic | Chapter file |
|---|-------|--------------|
| §0 | What the loop depends on | [`00-dependencies.md`](00-dependencies.md) |
| §1 | BRING ONLINE — preflight | [`01-preflight.md`](01-preflight.md) |
| §2 | BRING ONLINE — launch | [`02-launch.md`](02-launch.md) |
| §2.5 | Control panel & live status (`:65000`) | [`02.5-control-panel.md`](02.5-control-panel.md) |
| §3 | BRING OFFLINE — stop the loop (normal) | [`03-stop.md`](03-stop.md) |
| §3.5 | Session continuity (Tier 1) | [`03.5-session-continuity.md`](03.5-session-continuity.md) |
| §4 | BRING OFFLINE — full teardown (reclaim memory) | [`04-teardown.md`](04-teardown.md) |
| §5 | Fast recovery (runtime symptom → fix) | [`05-fast-recovery.md`](05-fast-recovery.md) |
| §6 | One-glance cheat | [`06-cheat-sheet.md`](06-cheat-sheet.md) |
