# The `example` character

This is the one character Hearth ships with: a generic, friendly companion you can talk to
out of the box, and — more importantly — a **worked example of the character format** to copy
when you author your own.

Everything here is original and generic. There is no roster of pre-made personalities to pick
from; the design is that you make your own (or adapt this one), and this directory shows you
the shape.

## What's in a character directory

```
characters/
  example/
    README.md            ← this file
    persona.md           ← who the character is (the {{persona}} the model is given)
    persona.<variant>.md ← optional variants of the persona, selected by `persona =` in active.toml
    voices/
      default/
        voice.toml        ← the voice descriptor: tag + reference clip + synth facts
        sample.wav        ← the reference clip the voice is cloned from
        VOICE-SOURCE.md   ← where the clip came from and its rights
    sessions/            ← written at runtime: this companion's conversations (0700, gitignored)
    transcripts/         ← written at runtime: the serve facade's taps, if enabled
    captures/            ← written at runtime: recordings from the panel's Record button
    profile.toml         ← written by the panel: the companion's saved knob preset
```

The first block is what you author; the `sessions/`, `transcripts/`, `captures/`, and
`profile.toml` entries appear as the companion is used — they are **its** memory and
recordings, kept in its own directory so the whole companion moves (or is erased) as one.
If you set `HEARTH_DATA`, the same layout lives there instead of in the checkout.

A character is a directory under `characters/`. It has:

- **`persona.md`** — the character's identity and inner life, written as two labelled
  sections (`## IDENTITY` and `## SOUL`). This text is composed into the system prompt at
  startup. See [Authoring a character](../../docs/authoring-a-character.md) for the format.
  A **variant** — `persona.night.md`, say — is the same format in a sibling file; select it
  with `persona = "night"` in `config/active.toml` (omit for `persona.md`).
- **`voices/<name>/`** — one or more voice bundles. Each is a self-contained folder holding a
  `voice.toml` descriptor and the reference clip it points at. A character can have several
  voices; you pick the live one in `config/active.toml`.

## Making it yours

The fastest way to a companion of your own: copy `characters/example/` to a new directory,
rewrite `persona.md`, and swap in your own voice clip. Then point `config/active.toml` at the
new character and voice, and restart.

`example` is meant to be replaced or adapted — treat it as a template, not a fixture.
