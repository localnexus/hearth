# Authoring a character

A character in Hearth is a directory under `characters/`. It bundles a **persona** (who the
character is) with one or more **voices** (how they sound). This guide covers writing the
persona and laying out the directory; adding a voice has its own guide,
[Bring your own voice](bring-your-own-voice.md).

## Directory layout

```
characters/
  yourname/
    persona.md              ← required: who the character is
    voices/
      default/
        voice.toml           ← required: the voice descriptor
        sample.wav           ← the reference clip the voice is cloned from
```

The directory name (`yourname` above) is the character's id — it's what you put in
`config/active.toml` under `character`. A character needs at least a `persona.md` and one
voice bundle.

The quickest start is to copy `characters/example/` and edit from there.

## Writing `persona.md`

`persona.md` is the text the model is given about who to be. It is composed into the system
prompt at startup, so write it as **description**, not dialogue.

It must contain two labelled sections, each non-empty:

```markdown
## IDENTITY

Who the character is: name, role, disposition, and how they relate to the person they're
talking with. Written as a direct address works well ("You are ...").

## SOUL

The inner life and voice: values, quirks, humor, how they speak, what they care about, what
they avoid. This is where personality actually lives.
```

Both section headers are required and both bodies must be non-empty — startup fails with a
clear error otherwise. The composed persona is the IDENTITY body followed by the SOUL body.

### Authoring notes with HTML comments

Anything inside an HTML comment is **stripped before the prompt is composed** — it never
reaches the model. Use comments freely for guidance to yourself or other authors:

```markdown
<!-- Reminder: keep the replies short; the character tends to ramble at high temperature. -->
```

### Tips

- **Keep it a few paragraphs.** The persona is read every turn; a tight, vivid description
  beats an exhaustive dossier.
- **Describe voice and manner, not just facts.** How the character talks shapes replies more
  than their backstory does.
- **Let the model layer handle format.** The "speak for the ear, no markdown, no stage
  directions" rules live in the model's `system-prompt-template.md`, not in your persona — so
  you don't need to repeat them. Write personality; the template keeps it speakable.
- **Make it original.** Write your own character. Don't paste in text or personas that belong
  to someone else.

## Wiring it up

Once the directory exists, point `config/active.toml` at it:

```toml
character = "yourname"
model = "example"
voice = "default"
```

Then restart the pipeline — the selection is read once at startup.
