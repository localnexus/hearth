# The phone lane — away mode, from the server side

*How the companion's voice reaches your pocket on a walk: what's exposed (and how narrowly), the one browser quirk that
shapes the whole setup, and the recovery moves when the phone stops connecting. This is the **server-side**
operator view — not the phone-tapping how-to.*

> **Scope — this is a deployment, not the shipped install.** Hearth ships the desk voice loop, its panel,
> and the optional Hearth ([The map of doors](the-map-of-doors.md)). The three pieces below — a media
> server, a TURN relay, a phone-facing voice page — are things *you* add around it, so their ports, their
> supervision, and their reboot behavior are yours. Read this chapter as the shape of a working away lane
> and the traps it taught, not as a description of what's already on your machine.

**Scope:** the operator process only. The privacy posture it rests on is simple — the phone lane
rides your own network to your own machine; nothing leaves the box.

---

## What "away mode" actually is

The mic moves to the **phone**; the Mac still does all the thinking, listening, and speaking. Audio travels
**Mac ↔ phone directly over the tailnet** (WebRTC), no cloud, more private than the app it replaces. Three
session-launched pieces make it work:

- **The away-mode media server** (:8080) — carries the audio and runs barge-in.
- **TURN / STUN** (:3478) — NAT traversal, embedded in the away-mode media server.
- **The web voice client** (:3001) — the page you open on the phone to talk.

All three bind the Mac's **overlay-network IP** (a tailnet `100.x` address), never `0.0.0.0`. Hearth (:65001) and the voice engine
(:8555) stay on loopback behind them. (Full port map: [The map of doors](the-map-of-doors.md).)

> **Tailnet only — never funnel.** Exposure is `tailscale serve` / a tailnet-IP bind, reachable only from
> devices *in your tailnet*. Tailscale **funnel** (public internet) is never used. The tailnet's WireGuard
> encryption + ACL is the boundary.

---

## The one quirk that shapes everything: TURN-over-TCP

The Pixel's hardened browser (**Vanadium**) sends **zero WebRTC UDP** — categorically, by design. A normal
WebRTC setup expects UDP and would just fail to connect. The **workaround of record** is to relay the media
over **TCP** through the local TURN server on :3478:

```
phone → TCP :3478 → TURN allocation → relay → away-mode media server → …
```

This is already wired and field-proven ("latency indistinguishable from desktop to my ear"). You don't
configure it per session — it's how the stack is built. Just **know that :3478 must be up and bound to the
tailnet IP** for the phone to connect at all; if it's down, the phone hangs at "connecting."

---

## The per-origin insecure-origin flag — the fragile, load-bearing bit

The phone talks to plain-HTTP tailnet origins (`:3001` for voice, `:65002` for chat). A browser won't give a
plain-HTTP page microphone access **unless** you've told Vanadium to treat that exact origin as secure. So
each origin is added once to Vanadium's flag:

```
chrome://flags → "Insecure origins treated as secure"
  add the exact origin (the :3001 voice client, and separately the :65002 chat origin)
  → Relaunch
```

**This flag is load-bearing for both mic access and PWA install.** Its recovery behavior is the thing to
remember:

| It survives… | It does **not** survive… |
|---|---|
| A phone **reboot** | A Vanadium **data reset / clear** |

> **Recovery when the mic suddenly stops working:** if a Vanadium data reset wiped the flag, the symptom is
> "can't hear me" / getUserMedia failing. **Re-add the exact origin** to the flag and relaunch — that's
> the whole fix. (Add each origin separately; :3001 and :65002 are distinct.)

---

## The screen-wake-lock stopgap

Android suspends a background tab's WebRTC and mic, and the Pixel's own display timeout can sleep the page
mid-conversation and drop the call. A **Screen Wake Lock** in the voice client holds the screen awake for
the whole live span (including reconnect). Two things follow from that:

- **Vanadium must stay foreground.** Background the tab and the session drops — expected Android behavior,
  not a bug.
- The wake-lock is a **stopgap**, not the real fix. The durable answer is a native shell with a foreground
  service (the surveyed screen-off path) — still a proposal, not built.

---

## The reconnect grace — why a tunnel or a pocket doesn't always kill the call

If the phone flips wifi↔cellular or naps briefly, the away-mode media server holds the conversation open for a **grace
window** (raised to **3 minutes** after a field walk showed a 30 s default was too short for a hotspot nap).
Inside that window the phone redials and rejoins the *same* conversation, pipeline still running. Outside it,
you start fresh. Nothing to operate here — just know a short dropout is expected to self-heal, a long one
won't.

---

## The open gap you must not forget: reboot durability

The launchd note bears repeating here because it bites hardest on this lane:

> **The away-mode media server (:8080/:3478) and the voice client (:3001) are session-launched, NOT launchd-managed.** After
> a Mac reboot they are **down** until relaunched — Hearth, Open WebUI, and the :8555 engine come back on
> their own, but the away-voice trio does not. This is a known reboot-durability hole, not a fault to debug. If the phone can't
> connect after the Mac restarted, **check whether these three were relaunched** before chasing anything
> deeper.

**Net:** away mode is tailnet-only WebRTC with a TCP-relay workaround for Vanadium's dead UDP; the
insecure-origin flag is the fragile keystone (re-add it after any browser data reset); keep the screen
foreground; and after a reboot, remember the voice trio doesn't come back by itself.
