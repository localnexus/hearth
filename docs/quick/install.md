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

Each of those lines starts with one mark:

| mark | meaning |
|---|---|
| `+` | done now |
| `·` | already there — a re-run repairs, it repeats nothing |
| `!` | a note (for example: no model server answering yet) |
| `-` | skipped (a flag, or you said no) |
| `x` | stopped — the line says why and what to run |

### What a good run looks like

After the Hearth drawing, a first run on a Mac that has none of this yet reads like this. The
numbers are one machine's, and `<you>` stands in for your own home folder; yours will differ.

<!-- screenshot: install.sh, a full first run -->

```text
Hearth install — a voice on your own machine. Every step is reported; nothing is hidden.
preflight
  · macOS 26.0 on arm64
  · 412 GB free
system tools
  · Xcode command-line tools
  · Homebrew
  + PortAudio (the audio library pyaudio is built against)
  + uv (builds the Python environment; fetches Python 3.12 itself)
  + llama.cpp (llama-server, the model server)
Hearth
  + cloned to /Users/<you>/hearth
Python environment
  + .venv with Python 3.12
    installing ~90 packages — a few minutes the first time
  + packages installed, speech pins hold
speech models
Fetch the speech models now? (~4.6 GB, once; they stay in ~/.cache/huggingface) [Y/n]
    mlx-community/chatterbox-turbo-fp16
    mlx-community/S3TokenizerV2
    mlx-community/whisper-large-v3-turbo
  + speech models in ~/.cache/huggingface
model server
  ! nothing answers at http://127.0.0.1:8080/v1 yet. In another terminal window, serve a model:
      llama-server -hf unsloth/Qwen3.6-35B-A3B-MTP-GGUF:Q8_0 -c 0 --port 8080 -a my-model
    (that is this project's recommendation, ~38 GB; smaller ones and which fits your Mac: docs/HARDWARE-REQUIREMENTS.md)
    (or -m /path/to/model.gguf for one you have; how to choose: docs/installing.md)
microphone
Check the microphone now? (2 seconds; macOS asks you to allow it) [Y/n]
  say something — listening for two seconds
  + heard you — loudest moment 37% of full scale
first run
```

Two of those deserve a word. The `!` at **model server** is normal on a first run: the model
server is your own program in another window, and nothing is wrong until you have started it.
After **first run**, the setup below takes over in the same window.

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

The install asks one last question for exactly this reason — "Check the microphone now?" Say
yes, say anything, and it tells you in one line whether it heard you. You can run that check
again whenever you like with `.venv/bin/python -m hearth.init.mic_check`.

Next: [your first talk](first-talk.md).

**Go deeper:** [Installing Hearth](../installing.md) and
[Installing by hand](../installing-by-hand.md).
