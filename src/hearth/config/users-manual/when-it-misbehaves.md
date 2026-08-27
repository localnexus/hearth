# When it misbehaves

*A router, not a manual. Match your symptom, go to the doc that actually fixes it. The only fixes written out
here are the handful of **facade** cases new enough that they aren't in the older playbooks yet.*

**Authoritative sources:** runtime symptom → one-action fix → `../../docs/runbook/05-fast-recovery.md`; deeper
wedged/silent-pipeline debugging → `../../docs/debugging/`; the fast-recovery
overview → `../../docs/runbook/README.md`. Those are the single sources of truth — this page just points.

---

## Symptom → where to go

| What you're seeing | Go to |
|---|---|
| Speaks nothing; log hangs after `Generating chat` | `../../docs/runbook/05-fast-recovery.md` (LLM emitting chain-of-thought — thinking must be off) |
| No audio / `Errno -9996` / wrong input device | `../../docs/runbook/05-fast-recovery.md` (invalid default device; relaunch) |
| STT never transcribes but the OS mic meter moves | `../../docs/runbook/05-fast-recovery.md` (iTerm mic permission / TCC) |
| `model_not_found` / `401` to `:1234` | `../../docs/runbook/05-fast-recovery.md` (wrong id / bad LM Studio token) |
| Set a voice in `active.toml` but a **different** voice plays | `../../docs/runbook/05-fast-recovery.md` (sticky `[voice]` in the panel's `overrides.toml`) → also [The config layers](the-config-layers.md) |
| Cut off mid-word / garbled render / cue tag spoken aloud | `../../docs/debugging/tts-audio-cases.md` |
| Resume "not found" / persona-model mismatch after resume | `../../docs/debugging/session-continuity-faults.md` |
| Phone won't connect / mic dead on the walk | [The phone lane — away mode](the-phone-lane-away-mode.md) (TURN up? insecure-origin flag re-added? trio relaunched after reboot?) |
| Facade won't answer / `unauthorized` | the two new cases below |

---

## The new facade cases — not yet in the older docs

These landed with the launchd facade and aren't in `../../docs/runbook/05-fast-recovery.md` or `../../docs/debugging/` yet, so they're
written out here until those docs absorb them.

### Connection refused after a `kickstart`, and the log says nothing ran

**Symptom:** you bounced the facade (`launchctl kickstart -k gui/$(id -u)/com.hearth.facade`), and now
`:65001` gives **connection refused**; the facade log shows it loaded nothing rather than an error/traceback.

**Cause: the `serve.toml` gate, not a fault.** When `config/serve.toml` has `enabled = false` (or the file
is absent), the facade **loads nothing and opens no socket** — byte-identical-appliance behavior, by design
(confirmed in `config/serve.toml.example`). A refused port here means *disabled*, not *broken*.

**[UNVERIFIED]** the exact "nothing to run" wording in the log — the mechanism (gate → no socket) is
verified from `serve.toml.example`, but the literal log string couldn't be confirmed (the facade is
currently enabled, and broad log reads are out of scope). Treat "refused + no error in the log after a
kickstart" as the gate signature regardless of the exact phrasing.

**Fix:** if you *want* the facade up, set `enabled = true` in `config/serve.toml` and kickstart again.
(Manage that file, never print it — [The config layers](the-config-layers.md).)

### `{"error": "unauthorized"}` back from the facade

**Symptom:** a request to `:65001` returns HTTP **401** with body `{"error": "unauthorized"}`.

**Cause:** the facade's bearer auth is **always on** — a request with no (or a wrong) token is refused. This
is the gate working, not a fault.

**Fix — the approved inline-token idiom** (feeds the token to the header, displays nothing):
```bash
curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
```
A healthy facade then returns the active character as the model `id`. **Never** work around a 401 by
printing the token or dumping env — the `$(cat config/serve-token)` *inside* the curl argument is the whole
point.

---

**Net:** almost everything routes to `../../docs/runbook/05-fast-recovery.md` or `../../docs/debugging/`. The only cases
that live here are the two facade signatures above — a refused port that's really the `serve.toml` gate, and
a 401 that's really the bearer doing its job.
