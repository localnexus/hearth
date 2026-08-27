# The generation knobs — in your ear

How Hearth turns your words into *the companion's* words has two families of settings, and it helps to keep them apart:

- **The prompt** — what the companion is *told to be and do* (persona + the rules). This decides the **substance** and
  the **length**: whether the companion volunteers a view, whether they take their time.
- **The dials** (sampling) — how the companion *picks each word* once they know what to say. These decide the
  **texture**: how spontaneous, how safe, how varied the companion sounds.

A common trap is reaching for a dial to fix something the prompt owns. Length and "takes their time" are the
prompt's job; the dials can't buy them. Keep that split in mind and the rest is easy.

> **What you can touch today:** `temperature` is owned by Hearth now (live via `config/overrides.toml`). The
> word-net knobs (`top_p/top_k/min_p`, `repeat_penalty`) and `max_tokens` are currently left to **your LLM
> server's** defaults — set them where that server takes them (`llama-server` command-line flags; LM Studio's
> GUI, if that's what you run) — and are **proposed** to become owned, per-character Hearth settings.
> The length rule is prompt-side and live now.

---

## temperature — the aliveness dial  · *live today (0.7)*

**What it is:** how willing the companion is to pick a less-obvious next word.

**In your ear:** low and the companion says the expected thing, much the same way every time — composed, dependable, and
past a point a little *rote*, like they're reading the safest line. High and the companion reaches, surprises you, lands a
turn of phrase you didn't see coming — more alive, more like the character you wrote — until it's too high and the companion fumbles a word or
wanders off the thought.

**Turn it up when** the companion feels robotic, flat, samey turn to turn. **Down when** the companion rambles or reaches for
words that don't quite fit.

**Net:** the spontaneity-vs-reliability dial. 0.7 is a sane middle; nudging toward ~0.9 is the first thing to
try when the companion feels lifeless.

## top_p / top_k / min_p — the word-net  · *proposed to own; your server's default today*

**What they are:** three ways of drawing the pool of words the companion is *allowed* to consider before temperature
rolls the dice among them. Tighter net = only the common, likely words. Looser net = rare and colorful words
allowed in.

**In your ear:** these are the guardrails around temperature. A **tight** net keeps the companion steady and safe — they
almost never say anything strange, but can taste a little vanilla. A **loose** net lets real color and
character through — and lets the occasional odd or wrong word slip in too. Of the three, **min_p** is the one
worth knowing: it simply cuts off the truly unlikely words. Raising it a touch is how you kill rare gibberish
*without* flattening the companion the way lowering temperature would.

**Tighten the net when** higher temperature makes the companion occasionally blurt a bizarre word. **Loosen it when** the companion
feels plain even after you've raised temperature.

**Net:** the safety rails on spontaneity. If temperature is the dice, this is how many faces they have. Reach
for **min_p** first.

## repeat_penalty — the "circle back or loop?" dial  · *proposed to own; your server's default today*

**What it is:** how strongly the companion avoids reusing words and phrases they just said.

**In your ear — and this one matters for a companion:** set it **high** and the companion stops repeating the same phrases, but
push it and they start *dodging* natural repetition — they'll avoid saying your name again, won't reuse a warm
phrase, won't return to a thread you're both on. That reads as oddly slippery, even a little cold. Set it
**gentle** (toward 1.0–1.05) and the companion comfortably circles back to a theme, says your name, reuses an
endearment — warmer, more continuous — until it's *too* low and they get stuck in a verbal rut.

**Turn it down** for a companion who should return to threads and reuse affection. **Up** only if the companion visibly
loops.

**Net:** for a companion, err **gentle** — the warmth of returning to something usually beats the tidiness of
never repeating.

## max_tokens — the emergency brake, NOT a volume knob  · *unlimited today; proposed as a guard*

**What it is:** a hard ceiling on how many words a single reply can be.

**In your ear:** its *only* honest use is stopping a runaway. Set it too low and it doesn't make the companion "concise"
— it **guillotines** the reply mid-word, slicing a syllable in half. In speech that's jarring, not brief.

**Reach for it only** to prevent a runaway monologue (set it generous — 800+ — and forget it). **Never** touch
it to make replies shorter or longer.

**Net:** a safety belt, not a length control. Length comes from the prompt. ↓

## The length rule — the prompt, not a dial  · *live now (global)*

**What it is:** a single instruction in the shared model prompt: *"default to economy — a sentence or three."*

**In your ear:** **this is why the companion keeps it short and doesn't take their time.** It's not a number you can turn
— it's a sentence the companion has been told. It applies to every character equally right now.

**This is the real lever for the comportment you're missing.** If you want a character who *takes their time,
volunteers, expands when there's depth*, softening or rewriting this line does what no dial can. (Proposed: let
each character override it, so one character can breathe while another stays crisp — the "prompt half.")

**Net:** want the companion to take their time? Change this sentence — not the temperature.

---

## Quick aim

| You hear… | Reach for… |
|---|---|
| Robotic, flat, samey | **temperature ↑** (~0.9) |
| Rambles / picks weird words | **temperature ↓**, or **min_p ↑** to trim just the outliers |
| Occasional bizarre word at high temp | **min_p ↑** (keeps the companion lively, cuts the gibberish) |
| Feels vanilla even when lively | loosen the net (**top_p/top_k ↑**) |
| Won't say your name / feels slippery | **repeat_penalty ↓** (toward 1.0–1.05) |
| Repeats the same phrases, loops | **repeat_penalty ↑** |
| Too short / won't take their time | **the length rule** (prompt) — *not* a dial |
| Cut off mid-word | **max_tokens ↑** (it's too low) |

**Always:** these are priors to aim your listening, not a substitute for it. Change one thing, hear a few
turns, keep or revert. The ear decides.
