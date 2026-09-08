<p align="center">
  <img src="docs/brand/hearth-mark-400.png" width="200" alt="Hearth — a flame in a fireplace, inside an amber ring">
</p>
<h1 align="center">Hearth</h1>
<p align="center"><strong>A voice companion that lives on your own computer.</strong></p>

You talk. It listens, thinks, and talks back in a voice you chose. The whole conversation
happens on your own Mac: no account, no subscription, and nothing leaves the machine.

Hearth is a [Local Nexus](https://github.com/localnexus) project — *local infrastructure,
sovereign inference.*

## What a talk is like

You press Start on a web page, say hello, and a couple of seconds after you stop talking a voice answers. You
keep talking the way you would on a phone call; there is no button to press between turns and
no typing. When you stop, the conversation is saved as a file in a folder on your computer, and
you can open it again later or throw it away.

## What you get

- **It talks back in a voice you chose.** A clean, rights-free voice comes with it. You can
  also give it a short recording of a voice you have the right to use, and it will speak in
  that voice.
- **It can remember you between talks.** Turn memory on and it keeps notes about you across
  conversations, in plain files you can read and delete.
- **It all stays on your computer.** No cloud service is in the loop. Once the models are
  downloaded, it works with the internet off.

> **Status: early public release.** The core loop is real and used daily, but this is a
> young project. Expect rough edges, thin docs in places, and setup that assumes some comfort
> with a terminal window. Issues and questions are welcome.

## Is this for me?

Three questions. If the answer to all three is yes, keep reading.

1. **Do you have a Mac with an Apple chip (M1 or later) and at least 64 GB of memory?** Hearth
   runs a large language model locally, and that takes memory. Smaller setups can work with
   lighter models; see the next section.
2. **Are you willing to install a few programs and run some commands?** The install is one
   command, and the guide walks you through the rest, but there is no app-store button yet.
3. **Do you want it to stay on your machine?** That is the whole point of Hearth. If you would
   rather use a hosted chat service, this is not the project for you.

## Can my computer run it?

| Your Mac's memory | What to expect |
|---|---|
| 16 to 24 GB | Not enough for the default setup. The computer runs out of room and the voice stutters. |
| 32 to 48 GB | Works with a smaller model. Expect it to feel tight. |
| 64 to 96 GB | The practical minimum for the default model. |
| 128 GB or more | Comfortable. Room for the full conversation length and a second model. |

Hearth needs about **60 GB of free disk** (most of it is the model you download) and a working
microphone and speaker. It was built and measured on Apple chips; Intel Macs cannot run it. A
rented Linux machine with an NVIDIA card is a planned second path, not a finished one.

Details, measured numbers, and what makes a lighter setup work:
[Hardware requirements](docs/HARDWARE-REQUIREMENTS.md).

## Get it running

1. **Install.** One command downloads Hearth, sets up its environment, and fetches the speech
   models (about 5 GB, it asks first). It stops and tells you if it needs something only you can
   do, like typing your password. → [Quick guide: install](docs/quick/install.md)
2. **Bring a model.** Hearth does not ship the language model. You download one and run it with
   a small local model server. The guide names the one we use. → [Quick guide: install](docs/quick/install.md#the-download)
3. **Talk.** A first-run setup creates your access key and turns the web pages on, then offers
   to start Hearth. Open the address it shows in your browser, press Start, and speak first;
   there is no greeting. → [Quick guide: your first talk](docs/quick/first-talk.md)

<details>
<summary>The commands, for the reader who wants them now</summary>

```bash
# 1. install (or: git clone https://github.com/localnexus/hearth && cd hearth && ./install.sh)
curl -fsSL https://raw.githubusercontent.com/localnexus/hearth/main/install.sh | bash

# 3. first-run setup, then start
.venv/bin/python -m hearth.init
.venv/bin/python -m hearth.serve      # then open http://127.0.0.1:65001/admin/launch
```

Hearth is not on PyPI (the `hearth` name there belongs to an unrelated project). Start Hearth
from a terminal window, because macOS grants the microphone to the terminal app, not to
Python. Every step as commands you run yourself: [Installing by hand](docs/installing-by-hand.md).
</details>

Short pages for each step, plus [uninstalling](docs/quick/uninstall.md) and
[when it goes wrong](docs/quick/when-it-goes-wrong.md): [the quick guides](docs/quick/README.md).

## Make it yours

- **A voice.** Record 10 to 15 seconds of clean speech, drop it in a folder, and point Hearth
  at it. Only clone a voice you have the right to use, and never share a cloned voice of a real
  person without their consent. → [Quick guide: make a voice](docs/quick/make-a-voice.md)
- **A character.** Write who the companion is in a plain text file: name, manner, what it cares
  about. → [Quick guide: make a character](docs/quick/make-a-character.md)
- **Memory.** Off until you turn it on. → [Memory](docs/memory.md)

## Where your words live

Everything Hearth knows about you is in one folder on your computer: your settings, your
characters, your voices, your saved conversations, and its memory notes if you turned memory
on. They are ordinary files. You can read them, copy them, or delete the folder, and then they
are gone. Nothing is sent anywhere.

## Bring-your-own philosophy

Three things Hearth deliberately does **not** bundle:

- **The model.** You choose and download it. Hearth talks to any local server that speaks the
  common chat API.
- **The model server.** None ships. `llama-server` from llama.cpp is the recommended default;
  you install and run it. LM Studio works as an alternative.
- **Voices.** Beyond the default, voices are yours to supply, under the consent rule above.

This keeps the project small and permissively licensed, and puts the choices that carry legal
or ethical weight, which model and whose voice, in your hands.

## Go deeper

- [Installing Hearth](docs/installing.md) — every prerequisite, the speech-model fetch, the
  voice-engine smoke test, first launch, updating.
- [Installing by hand](docs/installing-by-hand.md) — each step as a command you run yourself.
- [Hardware requirements](docs/HARDWARE-REQUIREMENTS.md) — memory floor, disk, measured
  latency, and what lowers the floor.
- [Authoring a character](docs/authoring-a-character.md) — write a persona, lay out a
  character directory.
- [Bring your own voice](docs/bring-your-own-voice.md) — add a reference clip and a voice
  descriptor, with the rights and consent expectations.
- [The config layers](docs/the-config-layers.md) — which file you edit, which files edit
  themselves, and which one you never print.
- [Memory](docs/memory.md) — continuity across conversations: the memory seam, the
  zero-dependency floor, adopted backends, and what enabling it means for what is kept on disk.
- [Component licensing](docs/COMPONENT-LICENSING.md) — every integrated component and its
  license, and what that means for running or redistributing Hearth.
- [Glossary](docs/glossary/README.md) — plain-language decoder for this project's acronyms
  and shorthand.

## License

MIT — see [LICENSE](LICENSE).
