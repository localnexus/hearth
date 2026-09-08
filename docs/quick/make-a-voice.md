# Quick guide: make a voice

Hearth copies a voice from one short recording. A clean, rights-free voice already comes with
it. This guide is for adding another one.

## Read this first

The responsibility for a cloned voice sits with you.

- Only use a voice you have the right to use. That means your own voice, a voice whose owner
  said yes, or a recording that is public domain or licensed for this.
- Never share a cloned voice of a real person without that person's consent. Copying someone's
  voice from audio you found and passing it around can break their rights and the law.
- The voice that ships with Hearth is deliberately clean on rights. Hearth never bundles or
  suggests any particular person's voice. That choice is yours, and so is what follows from it.

## Record a short clip

Any short, clean recording works. Ten to fifteen seconds is the right length, because the
voice engine listens to only the first fifteen seconds or so. Anything past that is ignored.

What matters is clarity, not audio settings:

- one person talking, with no music and no second voice
- a quiet room, with little echo
- an even, natural delivery, the way you want the companion to sound

You do not need to match any particular recording quality. Hearth converts the file to what
the voice engine needs when it loads it. Loudness is evened out for you as well.

The Voice Memos app or QuickTime Player on your Mac is enough to record it. Export or save
the result as a WAV file. If your recorder cannot make a WAV, Audacity is a free program that
can open your file and export one.

## Put it where Hearth looks

Inside your Hearth folder, make a folder for this voice under the character who will use it:

```
characters/yourname/voices/myvoice/
```

Put your recording in it, named `sample.wav`. Then add a small file beside it called
`voice.toml`:

```toml
tag = "myvoice"
ref_wav = "sample.wav"
license = "own-voice"
source  = "my own recording, 2026"
```

Only the first two lines are required. The other two are notes to yourself about where the
recording came from and that you have the right to it. Write them anyway. In a year you will
be glad they are there.

## Use it

Open `config/active.toml` and name the character and the voice:

```toml
character = "yourname"
voice = "myvoice"
```

Then stop Hearth and start it again. The selection is read once at startup.

A character can hold several voices side by side under `voices/`. Switch between them by
changing that one line and restarting.

**Go deeper:** [Bring your own voice](../bring-your-own-voice.md) and
[The config layers](../the-config-layers.md).
