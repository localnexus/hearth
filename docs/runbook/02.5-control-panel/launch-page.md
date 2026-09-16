# 2.5c The launch page (`/admin/launch`)

> Part of [2.5 Control panel & live status](../02.5-control-panel.md).

The standing offline surface: start, resume, stop, and the live companion indicator.

The standing surface for starting and stopping the companion **without recalling any flags** — reachable
whether the companion is up or down, from any device that can reach Hearth (it's plain HTTP, no
WebRTC). Open `http://<facade-host>:65001/admin/launch`; it asks for the serve access key **once**
(kept in that browser's localStorage, sent only as an `Authorization` header) and then offers:

- **Companion** — the [shared switch card](companion-switcher.md), the same box the `:65000`
  panel carries: character · voice · persona · model (● = resident, so the change can go live).
  It reads **Start** while the companion is down and **Switch** while it is up, which is what makes a
  **warm** switch possible without walking to the desk — Hearth applies it at the next words
  when every changed piece has a live path, and warm-restarts otherwise. Both rides are the same
  `POST /admin/switch`; a down companion gets `start:true`.
- **Session + the two switches** (companion down only): **— new session —** or a conversation off the
  shelf, and this sitting's remember-the-past / keep-this-conversation switches (default = remember,
  don't keep; a resumed conversation keeps its own switches). Both are start-only — the switches
  cannot ride a live switch, so Hearth refuses that pairing.
- **Audio** (companion down only): **the desk** (default) or **a paired device**, with a short
  name field for which device. The desk is exactly what it always was — the pinned local
  microphone and speaker. A paired device takes the conversation's audio over the overlay
  network instead, and the device opens [`/admin/voice`](remote-audio-route.md) to join. The
  browser remembers the last name typed. Like the two switches this is **start-only**: the
  route is fixed when a conversation begins, and changing it means starting a new one.
- The **control panel** link (companion up): the page mints the browser carrier once per load, so
  the proxied `:65000` panel opens by clicking rather than answering `401`. Everything else
  here sends the access key as a header and never needs the cookie.
- **Stop** (companion up): the session is unkept unless this sitting was started keeping it; a
  **keep this conversation** box and an optional name field show that choice from the moment the
  companion is up, and are the late chance to change it (the name is a label for the shelf — the
  file keeps its own id), and the button's label says which it will do
  (**Stop**, or **Stop and keep**). Plus a link into the proxied control panel. A line above
  states where this conversation's audio is, as a fact fixed at the start — `audio: the desk`,
  or `audio: Pixel — connected (direct, 120 ms buffer)`, with `waiting for it to connect`
  before the device arrives. It is seeded from the start-time choice the same way the keep
  switch is, so it is right from the moment the companion is up, and filled out from what the
  running conversation reports.
- **When a device goes away, that line tells you which kind of away it is**, because the two
  are not the same thing and one word for both would be true and useless:
  - *the desk* — `the headset is away; waiting, and it will come back on the same device only`.
    There is no countdown: silence is something you can wait out, and the conversation does.
    (`the headset came back` after it does; `no device pinned` when there is none.)
  - *a paired device* — `waiting for Pixel, 2:40 left; then this conversation closes`, counting
    down each second, and `it did not come back; closing` at the end of it. A phone is a
    countdown because a conversation left open all night is not waiting, it is stranded. See
    [the wait](remote-audio-route.md) for what closing means (short version: exactly what your
    Stop button means, keep switch and all).
  - While the device is connected but falling behind: `the phone is falling behind (140 ms
    dropped)` — the audio it could not keep up with, not a fault to hunt.
- A live state line (companion / pid / uptime / externals / switch phase, polled every few seconds).
  **With nothing running it also names why the last conversation ended, when it can**: a
  conversation that closed itself over a device that never came back says so — *the last
  conversation closed itself: Pixel did not come back within the wait*. That ending is the one
  nobody witnessed, and it is the only one the page can name this way.
- **Externals** (only when actuators are declared): one row per
  `[serve.supervisor.actuators.<name>]` with its note, its reachability probe, and the last
  run's outcome — plus a **Run** button. The request holds until the command finishes, so a
  slow bring-up simply keeps the button disabled; `409` means it is already running. This is
  what makes a session-launched external (the away-voice server and its web client)
  recoverable from a phone instead of only from the desk. An actuator declared with
  `guard = "companion"` is **held while a companion is running**: the press comes back as a
  question naming the cost (whatever the command frees, the next turn pays to bring back), and
  only a confirmed press goes through. That is the shape for the "free the model server's
  models" command — a live session owns its model's residency.
- **Sessions** — the selected companion's saved conversations, one row each, with the
  verbs on them: reveal (only where the browser is on this machine — it retires itself the
  first time it is refused), download (an authed fetch, since a link cannot carry the access
  key), archive/unarchive, rename (a title; the id only through `change id…`, and only when
  nothing knows the id), and a red **Destroy** that shows its plan and asks for the word the
  row displays. A **Bring in** file picker at the foot deposits a session file onto the shelf.
  An unkept leftover — a working file never kept onto the shelf — is offered Keep and Delete
  and nothing else; destroy is the one verb behind an exposure check. While the companion is up
  its whole shelf is read-only. Full account: [session continuity](../../config-manual/session-continuity.md).
- **Models** (only when weights are enrolled or a door is declared): which weights the door
  serves, one row per enrolled model, with enroll · render · apply · unenroll and the door's own
  Load / Unload. Every button shows what it would do and waits for a second press.
- **First run** (new installs only): while the selected model config still carries the shipped
  placeholder id, or no companion on this install has a session yet, a card at the top offers the
  first-run page (`/admin/first-run`, [admin surface](admin-surface.md)) — and while the id is the
  placeholder the companion card is parked, since a companion started now could not reach a model.
  `/admin/state` carries the two facts as `first_run`.

The page never bounces Hearth itself — it starts and stops the **companion** only. First supervised
spawn on a fresh macOS install is a desk moment (the mic permission attributes to Hearth).

## One look across both ports

The launch page and its siblings (first run, roster, settings, memory, pairing) share their palette,
mark, and header with the `:65000` control panel. The definition lives in one place —
`src/hearth/ui/brand.css` plus `ui/brand.py` — and is spliced into every page at import, the
same mechanism as the companion switcher and for the same reason: six hand-copied palettes
drift, and these two surfaces had already drifted into opposite visual languages.

Two deliberate asymmetries survive that sharing:

- **Hearth pages stay theme-adaptive.** They follow the OS (`color-scheme: light dark`),
  because they are the phone surface; they opt into light-mode brand overrides with
  `class="brand-adaptive"` on `<body>`. The panel is committed dark and must *not* take
  those overrides, which is why opting in is a class rather than a bare media query.
- **The panel keeps its own signal colours.** Amber means changed-from-default and hot
  orange means knob-extreme; those are panel semantics, not brand, and stay out of the
  shared layer.

The artwork is served from `/ui/brand/` rather than inlined — two cacheable images instead of
12.7 KB of base64 in every page. Edit the palette in `brand.css`; a page that declares a brand
token itself fails `tests/supervisor/test_shared_brand.py`.
