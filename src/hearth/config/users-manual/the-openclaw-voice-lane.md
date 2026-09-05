# The OpenClaw voice lane — as designed

> ⚠️ **UNTESTED CHAPTER — read the whole thing as "how it *should* work."** This lane has **not** been
> exercised end-to-end through the full Hearth experience. The individual legs shipped as a stage-1 pilot
> and some were round-trip-checked, but the integration as a whole is **to be confirmed on the
> first real test.** Every "does X" below is a design intention, not a field-verified fact. Treat it as a map
> to test *against*, and clear this banner once a real end-to-end run confirms it.

*What lets OpenClaw (the coding/agent harness) reply in one of Hearth's cloned voices — the small local
shim it speaks through, and what's known vs. still open.*

> **Not the OpenClaw bridge.** Hearth also ships an unrelated, gated feature with a confusingly similar
> name: `config/openclaw.toml` gives the *companion* two narrow tools for dispatching work to an OpenClaw
> agent — the companion's hands, not its voice. That one is part of the product and documented in
> `docs/config-manual/settings-reference-gates.md`. This page is the other direction: OpenClaw borrowing
> Hearth's voice, assembled outside the shipped install.

**Authoritative source (the ops record):** the OpenClaw voice-lane operating record
— the single operating record for this lane, including version pins, config, and rollback. This page is
a short, honest orientation; that record governs.

---

## The idea in one line

OpenClaw's reply-to-speech goes to a **local, OpenAI-compatible TTS server** (the mlx-audio shim on
**:8555**) that clones a Hearth voice from a `sample.wav` — so OpenClaw can *speak* as one of the companions,
with **zero OpenClaw source changes** and no code dependency on the Hearth repo.

```
OpenClaw reply text ──/v1/audio/speech──▶  mlx-audio shim  :8555 (loopback)
                        (ref_audio = a copied Hearth voice bundle)   │
                                                                     ▼
                                                            cloned-voice audio
```

A companion **voice agent** ("Hearth Voice") also runs OpenClaw's turns on the same local model Hearth
uses — so both the words and the voice stay local.

---

## What runs (as recorded)

- **`ai.openclaw.voice-tts`** — a launchd agent running `mlx_audio.server` on **loopback `127.0.0.1:8555`**.
  Localhost-only by design: **personal voices never leave the machine.** It serves `/v1/audio/speech` (the
  TTS-with-cloning route OpenClaw calls) and also carries idle STT routes.
- **The OpenClaw side** points its TTS provider at `http://127.0.0.1:8555/v1` with a **dummy API key** (the
  server ignores it — loopback is the switch), `wav` output, and an `extraBody.ref_audio` pointing at the
  chosen voice sample. It's **on-demand only** (`/tts audio <text>`), not automatic.

> **Same :8555 as Hearth's own voice door.** This is the very server Hearth (:65001) also proxies to for its voice
> notes — one loopback TTS engine, two consumers. Keep its version pins stable, or bump both consumers
> together (the ops README spells out the exact pins and why they matter).

---

## Voices are **copied**, not shared

Each voice here is a **snapshot copy** of a Hearth bundle (`sample.wav` + `voice.toml`), checksum-verified at
copy time — deliberately a copy, not a symlink, so OpenClaw's voice can't break when the Hearth repo
reorganizes. To add or refresh one, you copy the bundle across and point `ref_audio` at it. The same
**personal-use-only** discipline rides with the clip: **local only, never committed, never published** (see
[Onboarding a character](onboarding-a-character.md) and `COMPONENT-LICENSING.md`).

---

## What's honestly still open

Per the ops record, these are **not** done — reinforcing the untested banner:

- **Stage-2 items open:** the STT leg isn't wired; there's no voice-name→bundle adapter (you edit
  `ref_audio` by hand); and the "agent-to-agent" posture (letting the voice agent act) is deliberately **not
  enabled yet.**
- **Known losses vs. Hearth proper (accepted for the pilot):** no paralinguistic/ellipsis text cleanup and
  no warm per-voice conditionals — so delivery may not match the desk pipeline's polish.
- **The ear test hasn't rendered its verdict.** Whether those losses matter is exactly what a first real run
  is meant to tell you.

**Net:** the plumbing is in place and individually plausible, but **nobody has driven a full conversation
through it as the intended experience.** Until that happens, this chapter is the hypothesis — go confirm it
against the ops README, then lift the banner.
