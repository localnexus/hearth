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
source of truth** (config-manual, runbook, the bring-your-own-voice guide, debugging) rather than duplicating it.

### Knob pages — the felt difference

| Page | Translates |
|---|---|
| [`generation-knobs-in-your-ear.md`](generation-knobs-in-your-ear.md) | The LLM sampling + length knobs (temperature, top_p/top_k/min_p, repeat_penalty, max_tokens) and the prompt-side length rule — how each reshapes how the companion *talks*. |

### Process chapters — how you do it

| Chapter | Walks you through |
|---|---|
| [`the-map-of-doors.md`](the-map-of-doors.md) | Every port on one page — owner, bind, auth, and the read-only health check for each. Start here when you're lost about which thing is on which port. |
| [`switching-who-is-live.md`](switching-who-is-live.md) | The ritual to change character / voice / model: edit `active.toml` → check no live session → bounce the facade → confirm by id, log, and ear. |
| [`the-phone-lane-away-mode.md`](the-phone-lane-away-mode.md) | Away mode from the server side — tailnet-only exposure, the TURN-over-TCP workaround, the fragile insecure-origin flag, and the open reboot-durability gap. |
| [`the-openclaw-voice-lane.md`](the-openclaw-voice-lane.md) | ⚠️ **UNTESTED** — how OpenClaw *should* speak in a Hearth voice via the :8555 shim, honest about what's unconfirmed. |
| [`onboarding-a-character.md`](onboarding-a-character.md) | Bringing in a new companion — the persona/model split, the voice bundle, the personal-use-only licensing that rides the clip, and the ear test. |
| [`the-config-layers.md`](the-config-layers.md) | Who writes what — `active.toml` (you), `overrides.toml` (the panel), model dirs (you), `serve.toml` (manage, never print), launchd plists. |
| [`when-it-misbehaves.md`](when-it-misbehaves.md) | A router: symptom → the authoritative doc that fixes it, plus the two new facade cases (the `serve.toml` gate, the bearer 401). |

## What this manual is NOT

- **Not the spec.** Exact keys, defaults, precedence, and load behavior live in `config/`. When they disagree,
  the spec wins — tell me and I'll reconcile this page.
- **Not a promise the knob is wired.** A page may describe a lever that's still PROPOSED (it'll say so).
- **Not a substitute for the ear.** These are priors to aim your listening, not a lookup table that replaces
  hearing it. The ear test always decides.
