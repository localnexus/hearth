# Quick guide: uninstall Hearth

Hearth is a folder on your computer. Hearth itself never starts at login, and no account exists
anywhere. Removing it is deleting files.

## Stop it first

If Hearth is running, press Ctrl-C in the terminal window that is running it. If you started
a model server too, stop that window as well.

## Delete the Hearth folder

The install puts Hearth in a folder called `hearth` in your home folder unless you chose
somewhere else:

```bash
rm -rf ~/hearth
```

If you told the install to use a different place, delete it where you put it.

That one folder holds the program, the Python setup it runs on, your settings, your access
key, your characters, your voices, and your saved conversations. Deleting it removes all of
them. They are gone, and nothing is kept anywhere else about them.

One thing to check first. If you set `HEARTH_DATA` when you set Hearth up, then your
characters, settings, and conversations live in that folder instead, wherever you put it.
Delete that folder too if you want them gone.

## What stays behind

A few things live outside the Hearth folder on purpose, because other programs may share
them. Deleting the Hearth folder leaves them alone.

**The speech models.** These are the files that let Hearth hear and speak, about 4.6 GB. They
sit in a shared cache in your home folder:

```bash
rm -rf ~/.cache/huggingface
```

Other programs use that same cache, so look inside before you delete the whole thing.

**A model server you set to start at login.** Only if you followed the model-server guide and
installed its login item. Hearth never does this on its own. Remove it like this:

```bash
launchctl bootout gui/$UID/com.hearth.llm
rm ~/Library/LaunchAgents/com.hearth.llm.plist
```

**The language model.** Hearth never downloaded it. You did, when you set up your model
server, so it is wherever you or that server put it. Those files are large, so it is worth
finding them.

**The tools Homebrew installed.** The install added three: PortAudio for sound, `uv` for the
Python setup, and `llama.cpp` for running a model. They are ordinary tools and other things
may use them. Remove them only if you are sure:

```bash
brew uninstall llama.cpp uv portaudio
```

The Xcode command-line tools and Homebrew itself were already yours to keep. Neither is part
of Hearth, and this guide does not touch them.

## Coming back later

Reinstalling is the same one command as the first time. The speech models are already in the
cache if you left them there, so the second install is much faster.

**Go deeper:** [Installing Hearth](../installing.md) and
[Installing by hand](../installing-by-hand.md).
