# When it misbehaves

*A router, not a manual. Match your symptom, go to the doc that actually fixes it. The only fixes written out
here are the handful of **Hearth** cases new enough that they aren't in the older playbooks yet.*

**Authoritative sources:** runtime symptom → one-action fix → `docs/runbook/05-fast-recovery.md`; deeper
wedged/silent-pipeline debugging → `docs/debugging/`; the fast-recovery
overview → `docs/runbook/README.md`. Those are the single sources of truth — this page just points.

---

## Symptom → where to go

| What you're seeing | Go to |
|---|---|
| Speaks nothing; log hangs after `Generating chat` | `docs/runbook/05-fast-recovery.md` (model emitting chain-of-thought — thinking must be off) |
| No audio / `Errno -9996` / wrong input device | `docs/runbook/05-fast-recovery.md` (invalid default device; relaunch) |
| STT never transcribes but the OS mic meter moves | `docs/runbook/05-fast-recovery.md` (iTerm mic permission / TCC) |
| `model_not_found` / `401` from the model server | `docs/runbook/05-fast-recovery.md` (wrong model id, or a missing/bad `LM_API_TOKEN` — `llama-server` only wants one if started with `--api-key`; LM Studio always does) |
| Picked a voice but a **different** voice plays | A live audition is still bound. Press **Reset voice** in the panel's VOICE box (or **Restore ALL to defaults**) and the pick takes. Why: [The config layers](the-config-layers.md) · deeper: `docs/runbook/05-fast-recovery.md` |
| Cut off mid-word / garbled render / cue tag spoken aloud | `docs/debugging/tts-audio-cases.md` |
| Resume "not found" / persona-model mismatch after resume | `docs/debugging/session-continuity-faults.md` |
| A config file that may be wrong | With Hearth running: the form on `/admin/settings/ui` is generated from the schema that validates it, so a bad value is refused rather than saved. With Hearth off — the shipped default — `python -m hearth.config.check` validates every file and names bad keys, printing no values. Use it after any hand-edit |
| The panel has no **COMPANION** box | [The one-button switch](the-one-button-switch.md) (Hearth switch off? Hearth not running standalone? panel LAN-exposed?) |
| A switch answered `409` and nothing changed | [The one-button switch](the-one-button-switch.md) (a switch already in flight, or a refused live arm — the selection is already written; repost on the restart path) |
| Phone won't connect / mic dead on the walk | [The phone lane — away mode](the-phone-lane-away-mode.md) (TURN up? insecure-origin flag re-added? relaunched after reboot?) |
| Hearth won't answer / `unauthorized` | the two cases below |

---

## The two Hearth cases — not in the older docs

These are Hearth's own signatures, and both are the design working rather than a fault, so they're
written out here.

### Connection refused after a restart, and nothing ran

**Symptom:** you restarted Hearth and now `:65001` gives **connection refused**; it printed no error and
no traceback.

**Cause: the `serve.toml` switch, not a fault.** When `config/serve.toml` has `enabled = false` (or the file
is absent), Hearth **loads nothing and opens no socket** — byte-identical-appliance behavior, by design
(confirmed in `config/serve.toml.example`). A refused port here means *disabled*, not *broken*.

Run standalone, it says exactly this on stderr and exits 2:

```
[serve] config/serve.toml absent or enabled=false — nothing to run
```

**Fix:** if you *want* Hearth up, turn the switch on and start it again — the **serve** section of
`/admin/settings/ui` if you can already reach Hearth; otherwise the first-run setup
(`hearth.init`) sets `enabled = true` and touches nothing else, or set it by hand. (Manage that
file, never print it — [The config layers](the-config-layers.md).)

### `{"error": "unauthorized"}` back from Hearth

**Symptom:** a request to `:65001` returns HTTP **401** with body `{"error": "unauthorized"}`.

**Cause:** Hearth's access key auth is **always on** — a request with no (or a wrong) token is refused. This
is the switch working, not a fault.

**Fix — the approved inline-token idiom** (feeds the token to the header, displays nothing):
```bash
curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
```
A healthy Hearth then returns the active character as the model `id`. **Never** work around a 401 by
printing the token or dumping env — the `$(cat config/serve-token)` *inside* the curl argument is the whole
point.

---

**Net:** almost everything routes to `docs/runbook/05-fast-recovery.md` or `docs/debugging/`. The only cases
that live here are the two Hearth signatures above — a refused port that's really the `serve.toml` switch, and
a 401 that's really the access key doing its job.
