# Reading the panel — the CTRL surface at :65000

*What's actually on the control panel when you sit down: talking to the companion by text, the mic
controls, the Record button, and the status lines underneath — what each one is telling you, and the one
runtime button on the Memory line.*

**Authoritative sources:** the control panel → `docs/runbook/02.5-control-panel.md`. This page walks what
you *see*; that doc is the fuller reference. This is the **CTRL** destination — the rail's other
destination, **MANUAL**, is this book, and switching between them never resets anything on either side.

---

## Talking without speaking

The text box sends a message to the companion exactly as if you'd said it aloud — useful for a quiet room,
or to slip in something you'd rather type. **Enter** sends; **Shift+Enter** starts a new line.

**Mute mic** is your **baseline**: click it once and the mic stays muted until you click it again — it
doesn't come back on its own. **PTT (hold)** is a **momentary** override on top of that baseline: hold the
button (or hold the **M** key while the page has focus, as long as you're not typing in the text box) and
the mic opens for as long as you hold it, then drops back to whatever the baseline was. The `MIC:` line
above the buttons always tells you which of the three states you're actually in — **LIVE**, **MUTED**, or
**OPEN (PTT held)**.

## Recording the session

**Record** always captures the companion's spoken side (TTS) of the conversation. The two tickboxes add
more: **my mic** adds your side, **background music** mirrors whatever audio is currently playing on your
machine into the capture — that one needs a loopback device (e.g. BlackHole) plus Multi-Output routing set
up on your Mac, and disables itself with an explanatory tooltip if that isn't in place. Give the capture a
name before you start if you want one; the field locks once recording is armed. The button turns red and
shows an elapsed timer while recording; stopping it either shows the saved mix path or, if nothing was
captured, the stems directory it looked in.

---

## The status block

Four lines are always there once the panel is up; a fifth — **Memory** — only shows when this companion has
memory configured at all.

**`Tokens`** is the context gauge: how many tokens are held in context right now, what percentage that is
of the **measured reliable line** for this model (not the raw advertised window — see
[The config layers](the-config-layers.md) on `reliable_context`), how much room is left before that line,
and the model's advertised/maximum window for reference. The line's color shifts as you approach the line —
and past 75% a banner appears underneath: *"approaching reliable context line — consider wrapping or
consolidating this session."* A separate banner, *"reasoning tokens leaked,"* appears if the model's
internal reasoning is showing up somewhere it shouldn't — that's a model/server issue to chase, not
something this panel can fix.

**`Engine`** names your model server's identity: the inference provider and the model ID it's actually
serving right now — re-checked roughly once a minute, so a model you swap on the server side surfaces here
without a Hearth restart.

**`Agent`** names who's live: the character and the voice speaking. If you've auditioned a different clip
live from the **VOICE** box's Sample dropdown (see [The live knobs panel](the-live-knobs-panel.md)), this
line shows both — the session's real baseline voice, and the one you're currently hearing instead.

**`Misc`** carries the conversation's name, this session's memory mode (`off` / `recall-only` / `full` — the mode
it *started* with; see [The config layers](the-config-layers.md)), the turn count, net context growth, and
total tokens sent so far.

## The Memory line

When this companion has cross-session memory attached, a fifth line appears: which backend answered,
whether it's running **recall-only** (remembers nothing new this session), what per-turn recall is doing
(`off`, `chat`, or `chat + voice`), how many facts were recalled at the start of the session and from where,
and how many extra facts the last turn pulled in and from where. A recall source is always named honestly —
a fallback path never poses as the primary backend.

Beside it, a **review & prune** link points at the memory curation page. That page itself lives on the
:65001 **Hearth**, not on this panel — write-layer operations like curation never happen on :65000. The
link only resolves when you're viewing this panel *through* Hearth's proxy; opened directly at
:65000 it may not go anywhere. If you need curation and don't have that proxy set up, reach Hearth
directly instead (see [The map of doors](the-map-of-doors.md)). What you'll find when you get there is
[The pages behind the door](the-pages-behind-the-door.md).

### The one runtime button: pause / resume voice recall

If this session started with per-turn voice recall on, a small button sits next to the Memory line reading
**pause voice recall** or **resume voice recall**. Pressing it flips whether the *voice* lane keeps pulling
in fresh recall each turn — text chat recall is untouched either way. It takes effect from the next turn:
what was pulled in rides only the turn it was fetched for, so pausing simply stops the next fetch, and
resuming picks recall back up from there.

This is deliberately **runtime-only** — it pokes the live session and nothing else. `config/memory.toml`
is never touched, so a restart or a live companion switch always returns to whatever the file says,
regardless of where you last left this button. The button itself only appears when the session actually
built the voice-recall machinery at startup (voice recall has to have been on when the companion started) and the
current companion's per-turn chat switch is on — if either isn't true, there's nothing here to pause, so the
button stays hidden rather than offering a control that would just refuse.

---

## Net

The panel's core loop — type or speak, mute or PTT, optionally record — sits above a status block that's
read-only except for one thing: the Memory line's pause/resume button, a same-session-only knob that never
writes a file. Everything else you can *change* here (CHARACTER/VOICE/LISTENING sliders, the COMPANION
switcher) has its own page: [The live knobs panel](the-live-knobs-panel.md) and
[The one-button switch](the-one-button-switch.md).
