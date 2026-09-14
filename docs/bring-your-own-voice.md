# Bring your own voice

Hearth clones a voice from a single short reference clip. To give a character a new voice, you
supply one clean recording and a small descriptor file. Hearth ships the cloning *capability*
and one public-domain default voice — everything beyond that is yours to add.

## Rights and consent — read first

Voice cloning carries real responsibility, and it sits with you, the operator:

- **Only use voices you have the rights to.** That means your own voice, a voice you have
  explicit permission to use, or audio that is public domain or licensed for the purpose.
- **Never distribute a cloned voice of a real person without their consent.** Cloning a
  specific person's voice from found audio and sharing the result can violate their rights and
  the law. Keep such things private, or don't make them.
- **The default that ships is deliberately rights-clean** (a public-domain clip). Hearth never
  bundles or recommends any particular person's voice — that choice, and its consequences, are
  yours.

## What makes a good reference clip

- **One clean 10–15 second clip.** The cloning model conditions on only the first ~10–15
  seconds; anything past that is ignored, so trim to your best segment.
- **Any sample rate.** You do not need to resample: Hearth converts the file to what the voice
  engine needs when it loads it. A mono recording is best.
- **Clean and steady.** No background music, no other speakers, minimal noise and reverb, even
  delivery. Loudness is normalized automatically, so don't worry about matching levels — worry
  about clarity and consistent tone.

## Adding the voice

1. Create the bundle directory under the character:

   ```
   characters/yourname/voices/myvoice/
   ```

2. Put your trimmed clip in it as `sample.wav` — any sample rate, mono preferred; it is converted when the voice loads.

3. Add a `voice.toml` descriptor pointing at it:

   ```toml
   # The voice tag — recorded alongside sessions so a resumed conversation can warn you
   # if the live voice changed. By convention it matches the directory name.
   tag = "myvoice"

   # The reference clip, relative to this directory (an absolute path also works).
   # The file must exist — it's read at startup.
   ref_wav = "sample.wav"

   # Provenance. Record where the clip came from and that you have the rights to it.
   license = "own-voice"          # or: consented / public-domain / cc-...
   source  = "my own recording, 2026"

   # Optional engine facts, for the record only — the engine's output rate, not a requirement on your clip.
   model_repo         = "mlx-community/chatterbox-turbo-fp16"
   sample_rate        = 24000
   streaming_interval = 2.0

   # Loudness: a linear multiplier on the synthesized samples (not dB), applied before the output is clipped. 1.0 = as synthesized.
   # Keep one reference voice at 1.0 and set the others relative to it; a voice that needs more than about 2x is a finding about the clip, not a number to set.
   loudness = 1.0
   ```

   Only `tag` and `ref_wav` are required; everything else is optional metadata.

4. Point `config/active.toml` at it and restart:

   ```toml
   character = "yourname"
   voice = "myvoice"
   ```

That's it — Hearth prepares the clip at startup and the character speaks in the new voice.

## Multiple voices

A character can hold several voice bundles side by side under `voices/`. Switch between them
by changing `voice` in `config/active.toml` (and restarting), or audition them live from the
control panel.

Once you have auditioned your way to a keeper, name it in that character's own `profile.toml` so the
switch pickers reach for it instead of whichever bundle happens to sort first:

```toml
# characters/yourname/profile.toml
voice = "myvoice"
```

One line, at the top of the file (a bare key written after a `[llm]` table would belong to
that table). It changes nothing about who is live — `active.toml` remains the selection —
only what the picker offers the moment you move *to* them. An absent or stale pin falls back
to first-in-list, so nothing breaks if you rename a bundle.
