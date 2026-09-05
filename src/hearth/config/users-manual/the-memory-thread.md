# The memory thread — what remembers, and what it's attached to

*One question this manual never answered, and everyone eventually asks: when a companion remembers
something, what is that memory attached to? The character? The voice? The persona? The answer decides what
survives a change, and it is not obvious from any screen.*

**Authoritative sources:** the design and every key → `docs/memory.md`, with the detail split across
`docs/memory/backends.md`, `session-modes.md`, `records-and-curation.md`, `per-turn-recall.md`, and
`serve-facade-lane.md`. Those are the truth. This page translates one thing they state in passing into what
it means for you.

---

## The short answer

**Memory is anchored to the character.** Nothing else.

The character picks which memory backend answers, which records directory fills up, and which running
checkpoint is kept. Every recall comes from that one place and every retained conversation goes back to it.

Voice is not part of it. Persona is not part of it — a persona is *recorded on* each conversation, the way a
letter carries a date, but it never divides one companion's memory into separate piles.

## What that means in practice

**Change the voice and the thread is untouched.** A voice bundle is a sound. Swap it, audition a different
one, pin a new one in the roster — the companion still knows everything they knew this morning.

**Change the persona and the thread is untouched.** This is the one that surprises people. If you keep two
persona files for the same companion and swap between them, both carry the *same* history. The second
persona is not a fresh acquaintance; it inherits every conversation the first one had. What changes is how
they sound and what they attend to, not what they know about you.

**Two characters never share, no matter what they have in common.** Give two characters the identical
persona text and the identical voice and they still keep two separate memories that never meet. The
character name is the whole boundary.

## Three words: conversation, session, history

Hearth uses three words for three different things, and the pages keep to them:

- A **conversation** is one thread of talk you can come back to — the saved conversation on disk, plus
  the memories it has left behind. The launch page lets you open a new one or pick one off the shelf.
- A **session** (in full, a *conversation session*) is one sitting inside it: from **Start** to **Stop**,
  today. A conversation can hold many sessions across weeks. Each session picks its own memory mode.
- **History** is what the companion knows of you across *all* your conversations. Think of a conversation
  as one notebook you can reopen and keep writing in; history is what your companion has learned from every
  notebook so far. A new conversation starts on a blank page, and the companion still knows you.

You will see conversations described by *channel* — a desk session and a walk on the phone are tracked
separately while they are open, so two live conversations can't overwrite each other mid-sentence.

That separation ends when they close. Both land in the same history. A walk and a desk session are two
sessions with one companion, not two companions — and the next conversation recalls from all of it.

## The three modes, per session

Each session runs in one of three modes, chosen when it starts and shown on the panel's **Memory**
line while it runs:

| Mode | Recall | Retain |
|---|---|---|
| `full` | yes | yes — this session becomes part of the thread |
| `recall-only` | yes | **no** — they remember everything, this session leaves no record |
| `off` | no | no — a fresh meeting, and nothing is written |

`recall-only` is the useful middle one: a conversation you'd rather not add to the record, held by someone
who still knows you. The mode is picked on the launch page before starting, and it is a property of *that
session* — a resumed conversation keeps its own mode unless you say otherwise.

## How a session becomes a memory

A conversation is written to the running checkpoint as you go, so a crash never costs the whole session. The
durable record is written when the conversation **closes**, and there are three ways that happens: you close
it deliberately, it goes quiet long enough to be swept, or Hearth shuts down. A checkpoint left behind by
something that died is finalized at the next start.

**One honest gap.** Only a *deliberate* close captures the session's intent — the "here's what we decided"
marker. Walking away from a conversation still keeps the record, in full; it just doesn't leave that marker
behind. If a session mattered, ending it on purpose is worth the ten seconds.

## When you actually want a separate thread

Sometimes the answer to "I want a version of them who doesn't carry all this" is a genuinely different
companion. That is what the **branch** card on the roster page is for: it makes a new character from an
existing one at a chosen juncture, carrying the history up to that point and nothing after it. From then on
the two remember separately, because they are two characters — which is the only unit of separation there is.

Branching from a companion with no records starts them with no memory at all, which the card tells you before
you commit.

## It ships switched off

None of this is on out of the box. The switch lives in `config/memory.toml`, which ships only as an example,
with memory disabled — a fresh install composes prompts exactly as if the feature did not exist. Once the
Hearth is running, the **memory** section of the settings form turns it on and picks the backend; until
then that file's switch is the only door. A companion can also be opted out individually, so one character can
remember while another deliberately doesn't.

---

**Net:** the character is the thread. Voices and personas are things a character *wears*, and changing them
changes how a companion sounds and behaves, never what they remember. If you want a second memory, you want
a second character — and the roster page's branch card is how you get one without starting from nothing.
