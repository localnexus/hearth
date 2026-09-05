# The one-button switch — the supervisor daemon

*Turning the four-step switching ritual into one press: what the daemon is, how you turn it on, what the
**COMPANION** box does, and how to tell whether your switch landed live or cost a restart.*

**Authoritative sources:** the panel's switcher → `docs/runbook/02.5-control-panel.md`; the gate's keys →
`config/serve.toml.example` (the committed template); who owns which file →
[The config layers](the-config-layers.md). The hand ritual underneath it all is
[Switching who's live](switching-who-is-live.md).

---

## What it is

The **serve facade** can grow a second face: a **supervisor daemon** that owns the voice bot as a child
process. With it on, the facade can start the bot, stop it warmly, restart itself, and — the part you'll
use daily — **switch companion as one action** instead of edit-file-stop-start-confirm.

Three things stay true, and they're the reason it's safe to leave on:

- **One door.** The daemon lives on the facade's existing port and behind the facade's existing bearer.
  No second service, no second secret.
- **It ships OFF.** With the table absent or `enabled = false`, nothing mounts and the facade is exactly
  what it was.
- **Your LLM server is never touched.** "Stop" and "restart" here mean the voice bot only. The model
  server is yours, watched but never owned.

---

## Turning it on

Four things have to be true, and the whole of it is one table in one file. On a new install the
first-run bootstrap (`hearth.init`) has already done the first two — this is what it did, and the
by-hand path for an install that predates it:

- **The facade is enabled** — `config/serve.toml` with `[serve] enabled = true` and a token minted at its
  `token_source` path.
- **The daemon table is uncommented** in that same file (shown below).
- **The facade runs standalone** — `python -m hearth.serve`. The daemon mounts *only* in the standalone
  process; the bot's own in-process attach never mounts it. A bot the daemon starts skips that attach
  altogether (it inherits `HEARTH_SUPERVISED=1`; its parent already serves `/v1`), so its log notes the
  hand-off at INFO. The *bind failed* WARNING is reserved for a real collision: a terminal `start.sh`
  beside a running facade.
- **The panel stays on loopback.** The panel's switcher refuses to register when the panel is LAN-exposed
  (`WEB_HOST` set to anything but loopback) — a relay must never widen an unauthenticated panel into a
  control door. From a LAN-exposed panel, use the facade's authed route directly instead.

The table:

```toml
[serve.supervisor]
enabled = true
```

Validate before you flip it: `python -m hearth.config.check` binds the full schema and names any bad key.

> **First supervised start is a desk moment.** macOS grants microphone access to the app responsible for
> the process. A bot spawned by the daemon attributes differently than one you launched from your
> terminal, so expect **one mic prompt** the first time — be at the machine to say yes.

---

## The COMPANION box

When the daemon is up and the panel is loopback, the panel grows a **COMPANION** section: pick
character / voice / persona / model, tick **keep this session** if you want the current conversation kept,
press **Switch**. Models your LLM server already holds are marked with a ● — those are the ones that can
change without a restart.

What happens behind that press, in order:

1. **Validation.** The selection is checked against the settings registry *and* against what's actually on
   disk. A selection that fails **writes nothing** — you get named errors and the old companion keeps
   answering.
2. **The write.** `config/active.toml` is rewritten atomically, and the previous file is kept beside it as
   **`active.toml.prev`**. Rollback is one rename. (Comments don't survive a daemon write — the four keys
   are the whole contract.)
3. **The lightest apply that works.** See below.

If the box isn't there at all, that's information: the daemon isn't configured, isn't reachable, or the
panel is LAN-exposed.

---

## Live, or a warm restart — how to tell which you got

| Path | When | What you see |
|---|---|---|
| **Live** | The bot is running **and** every changed piece has a live path — persona, voice, and a model your server already holds | Nothing restarts. The old session finalizes exactly as a graceful stop would (memory record written, hold honored), the new companion arrives with their own recall, and the swap lands **at your next words**. The page stays up and says *switched ✓* |
| **Warm restart** | Anything heavier changed, the bot was down, or the live arm was refused | The bot warm-restarts (~10–30 s). The panel goes down with it and reloads itself when the new one is up. Your LLM server is untouched |

Either way the session semantics are identical — **keep this session** is honored on both paths.

> **The facade is deliberately left alone.** A `[serve.identity]` pin keeps its own voice regardless; an
> unpinned facade's LLM-leg params follow at its next restart. The switch response says so rather than
> pretending otherwise.

---

## The same thing without a browser

Every panel click is a thin relay to the facade's authed `/admin` surface, so the terminal can do it too
(the inline-token idiom — the token is fed to the header and never displayed):

```bash
TOK="Authorization: Bearer $(cat config/serve-token)"
curl -s -H "$TOK" http://127.0.0.1:65001/admin/switch                   # current + choices + bot state
curl -s -H "$TOK" -H 'Content-Type: application/json' \
     -d '{"character":"example","voice":"default","hold":true}' \
     http://127.0.0.1:65001/admin/switch
```

The rest of the surface, all behind the same bearer:

| Route | What it does |
|---|---|
| `GET /admin/state` | Bot state, panel reachability, watched externals (your LLM server, the speech server), the last switch |
| `POST /admin/bot/start` | Launch the bot — `{"mode":"new"}` or `{"mode":"resume","name":"<session>"}` |
| `POST /admin/bot/stop` | Graceful stop — `{"hold":true}` keeps the session |
| `POST /admin/daemon/restart` | Restart the facade itself (the one restart it can't do in place) |
| `GET`/`POST /admin/switch` | Read the picker / perform the switch |
| `GET /admin/actuators` | List your declared actuators (state + last run) |
| `POST /admin/actuators/<name>/run` | Run one — the reply waits for the honest result |

Two response details worth knowing when you script it: `"apply"` steers the routing — `"auto"` (default),
`"live"` (live or refuse, never restart), `"restart"` (force the restart path) — and a refused **live** arm
answers 409 while telling you the selection **is already written**, so a repost with `"apply":"auto"` takes
the restart path. A second switch while one is in flight is refused, not queued.

---

## Watching — and nudging — the rest of the stack

The daemon owns only the voice bot. Everything else it *watches*: `GET /admin/state` reports
reachability for your model server and the voice server, plus anything you declare under
`[serve.supervisor.watch.<name>]` (a name and a URL — any HTTP answer counts as up).

For the moments watching isn't enough, declare an **actuator** — your own fixed command under
`[serve.supervisor.actuators.<name>]` (see `config/serve.toml.example`): bring a companion
service back after a reboot, or free your model server's memory. Each run is bounded by its
timeout, and output goes to `logs/actuators/<name>.log` rather than the response. Two rules
keep this honest: the config file alone decides what can run — there are no arguments at
request time — and stopping the bot never touches your model server. Freeing the model is
always a deliberate press of an actuator *you* declared, never a side effect.

---

## What it does *not* do

- **It doesn't make the daemon load-bearing.** Kill the daemon and a live conversation survives — the bot
  runs in its own process group and is *adopted*, never killed or double-started, when the daemon comes
  back.
- **It doesn't replace the file.** `active.toml` is still the durable record and the cold-boot truth. Hand
  edit + restart works exactly as before.
- **It doesn’t touch your model server, or any other service you run.** Those are watched and
  reported, never owned — an actuator acts only when you declare it *and* press it.

**Net:** one gate in `serve.toml`, one standalone facade, and switching becomes a press — live at your next
words when the pieces allow, a warm bot restart when they don't, with `active.toml.prev` behind you either
way.
