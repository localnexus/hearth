# Weights, and where they live

*Authoritative sources: [`config/weights.toml.example`](../../../../config/weights.toml.example) ·
[`docs/config-manual/llm.md`](../../../../docs/config-manual/llm.md) · `python -m hearth.weights --help`.
This page is the felt version; those three are the spec.*

The model file is the biggest single thing Hearth touches — tens of gigabytes that took you a
download, a decision, and some disk you had to find. So Hearth's rule about it is short:

> **Hearth points at your weights. It never owns them.**

It will not download a file, move one, copy one, or delete one. Not on setup, not on a switch, not
when you tell it to forget a model. If you drag the file somewhere else tomorrow, Hearth notices and
says so; it does not go looking for a replacement.

## A root is just a folder

A **root** is a directory you are willing to let Hearth look in. That is the whole idea. You name
yours in `config/weights.toml`:

```toml
[weights]
roots = ["/Users/<you>/models"]
```

Hearth also carries three built-in pointers, on by default, at the folders the popular
model-serving apps keep files in — LM Studio's, Ollama's, and the Hugging Face cache. Those are
**paths, and only paths**. Hearth opens the files it finds there and reads their headers, the same
way it reads yours. It never asks those apps anything, never runs their commands, never reads their
private index files. Quit them, uninstall them, rename the folders: a scan gives you the same
answer, because none of them was ever part of the answer.

If one of those folders turns out to already live inside a root of your own — a common arrangement,
where the apps' folders are shortcuts into one models directory you keep — Hearth quietly skips it
rather than showing you everything twice.

## The landing folder

Weights that arrive from here on can go somewhere that is Hearth's, not some app's:
`<your first root>/hearth/`, subdivided by what the file is for — `llm/`, `tts/`, `stt/`. Nothing
forces you to use it. It exists so another program's library stops growing every time you try a new
model on Hearth's account. Today only `llm/` is looked at; the speech models still fetch themselves,
and they get the same treatment when that lands.

## What enrolling means

**Enrolling** writes one reference — the file's real path — into a model directory's `model.toml`,
under `[weights]`, alongside a note of what the file said about itself the day you did it: the
architecture, how many layers, how much context it was trained for. That is the entire act. The
weights file is not read again until the model server loads it.

**Un-enrolling** deletes those lines. Nothing else happens. The file is still on your disk, exactly
where it was, byte for byte.

## The seven things you can ask

```
python -m hearth.weights roots      # which folders, and where each came from
python -m hearth.weights scan       # what is in them, and what fits this machine
python -m hearth.weights enroll <model> --key <what scan called it>
python -m hearth.weights unenroll <model>
python -m hearth.weights check      # is it all still there
python -m hearth.weights render <model>   # the unit your model server would run under
python -m hearth.weights apply <model>    # put that unit where the machine reads it
```

`scan` is the one worth looking at. Each line names a file, its size, its architecture, the context
it was trained for, and a verdict: **fits**, **fits at ctx 131072** (it will, but not at the full
window), or **too large**. The verdict is an estimate for your eyes — the model server does its own
arithmetic when it loads, and what it reports afterwards is the truth. Some lines carry `dup`: the
same bytes are sitting somewhere else on your disk too. That is worth knowing before you buy more
storage; it is your call what to do about it, and Hearth will not do anything about it for you.

`enroll` shows you everything it is about to write and then stops. Read it, then run it again with
`--yes`. Same habit as everywhere else in Hearth.

## Changing the model, end to end

On a machine where the model server is kept up by the system — a launchd unit on
a Mac, a service file elsewhere — that unit holds a long command line: the weights
path, the context, the port, the key file, everything. It is the same set of facts
you already keep in Hearth's config, written a second time by hand, and the two
drift the moment you change one.

They don't have to be two things. `render` builds that command line **from** the
config, and `apply` puts the result where the system reads it. So changing the
model is four steps, each of which shows you what it will do first:

```
python -m hearth.weights enroll my-model --key <what scan called it> --yes
python -m hearth.weights render my-model --diff     # what would change, and is any of it real?
python -m hearth.weights apply  my-model --yes      # writes the unit; prints the two lines to run
```

and then the fourth step is **yours**: the two lines `apply` printed, which stop
the old process and start the new one. Hearth prints them and does not run them,
because restarting a model server mid-conversation is not a decision a tool should
make for you. `apply` also refuses outright while a companion is running, and
archives the unit that was already there as `<name>.prev-<date>` — so going back
is copying one file into place and running the same two lines.

Three facts about `--diff` make it the step worth reading. It compares as *sets of
flags and values*, so the order the two files list things in doesn't matter. It
knows that a **placement** flag — how many layers on the GPU, how to split across
devices — is one the model server works out for itself now, so a hand-written unit
that pins them and a rendered one that doesn't are the same unit. And it knows one
spelling from another: `--load-mode mlock` says what the older `--mlock` plus
`--no-direct-io` pair said. Everything else is a **real** difference, and a real
difference is the only kind that makes it stop.

The door's own facts — its label, address, the PATH to its access key, how it loads
the file, where it logs — live in `config/weights.toml` under `[weights.door]`, next
to the roots. A model's own flags live in that model's `model.toml` under `[server]`.
That line is the whole design: swap models all day and the door table never moves.

## What "missing" looks like

You delete the model through the app you downloaded it with. Or you move your models folder to a
different disk. Then:

```
[ERROR] my-model: weights missing — /Users/<you>/models/.../model-Q8_0.gguf is not there any more
```

That is `check` telling you plainly. It will not quietly load a different copy it found somewhere
else, and it will not go and fetch one. The model server stays down and the message says why —
because the alternative is Hearth deciding, on its own, which weights you meant. Re-enroll against
the file's new home, and it comes back up.

## Net

Roots are folders you allow. Enrolling is a pointer plus a note. The file is always yours: Hearth
reads it, never rearranges it, and tells you plainly when it can no longer find it.
