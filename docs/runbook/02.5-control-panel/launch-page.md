# 2.5c The launch page (`/admin/launch`)

> Part of [2.5 Control panel & live status](../02.5-control-panel.md).

The standing offline surface: start, resume, stop, and the live bot indicator.

The standing surface for starting and stopping the bot **without recalling any flags** — reachable
whether the bot is up or down, from any device that can reach the facade (it's plain HTTP, no
WebRTC). Open `http://<facade-host>:65001/admin/launch`; it asks for the serve bearer **once**
(kept in that browser's localStorage, sent only as an `Authorization` header) and then offers:

- **Companion** — the [shared switch card](companion-switcher.md), the same box the `:65000`
  panel carries: character · voice · persona · model (● = resident, so the change can go live).
  It reads **Start** while the bot is down and **Switch** while it is up, which is what makes a
  **warm** switch possible without walking to the desk — the daemon applies it at the next words
  when every changed piece has a live path, and warm-restarts otherwise. Both rides are the same
  `POST /admin/switch`; a down bot gets `start:true`.
- **Session + Memory** (bot down only): **— new session —** or a saved session off the shelf, and
  the memory posture (default = full; a resumed session keeps its own saved mode). Both are
  start-only — a memory change cannot ride a live switch, so the daemon refuses that pairing.
- The **control panel** link (bot up): the page mints the browser carrier once per load, so
  the proxied `:65000` panel opens by clicking rather than answering `401`. Everything else
  here sends the bearer as a header and never needs the cookie.
- **Stop** (bot up): one button — the session saves by default; an optional *name this session*
  field is the "name it now" ergonomic. Plus a link into the proxied control panel.
- A live state line (bot / pid / uptime / externals / switch phase, polled every few seconds).
- **Externals** (only when actuators are declared): one row per
  `[serve.supervisor.actuators.<name>]` with its note, its reachability probe, and the last
  run's outcome — plus a **Run** button. The request holds until the command finishes, so a
  slow bring-up simply keeps the button disabled; `409` means it is already running. This is
  what makes a session-launched external (the away-voice server and its web client)
  recoverable from a phone instead of only from the desk.

The page never bounces the facade itself — it starts and stops the **bot** only. First supervised
spawn on a fresh macOS install is a desk moment (the mic permission attributes to the daemon).
