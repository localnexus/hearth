<p align="center">
  <img src="docs/brand/hearth-mark-400.png" width="200" alt="Hearth — a flame in a fireplace, inside an amber ring">
</p>
<h1 align="center">Hearth</h1>
<p align="center"><strong>A fully-local, private, persistent voice companion.</strong></p>

You talk; it listens, thinks, and talks back — the whole conversation running on your own machine, with no account, no cloud
call, and nothing leaving the box once the models are cached.

Hearth is a [Local Nexus](https://github.com/localnexus) project — *local infrastructure,
sovereign inference.*

Hearth is a voice-conversation pipeline built on [Pipecat](https://github.com/pipecat-ai/pipecat):

```
mic → VAD → STT → LLM → TTS → speaker
```

Your microphone audio is gated by voice-activity detection, transcribed to text, sent to a
local language model, and the reply is spoken back through a local text-to-speech voice —
in a continuous, low-latency loop. Every stage runs locally against endpoints you own.

> **Status: early public release.** The core loop is real and used daily, but this is a
> young public project. Expect rough edges, thin docs in places, and setup that assumes some
> comfort on the command line. Issues and questions are welcome.

## What makes it different

- **Local by construction.** The pipeline targets endpoints on your own machine. There is no
  telemetry, no phone-home, no hosted API in the default path.
- **Bring your own everything.** Hearth ships the *pipeline* and the *cloning capability*, not
  the heavy or rights-encumbered pieces. You supply the LLM weights, the inference server, and
  any voices you want beyond the default. This keeps the project small, permissively licensed,
  and puts the choices that carry legal or ethical weight (which model, whose voice) in your
  hands.
- **Persistent.** A companion you configure once and keep — its persona, its voice, and its
  settings live in plain files in this repo, not in someone else's account.

## Requirements — two tiers

Hearth is designed to run on hardware **you** control. Two tiers are supported:

- **Gold tier — sovereign local (recommended).** Everything runs on your own Apple Silicon
  Mac: the LLM server, the speech models, the pipeline. Nothing is rented, nothing is remote.
  This is the tier the project is tuned for today. The Mac-only speech chain installs via the
  `hearth[mac]` extra.
- **Silver tier — rented raw GPU with root.** A GPU box you rent but administer as root (you
  install the stack, you hold the keys). Less sovereign than gold, but still *your* stack on a
  machine you fully control. The NVIDIA/CUDA path (`hearth[cuda]`) is a placeholder today —
  its contents are still being decided.

Provider and cloud chat APIs are **not** a supported tier. Hearth is a local companion; a
managed API in the loop would defeat the point.

## Quickstart (shape)

> These are the shape of the steps, not a turnkey script — see the guides under `docs/` for
> the detail, and adjust for your machine.

1. **Install Hearth with the extra for your hardware.**

   ```bash
   # Apple Silicon (gold tier)
   pip install "hearth[mac]"
   ```

2. **Bring an LLM.** Download GGUF weights for a chat model you like and serve them with
   [`llama-server`](https://github.com/ggml-org/llama.cpp) (from llama.cpp), which exposes an
   OpenAI-compatible endpoint. Hearth targets that endpoint — **no LLM server is bundled.**
   (LM Studio works too as an alternative workbench, but `llama-server` is the recommended,
   regression-stable default.)

3. **Point Hearth at your server.** Copy the example config and set your model id and the
   character/voice you want live:

   ```bash
   cp config/active.toml.example config/active.toml
   # edit config/active.toml — character, model, voice
   ```

4. **Add a voice.** A rights-clean default voice ships with `characters/example/`. To use your
   own, drop one clean 10–15 s reference clip into a voice bundle and point a `voice.toml` at
   it — see [Bring your own voice](docs/bring-your-own-voice.md).

5. **Talk.** Start the pipeline and speak.

## Bring-your-own philosophy

Three things Hearth deliberately does **not** bundle:

- **Weights.** You choose and download the LLM. Hearth is model-agnostic — it speaks to any
  OpenAI-compatible endpoint.
- **The server.** No inference server ships. `llama-server` is the recommended default; you
  install and run it.
- **Voices.** Beyond the public-domain default, voices are yours to supply. Only ever clone a
  voice you have the rights to use, and never distribute a cloned voice of a real person
  without their consent. See [Bring your own voice](docs/bring-your-own-voice.md).

## Guides

- [Authoring a character](docs/authoring-a-character.md) — write a persona, lay out a
  character directory.
- [Bring your own voice](docs/bring-your-own-voice.md) — add a reference clip and a voice
  descriptor, with the rights/consent expectations.
- [The config layers](docs/the-config-layers.md) — which file you edit, which files edit
  themselves, and which one you never print.

## License

MIT — see [LICENSE](LICENSE).
