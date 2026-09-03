# The listening calibration — in your ear

*The four dials behind the panel's* **LISTENING** *section — how surely, and how quickly, Hearth decides
you've started or stopped talking. Calibration, not character: these travel with your room and your mic,
never with a companion.*

Unlike the [generation](generation-knobs-in-your-ear.md) and [voice delivery](the-voice-delivery-knobs.md)
knobs, these aren't about how the companion sounds — they're about how well it *hears you*. Set them up
once for a given mic and room, and leave them; switching characters or voices never touches this tier.
Mechanics (where LISTENING lives on the panel, why it's collapsed by default, the reset button) are in
[The live knobs panel](the-live-knobs-panel.md).

---

## confidence — how sure it has to be that's you talking  · *ships at 0.7*

**What it is:** the detector's certainty threshold before a sound counts as your speech.

**In your ear:** low and the companion reacts to more — but a marginal sound (a cough, a chair creak) can
sometimes trigger a turn that wasn't meant as one. High and it's stricter — soft or unclear speech, a quiet
aside, can get ignored entirely.

**Turn it down when** the companion is missing things you actually said, especially said softly. **Up
when** it's jumping in on sounds that weren't you talking to it.

**Net:** the first dial to touch if the companion feels like it's not listening — or is listening to
everything.

## start_secs — how long a sound has to hold before it counts  · *ships at 0.2*

**What it is:** the minimum duration a sound must sustain before it's treated as the start of you talking.

**In your ear:** low and the companion reacts to the briefest sounds — snappy, but a stray noise can start
a false turn. High and it waits for sustained speech before reacting — steadier, but your first word or
two can get clipped off the front of what it hears.

**Turn it up when** false starts from background noise are the problem. **Down when** your opening words
keep getting cut off.

**Net:** trades false starts against clipped openings. The shipped 0.2 leans toward responsiveness.

## stop_secs — how long a silence has to hold before you're "done"  · *ships at 0.5*

**What it is:** how much silence after your voice has to pass before the companion decides you've finished
your turn.

**In your ear:** low and replies come snappier — but the companion can cut in mid-sentence, especially on a
breath or a thinking pause. High and it waits patiently before answering — safer against interruptions, but
every reply feels a beat laggier to arrive.

**Turn it down** if replies feel sluggish to start and you don't pause much mid-thought. **Up** if the
companion keeps talking over you mid-sentence.

**Net:** the responsiveness-vs-interruption dial for turn-taking — the listening equivalent of the
generation knobs' spontaneity trade-off.

## min_volume — how loud you have to be  · *ships at 0.6*

**What it is:** the loudness floor a sound must clear to count as speech at all.

**In your ear:** low and even quiet speech gets picked up. High and only clear, louder speech registers —
you'd have to raise your voice to be heard at all.

**Turn it down when** speaking normally or quietly doesn't register. **Up when** background noise (a fan,
another room, quiet music) is being picked up as speech.

**Net:** rarely the first dial to move — try `confidence` first for a similar symptom. Reach for this one
specifically when it's *loudness*, not certainty, that's the problem (a quiet room vs. a noisy one).

---

## Quick aim

| You hear… | Reach for… |
|---|---|
| Missing soft or unclear speech | **confidence ↓** |
| Reacting to sounds that weren't you | **confidence ↑** |
| Your first word gets clipped | **start_secs ↓** |
| False starts from background noise | **start_secs ↑** |
| Cuts in on you mid-sentence | **stop_secs ↑** |
| Replies feel laggy to start | **stop_secs ↓** |
| Quiet speech isn't heard at all | **min_volume ↓** |
| Background noise (fan, other room) registers as speech | **min_volume ↑** |

**Always:** calibrate by ear, in the room and on the mic you actually use — a setting tuned at a desk with
a headset mic won't necessarily suit a phone across the room.
