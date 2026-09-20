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

## Paired devices

Pairing **enrols** a device: one row in `HEARTH_DATA/config/devices.toml`, which
is what the launch page's **Audio** control lists, what `remote:<id>` names, and
what **forget** takes away. It has a page of its own:
**[Paired devices](paired-devices.md)** — the file and its fields, the claim's
body and answer, forget and its archive, the refusals, and the one re-pair a
device paired before the list needs.

## The talk page

`GET /admin/voice` on `:65001`, on the device itself. Like the launch page it is
served without the access key and carries nothing: it uses the key that device
already holds from pairing, and spends it once, inside the first message on the
audio socket.

1. Start a conversation on the launch page, choosing the device from the
   **Audio** list.
2. Open `/admin/voice` on that device and press **Start talking**. The first press
   is what lets the browser open the microphone, which is why it is a button and
   not automatic.
3. The status line walks `idle → connecting → connected (path, buffer) → …`. The
   page holds the screen awake until you press Stop.

The page never asks for a name. A browser that has not paired on this address
has no name to send, and says so — *not paired on this device — open the pairing
page first* — with **Start** held down until it has.

## Four things a device can get wrong, and the fix for each

The first three were found on the first sitting from a Linux laptop (Firefox, a
Bluetooth headset, 2026-09-18), the fourth on a Pixel the day after; none of
them is a fault in the route.

- **Connected, but the companion hears nothing.** The browser is capturing
  from a device that is not your microphone. Firefox keeps its own per-site
  choice and does not follow the system default: when it asks for the
  microphone, pick the headset **by name** rather than allowing all devices,
  or click the microphone icon in the address bar afterwards and change it
  there. A Bluetooth headset has a microphone only in its headset profile
  (HSP/HFP); in the high-quality A2DP profile it is output only and the system
  quietly falls back to another input.
- **"Unable to connect" before any page loads.** The tailnet name is not
  resolving on the device. On Linux, `sudo tailscale set --accept-dns=true`
  turns MagicDNS on for that machine; a browser running DNS-over-HTTPS in its
  strict mode also hides tailnet names until it is set back to the default.
  `getent hosts <the tailnet name>` says which it is.
- **No Start button anywhere.** You are on the control panel, not the talk
  page. After Start on the launch page the next stop for a remote route is
  `/admin/voice` *on that device*; the start card's route note links it as
  "the talk page". The control-panel link goes to the proxied `:65000` panel,
  which has no audio controls.
- **The line drops when the phone's screen goes off, or on the home screen.**
  The talk page is a browser tab, and a phone browser is free to discard a tab
  it cannot see. Measured on a Pixel with Chrome: any page of the browser app
  in the foreground keeps the line, a short Wi-Fi blip is survived without the
  server even noticing, but the screen going off or a trip to the home screen
  drops the socket. When you come back the page has been reloaded — it says
  `idle` and Start is live again — and pressing Start rejoins inside the wait
  window. Keep the browser app in front, or lengthen the window (next section).
  A Bluetooth headset is not used just because it is connected: Chrome on
  Android tends to keep the phone's own microphone and media speaker; that is
  the phone's choice, not the page's.

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

Success is answered with the buffer depth below **and the length of the wait** —
`{"ok": true, "path": "direct", "buffer_ms": 120, "grace_s": 180}` — and only then
does audio move. The device needs the second number to count honestly instead of
retrying into the dark.

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
| `local` | 120 ms — this machine's own address, or loopback: a page opened here, or a probe | (as direct) |
| `direct` | 120 ms | `HEARTH_AUDIO_BUFFER_DIRECT_MS` |
| `relayed` | 200 ms | `HEARTH_AUDIO_BUFFER_RELAYED_MS` |
| `unknown` | 200 ms — not knowing buys the deeper one | (as relayed) |

**Why 120 and not 60.** Seen in use on 2026-09-16, with a phone on a hotspot going
straight over the internet: a 60 ms buffer on the device dropped **1189 ms of audio
over about seven turns**, while this end shed nothing at all. The measurement above
had already said a direct hotspot ran to 107 ms; the depth had not listened. 120 ms
is that measurement taken seriously. A device on the same wifi is very likely fine at
60 and is not measured on the ring, which is what the override is for.

The page's own status line says which is in force, and says plainly when it is
going through a relay, because that is a little more delay and pretending
otherwise helps nobody.

**The far end drops rather than grows.** This is the one requirement that is not
a preference. Measured on this machine: when whatever is consuming the audio is
slower than the 20 ms frame cadence, delay accumulates *without bound* rather
than shedding — 25 ms of planted delay put a one-way trip at 740 ms and climbing.
So the device's playback buffer throws away its oldest audio past its depth and
shows how much, and Hearth does the same on its own way out.

## The wait — what happens when the device goes away

A socket that goes is not a conversation that is over. A screen sleeps, a network
flips, a lift has no signal. So the conversation **waits**, and it says how long at
the hello:

| | how long | what ends it |
|---|---|---|
| the desk | indefinitely | nothing; the headset is waited for as long as it takes |
| a paired device | **180 seconds**, `HEARTH_AUDIO_GRACE_S` | the device comes back, or the conversation closes itself |

- **It comes back inside the wait.** The device sends its hello again and rejoins a
  conversation that was never torn down — mid-answer, with the answer's tail still
  coming. The depth is decided again, from whatever network it came back on: a phone
  that left on wifi and returned on cellular is a different path and is told so.
- **It does not.** The conversation **closes itself the way the Stop button closes
  it** — the same signal, from inside. Everything downstream is the ladder that
  already existed: the capture finalised, the panel down, the memory tail, and the
  transcript kept or deleted exactly as you left the keep switch (including a late
  flip from the Stop card). There is no second close path and no file of its own.
- Afterwards the companion is simply down, and the launch page's status line says
  why: *the last conversation closed itself: Pixel did not come back within the
  wait*. Underneath, the process exits with status **3**, which nothing else uses.
- A device that arrives during the few seconds the socket takes to come down is
  closed with **4410** and the word `ended` — not a refusal, because nothing was
  refused: there is nothing left to join. The talk page says so and stops retrying.
- Where to set it: `[serve.supervisor.env]` in `config/serve.toml` (for instance
  `HEARTH_AUDIO_GRACE_S = "900"` for fifteen minutes). Hearth hands that block
  to every conversation it starts and reads it once, when it comes up — so
  restart Hearth, with nothing running, for a new value to take.
- A wrong `HEARTH_AUDIO_GRACE_S` (a word, a zero, a negative) leaves the 180 standing.
  A conversation that closes the moment a phone blinks is the expensive mistake.

**On the device**, the page counts the same wait down: `reconnecting in 8 s… (the
conversation waits 2:14)`. Thirty seconds past the wait it stops trying, releases the
screen and closes the microphone, and says *the conversation has most likely closed —
press Start when you are back*. A phone in a pocket must not retry all night.

## Reading it back

The launch page's Stop card states the route as a fact fixed at the start:
`audio: the desk`, or `audio: Pixel — connected (direct, 120 ms buffer)`. While a
device is away it counts down — `audio: Pixel — waiting for Pixel, 2:40 left; then
this conversation closes` — and the count ticks between polls rather than jumping.
`GET /admin/state` carries the same under `bot.route`:

```json
{"kind": "remote", "device": "Pixel", "state": "waiting|connected|lost|ended",
 "path": "local|direct|relayed|unknown", "buffer_ms": 120, "shed_ms": 0,
 "grace_left": null}
```

| state | means |
|---|---|
| `waiting` | started, no device has joined yet — it waits as long as it takes |
| `connected` | the hello was accepted; audio is moving |
| `lost` | the device went; `grace_left` is the seconds still to wait |
| `ended` | the wait ran out; the conversation is closing itself |

`grace_left` is null unless a remote device is actually away, and null always on the
desk — which is the difference between the two routes, not a gap in one of them.

Names and numbers only — no addresses, no key, and nothing anyone said.

## The lines in the log

| line | means |
|---|---|
| `[audio] remote client connected (path=direct, buffer=120ms)` | the hello was accepted, audio is moving |
| `[audio] remote client rejoined after 34 s (path=direct, buffer=120ms)` | the same device came back inside the wait; the conversation continued |
| `[audio] remote client lost — waiting up to 180 s` | the device went; the countdown started |
| `[audio] remote device did not return within 180 s — closing the sitting` | the wait ran out; the close ladder is running |
| `[audio] remote client refused` | a first message that was not this conversation's device with the key |
| `[audio] remote output stalled (N ms shed)` | the far end fell behind; N is the total thrown away so far. At most one line every five seconds |
| `[audio] remote route needs config/serve-token — not starting` | there was nothing to check the hello against |
| `[supervisor] device paired (pixel-3f4a)` | a claim was correct and enrolled a device. The **id**, never the label — a label is your own words |
| `[supervisor] device forgotten (pixel-3f4a)` | a confirmed forget removed a row (the list was copied beside itself first) |
| `[supervisor] device registry write failed (OSError)` | the list could not be written. Pairing still handed over the key; a start still ran |
| `[devices] devices.toml is malformed (…) — no devices listed` | the file could not be parsed. Nothing refuses to start; pair a device again to rebuild it |

## The secure address, and the fallback if you cannot have one

**A browser will not offer the microphone on a plain-http address at all.** Not a
refusal you can accept — the microphone interface is simply not there, and the
page looks broken. This is why both published lines above are `--https`.

If the https address is genuinely unavailable, the fallback is per-device and
per-address: allow insecure origins for *exactly* that address in the browser's
flags (`chrome://flags` → *Insecure origins treated as secure*). It must be set
again after the browser's data is cleared, and the talk page says so in its own
words when it finds no microphone interface.

## What this does not do yet

- **No timeout on the first wait.** A conversation started from the desk waits for
  the device to arrive for as long as it takes — only a device that has already been
  here is waited for on a clock. Now that the chooser exists, this is the place the
  question belongs: choosing a device that is switched off is the case a clock would
  be for. Still an open question rather than an omission.
- **The list is not consulted on the socket.** The hello compares the id the
  conversation was started for against the id on the wire, and the start door is
  what checks that id against the list. One check each, in the place that can make
  it — a device that is forgotten mid-conversation does not lose its socket.
- **One device at a time**, by design.
