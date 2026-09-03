# 2.5c The launch page (`/admin/launch`)

> Part of [2.5 Control panel & live status](../02.5-control-panel.md).

The standing offline surface: start, resume, stop, and the live bot indicator.

The standing surface for starting and stopping the bot **without recalling any flags** — reachable
whether the bot is up or down, from any device that can reach the facade (it's plain HTTP, no
WebRTC). Open `http://<facade-host>:65001/admin/launch`; it asks for the serve bearer **once**
(kept in that browser's localStorage, sent only as an `Authorization` header) and then offers:

- **Start** (bot down): pick the companion (a different companion auto-fills a valid voice and
  rides `/admin/switch` with `start:true`), pick **— new session —** or a saved session off the
  shelf, pick the memory posture (default = full; a resumed session keeps its own saved mode).
- **Stop** (bot up): one button — the session saves by default; an optional *name this session*
  field is the "name it now" ergonomic. Plus a link into the proxied control panel.
- A live state line (bot / pid / uptime / externals / switch phase, polled every few seconds).

The page never bounces the facade itself — it starts and stops the **bot** only. First supervised
spawn on a fresh macOS install is a desk moment (the mic permission attributes to the daemon).
