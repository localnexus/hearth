# The pages behind the door — the `/admin` surface at :65001

*The six pages the facade serves when the supervisor gate is on: what each one lets you do, the single
unlock they share, and the two habits that repeat on every page that writes something. This is the sibling
of [Reading the panel](reading-the-panel.md) — that page covers the `:65000` panel you sit at, this one
covers the pages on the other port.*

**Authoritative sources:** route by route → `docs/runbook/02.5-control-panel/admin-surface.md`; the launch
page in depth → `docs/runbook/02.5-control-panel/launch-page.md`. Which port is which →
[The map of doors](the-map-of-doors.md). Whether this door exists at all is one gate, covered in
[The one-button switch](the-one-button-switch.md) — **the supervisor ships off**, and with it off none of
this is there.

---

## Getting in, once

All six pages arrive **empty**. They're plain chrome with nothing baked in — no names, no state, no key —
and everything you see fills in afterward, fetched with your key attached. So a page that looks blank is
almost always **locked, not broken**.

The first one you open asks for the serve bearer **once**. That browser keeps it and sends it as a header
from then on; nothing is stored on the Hearth side, and there's no session to expire. Change the token and
every browser simply asks again.

**On a phone, don't type it.** The bearer is 64 hex characters, which is not something anyone should be
entering on a handset — that's what the pairing page exists for, below.

One wrinkle worth knowing, because it explains a link that would otherwise look inconsistent: when you
*click through* to the control panel from one of these pages, a browser navigation can't attach a header.
So the launch page quietly mints a **carrier cookie** for that hop. It's derived from your bearer rather
than being it, no page script can read it back, and it lasts 30 days. That's the whole reason the panel
opens by clicking instead of answering "unauthorized."

## The two habits every writing page shares

Learn these once and four pages stop holding surprises.

**Nothing writes on the first press.** Settings edits, persona rewrites, forgetting a session, branching a
memory track — each one **previews first**: you're shown what would change, old value beside new, and when
it would take effect. The write happens on a second, explicit confirm. A refusal at any point leaves
everything untouched.

**A file that shipped with Hearth is copied before it's changed, never edited in place** — your edit lands
in your own data root, and one previous generation is kept beside it. Undo is a rename, not a rebuild.

---

## `/admin/launch` — the standing surface

The page to leave open. It works whether the companion is running or not, from any device that can reach
the facade, and it never makes you remember a flag.

**When nothing is running**, it offers a start: who to bring up (the same companion card the panel carries —
character, voice, persona, model), which **conversation** to open (a new one, or one off the shelf), and this
session's **memory posture** — remembering and recording normally, remembering but leaving no record behind,
or a fresh meeting with no memory at all. There's also a button to **compact** a selected conversation first,
which shrinks a long transcript before it's reopened.

Conversation and memory are **start-only, on purpose.** The posture is decided when the companion comes up and
doesn't change underneath a live conversation, so those two controls disappear once one is running rather
than offering something that would be refused.

**When someone is running**, the same card reads **Switch** instead of Start — that's the one-press
companion change, and it's covered in [The one-button switch](the-one-button-switch.md). Beside it: a
**Stop** button, with an optional field to name the conversation on the way out (it saves either way), and a link
straight into the control panel.

**The state line at the top is process truth, not a memory of what this page did.** It's re-checked every few
seconds, so a companion you started at the desk appears here on its own, and one you stopped at the desk
drops off within a poll. It is an honest is-anyone-running indicator no matter who did the starting.

**Externals** only appears if your install declares any — bring-up commands you've defined for services
around Hearth. Pressing **Run** holds until the command actually finishes; the wait *is* the progress bar.
This is what makes a service recoverable from the couch instead of only from the desk.

Two things this page deliberately won't do: it never restarts the facade underneath itself, and it never
touches your LLM server. Stopping a companion stops the companion.

## `/admin/first-run` — the first session

Offered by the launch page while an install is new — the selected model config still carries the shipped
placeholder id, or no companion here has a session yet — and reachable at its address any time after.
Three steps: **your LLM server** (is it answering at the address the facade uses, and which model ids it
advertises — pick one and it's recorded in the selected model config, verbatim, comments kept, previous
file beside it), **bring the companion up** (the same companion card as the launch page, with Start —
parked until the id is set, because a bot started against the placeholder can't reach a model), and **say
something** (the bot's own turn counter, read through the facade: warming, then listening, then "heard
you and answered"). It ends by pointing at the launch page, which is the front door from then on.

Two honest edges. The page **never moves the server address**: if nothing answers, it shows the one
command that does (`hearth.init --lm-url …`, run with the facade stopped), because an address saved here
would look applied while the running facade kept using the old one. And the model id is the **one write
that skips the preview**: the value is the server's own listing, so what lands is what the server already
answers to — an id the server doesn't list is refused unless you insist.

## `/admin/settings/ui` — every config file, as a form

The general answer to "where do I change this." Every setting Hearth knows about is rendered here as a
control — with its current value, what the default was, and a badge telling you **when a change would land**:
right away, at the next start, or at the next switch.

The forms are **generated from the same schema that validates the files**, which has a consequence worth
stating plainly: a knob cannot exist in Hearth without showing up on this page. There's no drawer of
settings that only the files know about.

The page won't let you write something broken. A value is checked against the schema before anything
happens, and a value that would introduce a *new* problem is refused — while problems the file already had
aren't held against you. Your comments survive the edit. Structured, multi-part values are left to a hand
edit rather than half-rendered as a form.

**Three things it deliberately refuses, and each one points somewhere real** rather than just saying no:

- **Who's currently live** isn't edited here — that has its own surface, the switch card, which applies it
  properly instead of just writing it down.
- **The live knob layer and saved presets** belong to the `:65000` panel, which owns them. Tune them there.
- **Secrets** — an API key, an environment map — are shown as dots and can't be written through a web form.
  Their values never leave the file, even behind the door.

## `/admin/memory/ui` — review and prune

Three steps, narrowing: the companions who have records, with counts → one companion's sessions, each with
its date, name, turn count, and a short digest → forgetting one, permanently.

**You read a digest, never the transcript.** That's deliberate and it holds everywhere in this surface —
reviewing what a session contained means reading a summary of it, not reopening the conversation.

Forgetting always **names the companion explicitly.** There's no "the active one" shortcut here, because
the failure this guards against is forgetting the wrong companion's session from a page that has no one in
front of it. And it works in the safe order: the remembered facts go first, the record second. If it fails
partway you can simply press it again — nothing ends up half-forgotten.

The heavier repair (rebuilding a companion's whole fact bank from scratch) stays at the desk, because it
re-reads every record through the local model and takes minutes rather than seconds. If the page meets a
case that needs it, it says so and names the command instead of pretending to offer it.

If your install has the richer memory backend switched off, this page still *shows* records honestly — the
counts and digests come from the record files themselves. It's the pruning verbs that stand down.

You can also reach this page from the panel: the **review & prune** link on the Memory line is this page,
which is why that link only resolves when you're viewing the panel through the facade.

## `/admin/pair/ui` — the six digits

For handing a phone the key without typing 64 characters into it.

At the desk you open a pairing window; on the device you open this page and enter **six digits**. The device
trades them for the key, keeps it, and lands on the launch page ready to use.

What keeps six digits honest on a page that necessarily accepts them *without* a key: exactly **one** code
exists at a time, it lives for about **five minutes** after you deliberately ask for it, it's **burned the
moment it's used**, and **three wrong guesses burn it too**. Every failure answers the same way, so a wrong
guess never tells anyone which part was wrong.

## `/admin/roster` — named here, walked elsewhere

The fifth page is the roster: bringing in a new companion, adding a voice to one you already have, editing a
persona, or branching a memory track. It has a chapter of its own —
[Onboarding a character](onboarding-a-character.md) — and isn't repeated here.

---

## Net

Unlock once per browser (or pair a phone with six digits), and the facade's pages cover the things that used
to mean stopping to edit a file: **first run** walks a new install to its first words; **launch** starts,
stops, resumes and switches; **settings** renders every
knob as a control that refuses bad values; **memory** reviews and prunes what a companion holds; **roster**
brings companions in; **pairing** gets a device through the door. Every one of them shows you the change
before it makes it, and keeps the previous version behind you.
