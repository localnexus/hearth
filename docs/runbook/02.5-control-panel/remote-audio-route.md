# 2.5e The remote audio route — talking from a paired device

> Part of [2.5 Control panel & live status](../02.5-control-panel.md).

A conversation's audio on the desk, or on one paired device across the overlay network.

## What it is

A conversation has a **route**, chosen when it starts:

| route | what plays and listens | when |
|---|---|---|
| `desk` (default) | the pinned local microphone and speaker — unchanged | you are at the machine |
| `remote:<device-id>` | one paired device, through its browser | you are anywhere else |

The route is **fixed at the start**. Nothing can move a conversation from one to the
other while it runs; changing route means starting a new conversation. Everything
else — memory, the shelf, keep-or-delete, Stop, compaction — is identical on both
and unaware of which is in force.

```
  the desk route                         the remote route

  microphone ─┐                          phone browser ─┐
              ├─ Hearth's conversation ──┤              │  wss://<host>:65021
  speaker ────┘                          └─ page on :65001
```

## The ports, and the two lines that publish them

| port | what | bound to |
|---|---|---|
| `65001` | Hearth's own door, which serves the pages | its configured address |
| `65021` | the audio socket for the remote route | `127.0.0.1` only |

The audio socket **never binds to anything but loopback**. What puts it on the
overlay network is one published line, run once by hand:

```
tailscale serve --bg --https=65001 http://127.0.0.1:65001
tailscale serve --bg --https=65021 http://127.0.0.1:65021
```

Both lines are needed: the first serves the page, the second serves the audio.
`--https` matters for more than encryption — see *the secure address* below.

Override the socket's port with `HEARTH_AUDIO_WS_PORT`, and the addresses allowed
to open it with `HEARTH_AUDIO_ORIGINS` (comma-separated, replacing the list whole).
By default the allowed addresses are looked up from this machine's own names, so a
renamed machine needs no edit.

## The talk page

`GET /admin/voice` on `:65001`, on the device itself. Like the launch page it is
served without the access key and carries nothing: it uses the key that device
already holds from pairing, and spends it once, inside the first message on the
audio socket.

1. Start a conversation on the launch page, choosing **a paired device** and
   naming it.
2. Open `/admin/voice` on that device and press **Start talking**. The first press
   is what lets the browser open the microphone, which is why it is a button and
   not automatic.
3. The status line walks `idle → connecting → connected (path, buffer) → …`. The
   page holds the screen awake until you press Stop.

If the device has never been named, the page asks for a short name once and keeps
it. It must match the name the conversation was started for.

## The hello — how the socket knows who it is talking to

The desk route pins a device by identity and never re-opens on another one. The
remote route's equivalent is the **first message**:

```json
{"hello": {"device": "<device-id>", "token": "<access key>"}}
```

- The key is compared whole, in constant time, against `config/serve-token`.
- The name is compared against the one this conversation was started for.
- Anything wrong — a stale key, another device, audio sent before the hello —
  closes the socket with code **4401** and the single word `refused`. It never
  says which half was wrong, and the conversation simply keeps waiting.
- A second device, while one is connected, is refused too. The one already
  talking keeps the socket.
- A conversation asked to start on a device when there is no `config/serve-token`
  **does not start at all**. An audio socket nobody can check is not a degraded
  conversation, it is a different thing.

Success is answered with the buffer depth below, and only then does audio move.

## The depth, and why it is not one number

Measured 2026-09-15 with a phone on the far end:

| how the device reached us | round trip | deepest a frame ran late |
|---|---|---|
| same wifi, straight across the room | ~14 ms | 15 – 35 ms |
| phone hotspot, straight over the internet | ~88 ms | 26 – 107 ms |
| phone hotspot, **through a relay** | ~93 ms | **141 – 188 ms** |

Relaying costs almost nothing in the middle of the distribution and a great deal
at the far end of it. So the depth the device is told to hold is decided **per
connection**, from how it actually arrived:

| path | depth | override |
|---|---|---|
| `direct` | 60 ms | `HEARTH_AUDIO_BUFFER_DIRECT_MS` |
| `relayed` | 200 ms | `HEARTH_AUDIO_BUFFER_RELAYED_MS` |
| `unknown` | 200 ms — not knowing buys the deeper one | (as relayed) |

The page's own status line says which is in force, and says plainly when it is
going through a relay, because that is a little more delay and pretending
otherwise helps nobody.

**The far end drops rather than grows.** This is the one requirement that is not
a preference. Measured on this machine: when whatever is consuming the audio is
slower than the 20 ms frame cadence, delay accumulates *without bound* rather
than shedding — 25 ms of planted delay put a one-way trip at 740 ms and climbing.
So the device's playback buffer throws away its oldest audio past its depth and
shows how much, and Hearth does the same on its own way out.

## Reading it back

The launch page's Stop card states the route as a fact fixed at the start:
`audio: the desk`, or `audio: Pixel — connected (direct, 60 ms buffer)`.
`GET /admin/state` carries the same under `bot.route`:

```json
{"kind": "remote", "device": "Pixel", "state": "waiting|connected|lost",
 "path": "direct|relayed|unknown", "buffer_ms": 60, "shed_ms": 0}
```

Names and numbers only — no addresses, no key, and nothing anyone said.

## The lines in the log

| line | means |
|---|---|
| `[audio] remote client connected (path=direct, buffer=60ms)` | the hello was accepted, audio is moving |
| `[audio] remote client refused` | a first message that was not this conversation's device with the key |
| `[audio] remote output stalled (N ms shed)` | the far end fell behind; N is the total thrown away so far. At most one line every five seconds |
| `[audio] remote route needs config/serve-token — not starting` | there was nothing to check the hello against |

## The secure address, and the fallback if you cannot have one

**A browser will not offer the microphone on a plain-http address at all.** Not a
refusal you can accept — the microphone interface is simply not there, and the
page looks broken. This is why both published lines above are `--https`.

If the https address is genuinely unavailable, the fallback is per-device and
per-address: allow insecure origins for *exactly* that address in the browser's
flags (`chrome://flags` → *Insecure origins treated as secure*). It must be set
again after the browser's data is cleared, and the talk page says so in its own
words when it finds no microphone interface.

## What this phase does not do yet

- **No waiting room after a drop.** If the device goes away the conversation stays
  up and keeps waiting, indefinitely — there is no countdown and no self-stop yet.
  The page reconnects on its own, backing off up to three minutes.
- **No list of paired devices.** The launch page asks for the name; it remembers
  the last one in that browser.
- **One device at a time**, by design.
