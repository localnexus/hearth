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
    voices/
      default/
        voice.toml        ← the voice descriptor: tag + reference clip + synth facts
        sample.wav        ← the reference clip the voice is cloned from
        SAMPLE-CLIP-TODO.md
```

A character is a directory under `characters/`. It has:

- **`persona.md`** — the character's identity and inner life, written as two labelled
  sections (`## IDENTITY` and `## SOUL`). This text is composed into the system prompt at
  startup. See [Authoring a character](../../docs/authoring-a-character.md) for the format.
- **`voices/<name>/`** — one or more voice bundles. Each is a self-contained folder holding a
  `voice.toml` descriptor and the reference clip it points at. A character can have several
  voices; you pick the live one in `config/active.toml`.

## Making it yours

The fastest way to a companion of your own: copy `characters/example/` to a new directory,
rewrite `persona.md`, and swap in your own voice clip. Then point `config/active.toml` at the
new character and voice, and restart.

`example` is meant to be replaced or adapted — treat it as a template, not a fixture.
