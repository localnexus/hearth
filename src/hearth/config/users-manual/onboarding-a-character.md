# Onboarding a character

*Bringing a new companion into Hearth: what a character is made of, how the companion's voice bundle is put together,
the licensing discipline that rides every clip, and the ear test that decides what goes live.*

**Authoritative sources:** the persona half → `docs/authoring-a-character.md`; the voice half →
`docs/bring-your-own-voice.md` (bundle layout, the `voice.toml` key table, the capture recipe, every clip
requirement). The licensing frame: `docs/COMPONENT-LICENSING.md`. The what-to-edit reference:
`docs/config-manual/voice-tts.md`. This page walks the *shape* of the process; those hold the exact steps.

The fastest start: copy `characters/example/` and edit it. Or let the **roster
wizard** do the mechanical steps for you — `/admin/roster` on Hearth
(behind the access key door, when the launch page is mounted) scaffolds the
directories, conditions the clip, writes `voice.toml` + `VOICE-SOURCE.md` from
one set of answers, and verifies the bundle with the same loaders startup
uses. What it never does: overwrite an existing bundle, or promote a voice —
going live and the ear test stay yours either way.

The same page also covers a **living** character: an **Edit a persona** card
(rewrites `persona.md` or a variant, verified with the composition path, one
`.prev` backup kept — a shipped persona is copied-on-write into your data
root, never edited in place) and an **Add a voice** card (the wizard's clip
pipeline pointed at an existing character; create-only per tag, provenance
appended to `VOICE-SOURCE.md`). New tags and variants show in the switch
pickers immediately; composition still happens at startup or a live switch.

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

- **`persona.md`** is **model-agnostic** — it's just *the character*, with two labelled sections:
  `## IDENTITY` (the stable who) and `## SOUL` (the tunable how). Both headers are required and both bodies
  must be non-empty; startup fails with a clear error otherwise. Anything inside an HTML comment is stripped
  before composition, so you can leave authoring notes to yourself that the model never sees.
- **A character can carry persona *variants*.** Add `persona.<variant>.md` beside `persona.md` (same two
  sections) and select it with `persona = "<variant>"` in `active.toml`. Trying a rewrite is then a file you
  can diff and delete, never a copy you have to remember to restore — and a session records which variant
  was live, so a resume warns you if it changed.
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

- **`sample.wav`** is the clone reference. The cloning model conditions on only the **first ~10–15 s** —
  audio past that is ignored, while loudness normalization runs over the whole clip — so your best,
  cleanest, most characteristic seconds belong at the **front**, and trimming to a clean 10–15 s is the
  highest-leverage thing you can do. (`docs/bring-your-own-voice.md` covers the conditioning science;
  honour it and the clone comes out right.)
- **`voice.toml`** has two **required** keys — `tag` (match the dir name) and `ref_wav` (normally
  `"sample.wav"`, relative) — then a **provenance / license block**, then optional engine knobs. Only `tag`
  + `ref_wav` are read at load; the rest is metadata the operator maintains.
- **Nothing else is read at load.** The engine facts a bundle may carry (`model_repo`, `sample_rate`,
  `streaming_interval`) match the pipeline's defaults — they document the clip, they don't change it.

---

## The licensing discipline — it rides the clip, not the character

This is the part you cannot skip, because the restriction attaches to the **audio**, not the persona:

- **A voice you clone yourself is `personal-use-only` unless you know otherwise** — the conservative
  posture. A voice cloned from a copyrighted character, a real performer, or an unclear source is **local
  only: never shipped, shared, published, or reaching any public artifact.**
- **Record provenance where it's enforceable.** Each character keeps a **`VOICE-SOURCE.md`** convention doc —
  the tracked record of where the audio came from and its restriction class — *and* the same `license` /
  `source` live in the bundle's `voice.toml`. (A character's *persona* can be fully original while its
  *voice* is restricted — the two axes are independent; the clip's restriction wins for distribution.)
- **The one voice that ships is rights-clean on purpose.** `characters/example/voices/default/` carries a
  public-domain clip (LJ Speech), documented in its own `VOICE-SOURCE.md` — an ordinary, clear English
  voice, deliberately not a character, so the box works out of the box. Every voice you add after it is
  yours to clear. (`docs/COMPONENT-LICENSING.md` is the why: voice-likeness is the operator's legal axis,
  not the project's.)

---

## The process, end to end

**On the roster page** (`/admin/roster`) — the ordinary way:

1. **Answer the wizard's questions** and hand it your clip. It scaffolds the directories, conditions the
   audio, writes the descriptor and the provenance record, and verifies the result with the same loaders
   startup uses. A whole new companion, or a new voice for one you already have, is the same card.
2. **Write the persona** in the *Edit a persona* card — the two sections, checked through the real
   composition path before it saves, with one backup kept.
3. **Make them live** with the switch card, on the panel or the launch page.
4. **Audition by ear before you promote.** **Your ear decides** which cut becomes the live voice. Listen
   to a real spoken reply, keep or revert. The wizard deliberately stops short of this one — promoting a
   voice is a judgement, not a step.

**By hand** — when the launch-page switch is off, or you'd rather see the machinery:

1. **Get a clean clip** into `characters/<name>/voices/<tag>/sample.wav` — mono, ~24 kHz, best ~10–15 s at
   the front, ending on a settled falling phrase. (No downloadable file? `docs/bring-your-own-voice.md` has
   a loopback capture recipe.)
2. **Write `voice.toml`** — `tag` + `ref_wav`, then the license/source block (start at `personal-use-only`),
   then any engine knobs. Put the ~15 s cloning note in the header comment.
3. **A whole new character?** Add `persona.md` (`## IDENTITY` + `## SOUL`) alongside `voices/`. No template
   change needed. Then `python -m hearth.config.check` — it validates the new `voice.toml` and everything
   else present, naming keys only.
4. **Record provenance** — the character's `VOICE-SOURCE.md` *and* the `voice.toml`.
5. **Select + restart** — see [Switching who's live](switching-who-is-live.md). The composition happens
   once at startup, so something has to re-read it either way.
6. **Audition by ear**, as above.

Both paths write the same files; the wizard just doesn't make you type them. The anatomy above is worth
reading either way — knowing what a bundle *is* is what lets you fix one.

**Net:** the roster page does the mechanical part — scaffold, condition, describe, verify. What stays
yours is the part that was never mechanical: two halves (model-agnostic persona + model-side hard rules),
a voice bundle whose license rides the clip, provenance recorded in two enforceable places, and the ear as
the final judge.
