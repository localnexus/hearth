# The voice delivery knobs — in your ear

*How the companion's cloned voice turns text into sound, and the four dials the panel's* **VOICE** *box
gives you over that — live, per voice sample.*

> **Not the same knobs as the words.** These are separate from — and unrelated to — the LLM sampling
> knobs on [The generation knobs](generation-knobs-in-your-ear.md). Confusingly, two of them share a name:
> the LLM has its own `top_p`/`top_k` (your LLM server's, not live here) and the *voice* has its own
> `top_p`/`top_k` (Hearth's, live on this page). They never touch the same file or the same moment — one
> shapes which words get picked, this one shapes how the picked words get spoken.

These four live in `config/tts/<engine>/tts.toml` as the shipped baseline, and the panel's **VOICE** box
moves them live into `config/overrides.toml` per the voice sample you have selected — mechanics in
[The live knobs panel](the-live-knobs-panel.md).

---

## temperature — the emotional-range dial  · *ships at 0.8*

**What it is:** how much the delivery is allowed to vary from the flattest, safest reading of the line.

**In your ear:** low and the voice reads evenly — composed, controlled, and past a point flat or
monotone, like it's reciting rather than feeling the line. High and the delivery gets more dramatic and
animated, closer to how a person actually says something they mean — until it's too high and the delivery
turns erratic, even garbled.

**Turn it up when** the voice sounds robotic or clipped, or a line that should land with feeling comes out
flat. **Down when** the delivery starts to wander — odd emphasis, a warble, a line that doesn't sound like
a sentence anymore.

**Net:** the companion's answer to the LLM's `temperature`, but for *how it's said* rather than *what's
said*. 0.8 is the shipped middle; nudge up first if the voice sounds like it's reading off a card.

## top_p — the pronunciation net  · *ships at 0.95*

**What it is:** how wide a pool of acoustic choices (pitch, pacing, emphasis) the voice draws from at each
step, before `temperature` picks among them.

**In your ear:** tighter and the delivery stays consistent and safe, turn after turn — reliable, if a
little same-y. Looser and you get more natural variation in cadence — and more room for an odd pause or a
mispronunciation to slip through.

**Turn it down when** you're hearing occasional stalls or dropped words. **Up when** the voice sounds
repetitive in its phrasing even with `temperature` raised.

**Net:** the guardrail around `temperature`, same relationship as the LLM's word-net has to its own
temperature — just a different net, for sound instead of words.

## top_k — how many acoustic options get weighed  · *ships at 1000*

**What it is:** a hard cap on how many candidate sounds the voice considers at each step, before `top_p`
and `temperature` narrow further.

**In your ear:** low and the delivery is steadier and more predictable, turn to turn. High and there's more
variety in how a phrase lands — closer to a live performance — at some cost to stability.

**Turn it down when** the voice feels unstable — stalls, repeated syllables. **Up when** you want more
natural variation and stability isn't the problem.

**Net:** rarely the first knob to reach for — `temperature` does most of the work. Lower this only if
you're chasing a stall that `temperature` alone doesn't fix.

## repetition_penalty — keeps the delivery from looping  · *ships at 1.2*

**What it is:** how strongly the voice avoids repeating the same acoustic pattern (a cadence, a rhythm) it
just used.

**In your ear:** set it high and cadence stops looping — but push it and the delivery can turn *too*
varied, even distorted, or drop sounds trying to avoid repeating them. Set it low (toward 1.0) and a long
or repetitive line can fall into a sing-song loop, the same phrase-shape over and over.

**Turn it up** only if you can hear the voice looping a rhythm. **Down toward 1.0** if longer replies are
coming out oddly distorted or clipped.

**Net:** a stability dial you'll rarely need to move — the shipped 1.2 is already ear-calibrated. Move it
only chasing one of the two specific symptoms above.

---

## Quick aim

| You hear… | Reach for… |
|---|---|
| Flat, robotic, reading off a card | **temperature ↑** |
| Warbling, odd emphasis, doesn't sound like a sentence | **temperature ↓** |
| Stalls or dropped words | **top_p ↓** |
| Repetitive phrasing even at higher temperature | **top_p ↑** |
| Unstable / stalling and `temperature` alone didn't fix it | **top_k ↓** |
| Looping the same cadence | **repetition_penalty ↑** |
| Distorted or dropped sounds on longer replies | **repetition_penalty ↓** (toward 1.0) |

**Always:** these are priors to aim your listening, not a substitute for it — the same rule as the
generation knobs. Change one thing, hear a reply or two, keep or revert.
