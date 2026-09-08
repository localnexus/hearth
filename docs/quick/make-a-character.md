# Quick guide: make a character

A character is who your companion is: their name, their manner, and what they care about. It
lives in a folder you can read, copy, and delete.

## Start from the example

Inside your Hearth folder there is a folder called `characters`. Each character is one folder
inside it. The quickest start is to copy the one that ships and edit it:

```bash
cp -R characters/example characters/yourname
```

The folder name is the character's name as far as Hearth is concerned. Keep it short and
plain, with no spaces.

A character folder holds two things: a file describing the person, and at least one voice.
Copying the example gives you both, so the character works right away and you can change it
piece by piece.

## Write who they are

Open `characters/yourname/persona.md`. This is the text the model is given about who to be.
Write it as a description, not as sample dialogue.

It needs two sections, and both must have something in them:

```markdown
## IDENTITY

Who the character is: name, role, manner, and how they relate to the person they are
talking with. Writing it as direct address works well, as in "You are ...".

## SOUL

The inner life: values, humor, quirks, how they speak, what they care about, what they
stay away from. This is where the personality actually lives.
```

Both headings are required. If either section is empty, Hearth stops at startup and says so
in plain words.

Three things that help:

- **Keep it to a few paragraphs.** This text is read on every turn. A tight, vivid
  description beats a long file of facts.
- **Describe manner, not just history.** How someone talks shapes replies more than their
  backstory does.
- **Write your own.** Do not paste in a character that belongs to somebody else.

Anything you put inside an HTML comment is stripped out before the model sees it, so you can
leave notes to yourself in the file freely.

## Pick them

Open `config/active.toml` and name the folder you made:

```toml
character = "yourname"
model = "example"
voice = "default"
```

That file is yours to edit. It is the one deliberate lever for who is live. There is a second
settings file in the same folder that the control panel writes for itself. Leave that one
alone.

## Restart

Stop Hearth and start it again. The selection is read once at startup, so a change while it
is running does nothing until then.

To give the character a voice of your own, see [make a voice](make-a-voice.md).

**Go deeper:** [Authoring a character](../authoring-a-character.md) and
[The config layers](../the-config-layers.md).
