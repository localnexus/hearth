# Quick guide: install Hearth

Hearth runs on a Mac with an Apple chip. Set aside about an hour the first time. Most of
that hour is waiting for downloads, not typing.

## One command

Open a terminal window and paste this line:

```bash
curl -fsSL https://raw.githubusercontent.com/localnexus/hearth/main/install.sh | bash
```

It checks your Mac, installs the few tools Hearth needs, copies Hearth into a folder called
`hearth` in your home folder, and builds the small Python setup it runs on. One line is
printed per step, so you can see what happened. Running the command again is safe. It fixes
what is missing and repeats nothing.

Want to read the script before you run it? Copy the project first and run the same script
from inside it:

```bash
git clone https://github.com/localnexus/hearth
cd hearth
./install.sh
```

## The two things only you can do

The install stops at two points, prints the command you need, and exits. Run that command,
then start the install again.

**The Xcode command-line tools.** These are Apple's own developer tools. macOS asks you to
agree in a dialog box, and no script can click it for you.

```bash
xcode-select --install
```

**Homebrew.** This is the program that installs the rest of the tools. Its own installer
asks for your Mac password, so you have to run it yourself. The install prints the exact
line to paste.

## The download

Partway through, the install asks whether to fetch the speech models now. These are the
files that let Hearth hear you and speak. They are about 4.6 GB, they download once, and
they are kept in a cache folder called `.cache/huggingface` in your home folder. Say yes.

You also need a language model, which Hearth does not ship. You download one and run it in a
small model server on your own machine. The install guide names the one this project uses.

## First-run setup

When the tools are in place, run the setup:

```bash
.venv/bin/python -m hearth.init
```

It creates your access key and turns on Hearth's web pages. Memory stays off unless you ask
for it later. It prints the key once and then offers to start Hearth right there.

## Start it from a terminal window

Always start Hearth from a terminal window, like this:

```bash
.venv/bin/python -m hearth.serve
```

The reason is a macOS rule. macOS gives microphone permission to the app that owns the
terminal, not to Python. Pick one terminal app, allow the microphone when macOS asks, and
keep starting Hearth from that same app. If you skip this, Hearth hears silence and never
says so.

Next: [your first talk](first-talk.md).

**Go deeper:** [Installing Hearth](../installing.md) and
[Installing by hand](../installing-by-hand.md).
