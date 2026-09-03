# users-manual/ — how Hearth's settings *feel*, not what they *are*

This is a **translation layer.** Everywhere else in the repo describes settings the way a machine needs them:
`config/` holds the spec and the code holds the truth. This directory does the one
thing those don't — it tells you **what a setting does to the person listening**, when the response actually
lands in their ear.

It's written for someone who shouldn't have to know what "min_p" is to make Hearth sound more like *the companion you wrote*.

## The rule this manual follows

The guiding idea: *a translation layer — every field rendered as what you'd feel
in Hearth, so each number teaches what it means and what the difference would be like to live with.* Every
page renders a setting as a felt difference:

- **What it is** — one honest line, no more.
- **In your ear** — what *changes* when the response lands, described as an experience, not a mechanism.
- **Turn it up when… / down when…** — the decision, phrased by the goal you can hear, not the number.
- **Net** — the one-line takeaway you'd act on.

If a page can't tell you what you'd *hear differently*, it doesn't belong here — it belongs in `config/`.

## What's here

The manual has two flavours of page, same operator-facing, plain-language register throughout. **Knob
pages** translate a setting into a felt difference. **Process chapters** walk a task — "what this does and
how you do it" — and each opens with an *authoritative sources* pointer, then **links out to the single
source of truth** (`docs/config-manual/`, `docs/runbook/`, `docs/bring-your-own-voice.md`,
`docs/debugging/`) rather than duplicating it.

### Knob pages — the felt difference

| Page | Translates |
|---|---|
| [`generation-knobs-in-your-ear.md`](generation-knobs-in-your-ear.md) | The LLM sampling + length knobs (temperature, reasoning_effort, top_p/top_k/min_p, repeat_penalty, max_tokens) and the prompt-side length rule — how each reshapes how the companion *talks*. |
| [`the-voice-delivery-knobs.md`](the-voice-delivery-knobs.md) | The panel's **VOICE** box (temperature, top_p, top_k, repetition_penalty) — how each reshapes how the cloned voice *sounds*, as distinct from the LLM knobs above. |
| [`the-listening-calibration.md`](the-listening-calibration.md) | The panel's **LISTENING** box (confidence, start_secs, stop_secs, min_volume) — per-room/mic calibration for how surely and how fast Hearth notices you're talking. |

### Process chapters — how you do it

| Chapter | Walks you through |
|---|---|
| [`the-map-of-doors.md`](the-map-of-doors.md) | Every port on one page — owner, bind, auth, and the read-only health check for each, plus what a deployment tends to add around them. Start here when you're lost about which thing is on which port. |
| [`reading-the-panel.md`](reading-the-panel.md) | What's actually on the :65000 panel — text/mute/PTT, the Record button, and every line of the status block, including the Memory line's live pause/resume button. Start here when you're lost about what's *on the page*. |
| [`the-live-knobs-panel.md`](the-live-knobs-panel.md) | How the panel's CHARACTER / VOICE / LISTENING boxes actually work — effective values, Save/Load/Reset, and why an auditioned voice can't outlive the session. |
| [`switching-who-is-live.md`](switching-who-is-live.md) | The hand ritual to change character / voice / model / persona: edit `active.toml` → check no live session → restart the lane → confirm by name, log, and ear. |
| [`the-one-button-switch.md`](the-one-button-switch.md) | The same switch as one press: turning on the supervisor daemon, the panel's **COMPANION** box, and how to tell a live swap from a warm restart. |
| [`the-phone-lane-away-mode.md`](the-phone-lane-away-mode.md) | ⚠️ **A deployment, not the shipped install** — away mode from the server side: overlay-network-only exposure, the TURN-over-TCP workaround, the fragile insecure-origin flag, and the reboot-durability gap. |
| [`the-openclaw-voice-lane.md`](the-openclaw-voice-lane.md) | ⚠️ **UNTESTED** — how OpenClaw *should* speak in a Hearth voice via the :8555 shim, honest about what's unconfirmed. |
| [`onboarding-a-character.md`](onboarding-a-character.md) | Bringing in a new companion — the persona/model split, the voice bundle, the personal-use-only licensing that rides the clip, and the ear test. |
| [`the-config-layers.md`](the-config-layers.md) | Who writes what — the two roots, `active.toml` (you), `overrides.toml` (the panel), model dirs (you), the shipped baselines, `serve.toml` (manage, never print), and the memory / OpenClaw gates. |
| [`when-it-misbehaves.md`](when-it-misbehaves.md) | A router: symptom → the authoritative doc that fixes it, plus the two facade cases written out here (the `serve.toml` gate, the bearer 401). |

## What this manual is NOT

- **Not the spec.** Exact keys, defaults, precedence, and load behavior live in `config/`. When they disagree,
  the spec wins — tell me and I'll reconcile this page.
- **Not a promise every lever is Hearth's.** Some knobs belong to the LLM server you run, not to Hearth —
  the pages say which, and where to set them instead.
- **Not a substitute for the ear.** These are priors to aim your listening, not a lookup table that replaces
  hearing it. The ear test always decides.
