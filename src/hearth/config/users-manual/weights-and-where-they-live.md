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

## The five things you can ask

```
python -m hearth.weights roots      # which folders, and where each came from
python -m hearth.weights scan       # what is in them, and what fits this machine
python -m hearth.weights enroll <model> --key <what scan called it>
python -m hearth.weights unenroll <model>
python -m hearth.weights check      # is it all still there
```

`scan` is the one worth looking at. Each line names a file, its size, its architecture, the context
it was trained for, and a verdict: **fits**, **fits at ctx 131072** (it will, but not at the full
window), or **too large**. The verdict is an estimate for your eyes — the model server does its own
arithmetic when it loads, and what it reports afterwards is the truth. Some lines carry `dup`: the
same bytes are sitting somewhere else on your disk too. That is worth knowing before you buy more
storage; it is your call what to do about it, and Hearth will not do anything about it for you.

`enroll` shows you everything it is about to write and then stops. Read it, then run it again with
`--yes`. Same habit as everywhere else in Hearth.

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
