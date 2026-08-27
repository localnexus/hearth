# The map of doors — every port on one page

*Every network door Hearth opens, what's behind it, and the one read-only command that tells you it's
alive. This is the orientation map — when you're lost about "which thing is on which port," start here.*

**Authoritative sources:** control panel → `docs/runbook/02.5-control-panel.md`; facade + Open WebUI as
services → `config/launchd/*.plist`; the OpenClaw TTS lane → [The OpenClaw voice lane](the-openclaw-voice-lane.md).
This page only *maps* them.

---

## The two questions each door answers

For any port, you really want to know two things: **can something reach it** (is it loopback-private or
open on the tailnet?) and **what stops an intruder** (loopback bind, a bearer token, tailnet identity, or
nothing). The table carries both, plus who owns the process and how to check it's up — all **read-only**.

| Door | What's behind it | Owner process | launchd label | Bind | What gates it |
|---|---|---|---|---|---|
| **:65000** | Desk **control panel** (drive turns without speaking) | `bot.py` (session-launched) | *(none)* | **loopback** `127.0.0.1` by default | Nothing — the loopback bind *is* the gate. `WEB_HOST=0.0.0.0` opts it onto the LAN (owner opt-in) |
| **:65001** | The **serve facade** — one OpenAI-compatible `/v1` door (chat, voice-out, STT-in) | `python -m hearth.serve` | **`com.hearth.facade`** | **loopback** `127.0.0.1` | **Bearer token, always on** (no unauthenticated mode) |
| **:65002** | **Open WebUI** — the phone chat client (installed as a PWA) | `clients/open-webui/run.sh` | **`com.hearth.openwebui`** | **tailnet** (the Mac's `100.x` IP) | Open WebUI login + tailnet identity; proxies to :65001 over loopback |
| **:3001** | The **web voice client** (Next.js) — away-mode's talk page | `next dev` (session-launched) | *(none)* | **tailnet** `100.x` | Rides the tailnet + the facade; the client itself has no login |
| **:8080** | **The away-mode media server** — carries the audio (WebRTC / WHIP) | `media-server` (session-launched) | *(none)* | **tailnet** `100.x` | Tailnet ACL (gate-only; the fork's JWT is off today) |
| **:3478** | **TURN / STUN** — NAT traversal for the phone (Pion, embedded in the away-mode media server) | `media-server` (same process) | *(none)* | **tailnet** `100.x` (bound tailnet-IP-only) | Creds handed out at connect via `/turn-credentials`; tailnet is the boundary |
| **:8555** | **mlx-audio shim** — local voice-clone TTS (+ a Whisper STT leg). The OpenClaw voice lane's engine, *also* what the facade proxies for its voice notes | `mlx_audio.server` | **`ai.openclaw.voice-tts`** | **loopback** `127.0.0.1` | Loopback bind; a dummy API key the server ignores. Personal voices never leave the machine |

> **The tailnet IP.** Where a door binds "the Mac's `100.x` IP," that's its Tailscale address — private
> to your tailnet, not the open internet. The exact literal lives in `clients/open-webui/README.md`;
> this page keeps it generic on purpose.

---

## Which doors survive a reboot (and which don't)

Three of these run under **launchd** — they come back on their own after a reboot or a crash. The rest are
**session-launched**: something (you, or `start.sh`) started them by hand this session, and a reboot leaves
them down until they're started again.

```
launchd-managed (reboot-durable)          session-launched (down after reboot)
────────────────────────────────          ────────────────────────────────────
:65001  com.hearth.facade                 :65000  desk control panel (bot.py)
:65002  com.hearth.openwebui              :3001   web voice client (next dev)
:8555   ai.openclaw.voice-tts             :8080 / :3478  away-mode media server + TURN
```

> **This split is a known open gap.** The away-voice trio (:3001 / :8080 / :3478) is *not*
> reboot-durable yet. A reboot silently takes the phone-voice lane offline until it's relaunched. Don't assume it's
> up after the Mac restarts — **check**.

---

## The read-only health checks

None of these change anything — they only look. Two families cover every door:

**Is the listener up?** — `lsof` for the port:
```bash
lsof -nP -iTCP:65001 -sTCP:LISTEN     # swap in any port above
```
A line back = something is listening; the `NAME` column shows the bind (`127.0.0.1:…` loopback vs
`100.x:…` tailnet).

**Is the launchd service loaded?** — `launchctl list` (for the three managed labels):
```bash
launchctl list | grep -E 'com\.hearth|ai\.openclaw'
```
A numeric PID in the first column = running; a `-` = loaded but not currently up.

**Is the facade actually answering (and as whom)?** — the one door worth probing deeper, using the
**inline-token idiom** (it authenticates without ever printing the token):
```bash
curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
```
A healthy facade returns a one-model list whose `id` is the **active character**. (No header → `401
{"error": "unauthorized"}` — that's the bearer gate doing its job, not a fault.)

> **Never** print `config/serve-token`, `cat` it on its own, or dump env to "check the token." The
> `$(cat …)` *inside* the curl argument is the whole trick — it feeds the token to the header and nowhere
> else.

---

## The shape of it, in one breath

- **Loopback doors** (:65000, :65001, :8555) are private to the Mac. The facade adds a bearer token on top.
- **Tailnet doors** (:65002, :3001, :8080, :3478) are reachable from your phone anywhere on the tailnet —
  and *only* from your tailnet. None is ever bound to `0.0.0.0` by design.
- **Everything the phone touches funnels through the facade** (:65001) as the single authed door — the chat
  client, the voice client's LLM leg, all of it. That's the point of the design: one door to secure.
