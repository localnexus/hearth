# The map of doors — every port on one page

*Every network door Hearth opens or talks to, what's behind it, and the one read-only command that tells
you it's alive. This is the orientation map — when you're lost about "which thing is on which port," start
here.*

**Authoritative sources:** the control panel → `docs/runbook/02.5-control-panel.md`; the Hearth on/off switch →
`config/serve.toml.example`; the launch page's `/admin` surface →
[The one-button switch](the-one-button-switch.md). This page only *maps* them.

---

## The two questions each door answers

For any port, you really want to know two things: **can something reach it** (is it loopback-private, or
open to a network?) and **what stops an intruder** (a loopback bind, an access key, nothing at all). The
table carries both, plus who owns the process and how to check it's up — all **read-only**.

| Door | What's behind it | Owner process | Bind | What switches it |
|---|---|---|---|---|
| **:65000** | The **control panel** — drive turns without speaking, live status, and (with the launch page) the COMPANION switcher | the voice companion — it lives *inside* that process and dies with it | **loopback** `127.0.0.1` by default | Nothing — the loopback bind *is* the switch. `WEB_HOST=0.0.0.0` opts it onto the LAN (owner opt-in); `WEB_PORT` moves it |
| **:8080** | **Your model server** — `llama-server`'s default port. Not Hearth's: you run it, Hearth is its client | yours (`llama-server`, or LM Studio on `:1234`) | yours to choose | yours to choose. `LM_BASE_URL` / `LM_API_TOKEN` tell Hearth where and how |
| **:65001** | **Hearth** itself (optional) — one OpenAI-compatible `/v1` door (chat, voice-out, opt-in STT-in), plus the `/admin` launch page when that switch is on | `python -m hearth.serve` (or the companion's in-process attach) | **loopback** `127.0.0.1` by default | **Access key, always on** — there is no unauthenticated mode. Only `/health` answers without it |
| **:8555** | A **speech server** (`mlx_audio.server`) — what *Hearth* proxies to for voice notes and transcription. The desk loop needs none of this: its TTS and STT run in-process | yours to run | **loopback** `127.0.0.1` | The loopback bind. Personal voices never leave the machine |

Memory sidecars, if you enable the richer backend, are **children of Hearth's own process** on loopback
with ports picked at spawn — nothing for you to open or check.

> **Everything above is loopback by default.** Hearth binds no network interface on its own, and nothing
> here is ever `0.0.0.0` unless you explicitly ask for it.

---

## What's behind the `/admin` door

When the launch-page switch is on, `:65001` serves a small set of **pages**, not just an API. They're behind
the same access key as everything else on that port, and between them they cover most of what used to be a
file edit and a restart. This table says *what each one is for*;
[The pages behind the door](the-pages-behind-the-door.md) walks what's actually on them:

| Page | What you do there |
|---|---|
| `/admin/launch` | Start and stop the companion, see whether they're running, and switch who's live — the standing surface you can leave open |
| `/admin/first-run` | The first session on a new install — is the model server answering, which model it serves, Start, did it hear you. The launch page offers it until those are true |
| `/admin/roster` | Bring in a new companion, add a voice to an existing one, edit a persona, or branch their memory onto a new track |
| `/admin/settings/ui` | **Every config file, as a form** — the selection pointer, model facts, voice descriptors, the listening calibration, the switches. Generated from the same schema that validates them, so a bad value is refused before it's written |
| `/admin/memory/ui` | Review what a companion remembers and prune it — read what a record says, forget one, clear a companion |
| `/admin/pair/ui` | Hand a device a pairing code, so a phone can reach the door without you typing a token into it |

> **The panel links across to these.** The `:65000` page's *Manage the roster*, *Settings* and *review &
> prune* links are these same pages — they work when you're viewing the panel through Hearth, because
> then both are behind the one door.

The settings page is the general answer to "where do I change this without opening a file" — and
[The pages behind the door](the-pages-behind-the-door.md) is the chapter for all six. The rest of
this manual names the files anyway, because they're **yours** and you should know what you own — but
naming a file is not the same as being told to open it.

---

## Doors your deployment may add

Hearth ships the doors above. A particular installation often puts more around them — a chat client, a
phone-facing voice page, a media server for away mode, a launchd or systemd unit that keeps a service
alive. Those are **not part of the shipped install**, so their ports, labels, and reboot behavior are
whatever *you* set up. Two chapters here describe such a deployment, and both say so at the top:

- [The phone lane — away mode](the-phone-lane-away-mode.md) — a phone-facing voice client, a media server,
  and a TURN relay, all on a private overlay network.
- [The OpenClaw voice lane](the-openclaw-voice-lane.md) — the `:8555` speech server serving a second
  consumer.

> **The rule that carries across all of them:** a door that isn't managed by a service manager is
> **session-launched** — a reboot leaves it down until something starts it again. Don't assume a lane is up
> after the machine restarts; **check**.

---

## The read-only health checks

None of these change anything — they only look.

**Is the listener up?** — `lsof` for the port:
```bash
lsof -nP -iTCP:65000 -sTCP:LISTEN     # swap in any port above
```
A line back = something is listening; the `NAME` column shows the bind (`127.0.0.1:…` loopback vs a
network address).

**Is Hearth alive at all?** — the one unauthenticated route, which leaks no identity:
```bash
curl -s http://127.0.0.1:65001/health          # {"ok": true}
```

**Is Hearth answering, and as whom?** — the **inline-token idiom** (it authenticates without ever
printing the token):
```bash
curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
```
A healthy Hearth returns a one-model list whose `id` is the **active character**. (No header → `401
{"error": "unauthorized"}` — that's the access-key check doing its job, not a fault.)

**Is the whole desk lane ready?** — the preflight, which touches nothing:
```bash
./start.sh --check
```
It prints the engine tree and the data folder, resolves the model id the companion will request, checks your model
server advertises it, and confirms a valid default mic **and** speaker.

> **Never** print `config/serve-token`, `cat` it on its own, or dump env to "check the token." The
> `$(cat …)` *inside* the curl argument is the whole trick — it feeds the token to the header and nowhere
> else.

---

## The shape of it, in one breath

- **The desk loop is self-contained.** Mic → VAD → STT → your model server → TTS → speaker, with one
  loopback panel to watch it. That's the whole appliance.
- **Hearth is the optional door out**, and it is authed from birth — one access key, one port, no
  unauthenticated mode to forget about.
- **Anything a phone touches should funnel through that one authed door**, over a private network you
  control. That's the design: one door to secure.
