# The live knobs panel — CHARACTER, VOICE, LISTENING

*What the panel's three knob boxes are, how a slider's value gets applied, and what Save / Load / Reset
actually do to your files. This page is the mechanics; the felt meaning of each dial lives in its own
knob page.*

**Authoritative sources:** the control panel generally → `docs/runbook/02.5-control-panel.md`; who writes
which file → [The config layers](the-config-layers.md). This page is the *how the boxes work* companion to
that ownership map.

---

## What shows up, and when

Below the status block, the panel grows up to three boxes — **CHARACTER**, **VOICE**, and (collapsed by
default) **LISTENING**. They appear on their own: the page probes for them at load, and an older install
without this feature simply never grows the section. Nothing to turn on.

Each box holds live sliders (and one dropdown, `reasoning_effort`) for a different slice of the sound and
sense of the companion:

| Box | Knobs | Tier | Felt meaning |
|---|---|---|---|
| **CHARACTER** | `temperature`, `reasoning_effort` | per **character** | [The generation knobs](generation-knobs-in-your-ear.md) |
| **VOICE** | `temperature`, `top_p`, `top_k`, `repetition_penalty` | per **voice sample** | [The voice delivery knobs](the-voice-delivery-knobs.md) |
| **LISTENING** | `confidence`, `start_secs`, `stop_secs`, `min_volume` | per **room/mic**, never per character | [The listening calibration](the-listening-calibration.md) |

CHARACTER and VOICE are the **texture** tiers — they travel with a companion (below). LISTENING is
**calibration** — it never does; switching who's live never touches it.

---

## What moving a slider does

Every slider shows the **effective** value — your live override if one is set, otherwise the persisted
baseline. Move one and it writes immediately to `config/overrides.toml`; `config_reload.py` picks it up at
the **next turn boundary**, not mid-reply. A value shown in amber means it's currently overridden from the
shipped default; plain-colored means you're sitting at baseline. Each slider also carries a one-line "in
your ear" description underneath it that updates live to say which way the current value leans (see the
knob pages above for the fuller version), and a `⚠` warning line that appears only when a value has drifted
past a threshold worth knowing about — a low `stop_secs`, an extreme `temperature`, and so on.

A slider that shows **`—`** and won't move means there's no persisted baseline for that key on this
install (an unusually old or hand-pruned config) — nothing is silently defaulted underneath you; fix the
underlying file and it lights up.

---

## Save, Load, Reset — what they touch

**Save to character** / **Save to voice** snapshot your *current live overrides* for that tier into the
companion's own directory — `characters/<name>/profile.toml` for CHARACTER,
`characters/<name>/voices/<tag>/profile.toml` for VOICE. This is the **deliberate** copy: it doesn't change
automatically as you keep tweaking afterward. A profile is a set of *deltas* from the shipped baseline, not
absolute values — saving with nothing dialed away is a valid "this voice uses the defaults" preset.

**Load preset** (VOICE only) re-reads that voice's saved `profile.toml` and makes it the live state again,
discarding whatever you'd been auditioning live since. The **Sample** dropdown in the VOICE box does this
automatically too: picking a different voice sample loads *that* sample's saved preset (a `★` marks samples
that have one) and switches which clip is speaking, in one action.

**Reset character** / **Reset voice** clear that tier's *live* overrides back to the shipped baseline — it
never touches or deletes the saved profile file. **Restore ALL to defaults** clears every live override,
LISTENING included, in one press.

Besides the deliberate profile, there's a second file beside it — `overrides.toml` in that same companion
directory — that the panel keeps in sync automatically after every write, mirroring whatever the live
CHARACTER/VOICE knobs currently are. You never manage this one; it exists so the companion's whole
directory is always self-describing, without requiring an explicit Save.

---

## The voice sample you're auditioning doesn't outlive the session

Picking a different clip in the VOICE box's **Sample** dropdown writes a live `[voice]` override — but that
one override is **session-scoped**: it's cleared automatically the next time the bot starts, before
anything else loads. Between sessions, `config/active.toml` is still the only thing that decides who
sounds how ([Switching who's live](switching-who-is-live.md)). Audition freely; a sample you tried and
didn't keep can't quietly become tomorrow's voice by accident.

---

## Net

Three boxes, two travel with the companion (CHARACTER, VOICE) and one doesn't (LISTENING); every slider
writes to `overrides.toml` and lands next turn; Save/Load/Reset operate on the companion's own preset
files, never on `active.toml`; and a live voice audition can't survive a restart on its own. What each dial
*sounds like* is the knob pages linked above — this page is only the machinery.
