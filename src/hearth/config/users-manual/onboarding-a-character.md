# Onboarding a character

*Bringing a new companion into Hearth: what a character is made of, how the companion's voice bundle is put together,
the licensing discipline that rides every clip, and the ear test that decides what goes live.*

**Authoritative source (the recipe):** the bring-your-own-voice guide is the step-by-step
that governs — bundle layout, the `voice.toml` key table, the BlackHole capture recipe, and every clip
requirement. The licensing frame: `COMPONENT-LICENSING.md`. The what-to-edit reference:
`../../docs/config-manual/voice-tts.md`. This page walks the *shape* of the process; those hold the exact steps.

---

## What a character actually is

A character is two independent halves that get composed at startup — keep them separate in your head:

```
characters/<name>/persona.md          ← WHO they are (model-agnostic)
   ## IDENTITY  — the stable who        fills the {{persona}} slot of…
   ## SOUL      — the tunable how              │
                                               ▼
config/models/<model>/system-prompt-template.md   ← the output-shaping HARD RULES
   (short, spoken, no-markdown; the nine breath-cue tags; the {{persona}} slot)
```

- **`persona.md`** is **model-agnostic** — it's just *the character*, with two labelled sections: `## IDENTITY` (the
  stable who) and `## SOUL` (the tunable how). It fills the `{{persona}}` slot of the active model's template.
- **The hard rules live in the model template, not the persona** — "keep it short, spoken, no markdown," the
  paralinguistic-cue whitelist, and so on. That's deliberate: swap the model, keep the character; swap the
  character, keep the delivery rules. So a new character needs **no template change.**

> You don't need to open any persona file to onboard one — you need to know the *shape* above and follow the
> recipe. The persona's content is the character author's craft, tuned by ear over time.

---

## What a voice bundle is

A voice is a **self-contained folder** — it travels with the tree, no registration step, discovered by path:

```
characters/<name>/voices/<tag>/
├── sample.wav     # the clone reference — 24 kHz mono
└── voice.toml     # tag + ref_wav, then license/provenance, then engine knobs
```

- **`sample.wav`** is the clone reference. Chatterbox-Turbo conditions on only the **first ~15 s** (and only
  the first ~10 s for its decoder) — so your best, cleanest, most characteristic seconds belong at the
  **front**, and trimming to a clean ~10–15 s is the highest-leverage thing you can do. (The
  bring-your-own-voice guide covers the full conditioning science; honour it and the clone comes out right.)
- **`voice.toml`** has two **required** keys — `tag` (match the dir name) and `ref_wav` (normally
  `"sample.wav"`, relative) — then a **provenance / license block**, then optional engine knobs. Only `tag`
  + `ref_wav` are read at load; the rest is metadata the operator maintains.
- **An acoustic-proxy analysis pass** ranks candidate clips (class/rank annotations get written into bundle
  headers) so you audition from the strongest cuts first — but the ranking *proposes*, it never *decides*.

---

## The licensing discipline — it rides the clip, not the character

This is the part you cannot skip, because the restriction attaches to the **audio**, not the persona:

- **Every bundle carries `license = "personal-use-only"`** by default — the conservative posture. A voice
  cloned from a copyrighted character, a real performer, or an unclear source is **local only: never
  shipped, shared, published, or reaching any public/OSS artifact.**
- **Record provenance where it's enforceable.** Each character keeps a **`VOICE-SOURCE.md`** convention doc —
  the tracked record of where the audio came from and its restriction class — *and* the same `license` /
  `source` live in the bundle's `voice.toml`. (A character's *persona* can be fully original while its
  *voice* is restricted — the two axes are independent; the clip's restriction wins for distribution.)
- **No default voice ships, by design.** Because every catalogued voice is `personal-use-only`, Hearth pins
  no voice as *the* shipping default until a permissively-licensed sample lands. The operator selects one
  locally. (`COMPONENT-LICENSING.md` Callout 2 is the why: voice-likeness is the operator's legal axis, not
  the project's.)

---

## The process, end to end

1. **Get a clean clip** into `characters/<name>/voices/<tag>/sample.wav` — mono, ~24 kHz, best ~10–15 s at
   the front, ending on a settled falling phrase. (No downloadable file? The bring-your-own-voice guide has the
   BlackHole loopback capture recipe.)
2. **Write `voice.toml`** — `tag` + `ref_wav`, then the license/source block (start at `personal-use-only`),
   then any engine knobs. Put the ~15 s cloning note in the header comment.
3. **A whole new character?** Add `persona.md` (`## IDENTITY` + `## SOUL`) alongside `voices/`. No template
   change needed.
4. **Record provenance** — the character's `VOICE-SOURCE.md` *and* the `voice.toml`.
5. **Select + restart** — set `character` / `voice` in `config/active.toml` and bounce the lane that serves
   the companion (see [Switching who's live](switching-who-is-live.md)). Conditionals precompute once at startup, so a
   restart is required.
6. **Audition by ear before you promote.** The analysis pass ranks candidates; **your ear decides** which
   becomes the live voice. Listen to a real spoken reply, keep or revert.

**Net:** two halves (model-agnostic persona + model-side hard rules), a self-contained voice bundle whose
license rides the clip, provenance recorded in two enforceable places, and the ear as the final judge.
