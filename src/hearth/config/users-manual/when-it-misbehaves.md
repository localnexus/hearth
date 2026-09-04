# When it misbehaves

*A router, not a manual. Match your symptom, go to the doc that actually fixes it. The only fixes written out
here are the handful of **facade** cases new enough that they aren't in the older playbooks yet.*

**Authoritative sources:** runtime symptom → one-action fix → `docs/runbook/05-fast-recovery.md`; deeper
wedged/silent-pipeline debugging → `docs/debugging/`; the fast-recovery
overview → `docs/runbook/README.md`. Those are the single sources of truth — this page just points.

---

## Symptom → where to go

| What you're seeing | Go to |
|---|---|
| Speaks nothing; log hangs after `Generating chat` | `docs/runbook/05-fast-recovery.md` (LLM emitting chain-of-thought — thinking must be off) |
| No audio / `Errno -9996` / wrong input device | `docs/runbook/05-fast-recovery.md` (invalid default device; relaunch) |
| STT never transcribes but the OS mic meter moves | `docs/runbook/05-fast-recovery.md` (iTerm mic permission / TCC) |
| `model_not_found` / `401` from the LLM server | `docs/runbook/05-fast-recovery.md` (wrong model id, or a missing/bad `LM_API_TOKEN` — `llama-server` only wants one if started with `--api-key`; LM Studio always does) |
| Picked a voice but a **different** voice plays | A live audition is still bound. Press **Reset voice** in the panel's VOICE box (or **Restore ALL to defaults**) and the pick takes. Why: [The config layers](the-config-layers.md) · deeper: `docs/runbook/05-fast-recovery.md` |
| Cut off mid-word / garbled render / cue tag spoken aloud | `docs/debugging/tts-audio-cases.md` |
| Resume "not found" / persona-model mismatch after resume | `docs/debugging/session-continuity-faults.md` |
| A config file that may be wrong | Open it on `/admin/settings/ui` — the form is generated from the schema that validates it, so a bad value is refused rather than saved. Checking a file you already hand-edited: `python -m hearth.config.check` (names bad keys, prints no values) |
| The panel has no **COMPANION** box | [The one-button switch](the-one-button-switch.md) (daemon gate off? facade not running standalone? panel LAN-exposed?) |
| A switch answered `409` and nothing changed | [The one-button switch](the-one-button-switch.md) (a switch already in flight, or a refused live arm — the selection is already written; repost on the restart path) |
| Phone won't connect / mic dead on the walk | [The phone lane — away mode](the-phone-lane-away-mode.md) (TURN up? insecure-origin flag re-added? relaunched after reboot?) |
| Facade won't answer / `unauthorized` | the two cases below |

---

## The two facade cases — not in the older docs

These are the facade's own signatures, and both are the design working rather than a fault, so they're
written out here.

### Connection refused after a restart, and nothing ran

**Symptom:** you restarted the facade and now `:65001` gives **connection refused**; it printed no error and
no traceback.

**Cause: the `serve.toml` gate, not a fault.** When `config/serve.toml` has `enabled = false` (or the file
is absent), the facade **loads nothing and opens no socket** — byte-identical-appliance behavior, by design
(confirmed in `config/serve.toml.example`). A refused port here means *disabled*, not *broken*.

Run standalone, it says exactly this on stderr and exits 2:

```
[serve] config/serve.toml absent or enabled=false — nothing to run
```

**Fix:** if you *want* the facade up, turn the gate on and start it again — the **serve** section of
`/admin/settings/ui` if you can already reach the facade, otherwise `enabled = true` in
`config/serve.toml` by hand. (Manage that file, never print it — [The config layers](the-config-layers.md).)

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

**Net:** almost everything routes to `docs/runbook/05-fast-recovery.md` or `docs/debugging/`. The only cases
that live here are the two facade signatures above — a refused port that's really the `serve.toml` gate, and
a 401 that's really the bearer doing its job.
