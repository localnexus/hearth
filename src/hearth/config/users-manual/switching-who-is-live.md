# Switching who's live — character, voice, model

*The hand-edit ritual for changing **who** answers: pick the companion in one file, make sure no one's
mid-conversation, restart the lane that serves them, confirm the right one came back.*

**Authoritative sources:** what each config key means → `docs/config-manual/llm.md` +
`docs/config-manual/voice-tts.md`; the launch/stop drill → `docs/runbook/`. This page is the *sequence*
that ties them together.

> **There is a one-action version of this.** With the supervisor daemon on, the control panel's
> **COMPANION** box does all four steps for you — and usually without any restart at all. See
> [The one-button switch](the-one-button-switch.md). This page stays the ground truth underneath it:
> the daemon performs exactly this ritual, and hand-edit + restart keeps working unchanged.

---

## What owns "who's live"

`config/active.toml` is the selection pointer. Whichever lane reads it — the **desk voice loop**
(`python -m hearth.pipeline.bot`, the panel on `:65000` living inside it) or the optional **serve facade**
(`python -m hearth.serve`, `:65001`) — reads it **once, at its own startup** and pins that character +
voice for the whole run. Nothing hot-swaps by itself. So switching by hand is always the same shape:
**edit the pointer → restart that lane → confirm**.

> **One file, four keys.** `config/active.toml` holds `character`, `model`, `voice`, and an optional
> `persona` variant. It's **yours and gitignored** — your live selection, never committed. Edit it
> freely; you're not dirtying the repo. (The committed template is `config/active.toml.example`.)

> **The facade can be pinned away from it.** If `config/serve.toml` carries a `[serve.identity]` table,
> the facade's persona and voice come from *that* fixed selection and `active.toml` supplies only the
> LLM leg. A pinned facade will not follow this edit — by design.

---

## The four steps

### 1 · Edit the selection

Open `config/active.toml` and set what you want:

```toml
character = "<name>"     # a dir under characters/
model     = "<name>"     # a dir under config/models/
voice     = "<tag>"      # a bundle under characters/<name>/voices/
persona   = "default"    # optional: "default" = persona.md, else persona.<name>.md
```

Change one or all four. (Adding a *new* character or voice first? See
[Onboarding a character](onboarding-a-character.md).)

Worth doing before you restart, especially after a hand-edit:

```bash
python -m hearth.config.check
```

It validates every config file present on this install against its schema and prints a per-file verdict —
keys only, never values. A typo'd character name is cheaper to find here than in a failed launch.

### 2 · Confirm no one is mid-conversation — **do this before you restart**

Restarting a lane **kills any live session it is serving.** Check it's empty first:

```bash
lsof -nP -iTCP:65000 -iTCP:65001 -sTCP:ESTABLISHED
```

**Empty output = safe to switch.** Any established connection = someone (a browser on the panel, a chat
client on the facade) is mid-turn — **wait**. Never restart out from under a live session.

> Don't probe your LLM server's port for this. `llama-server`'s default is `:8080`, and the connections
> you'd see there are Hearth talking to the model, not a person talking to the companion.

### 3 · Restart the lane that serves the companion

For the **desk voice loop**, that's the ordinary stop/start:

```bash
./stop.sh          # SIGINT → graceful: session finalize, memory record, capture close
./start.sh         # preflight, then back online (~10–20 s to warm)
```

`./stop.sh --hold` instead keeps the current session instead of letting it go.

For the **serve facade**, restart however you run it — foreground `python -m hearth.serve`, or whatever
service manager keeps it alive on your machine (a launchd agent, a systemd user unit). With the
supervisor daemon on, `POST /admin/daemon/restart` does it through the one authed door.

### 4 · Confirm the right one came back

The desk loop tells you on its panel: the **`Agent`** line of the status block names the live
`character` + `voice`, resolved from `active.toml` at startup.

The facade advertises it. Two agreeing checks — what it *serves*, and what its startup line *says*:

```bash
curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
```
The returned model `id` is the active character — it should be your new pick. Then the facade's startup
line agrees (wherever you send its output):
```
[serve] /v1 facade → http://127.0.0.1:65001/v1 (character=<name>)
```
When those two match your edit, the switch took.

---

## The gotchas — the things the checks won't tell you

- **The voice is the one thing the log can't confirm.** The startup line names the **character**, never
  the **voice bundle**. There's no read-out for "which `.wav` is being cloned." The only check that
  matters is your **ear** — listen to the first spoken reply and make sure it's the voice you meant.
- **A leftover `[voice]` in `overrides.toml` beats your edit.** The panel writes that layer when you
  audition a voice, and it wins every turn while it's set. That's the classic "I changed it and it didn't
  change" — see [The config layers](the-config-layers.md).
- **`serve.toml` `enabled = false` means the facade won't run at all.** If a restart is followed by
  *connection refused*, that's the **gate**, not a fault — the facade is designed to load nothing and open
  no socket when disabled. (See [When it misbehaves](when-it-misbehaves.md).)
- **Restarting your LLM server changes nothing about who the companion is.** Persona and voice are
  Hearth's, not the model server's.

---

## What each lane does when you switch

Lanes read `active.toml` at *different* moments, so a switch reaches them differently:

| Lane | How it learns the new pick | What you do |
|---|---|---|
| **Desk voice loop** (`bot.py`, panel `:65000`) | Snapshots `active.toml` at **its own** launch | `./stop.sh` then `./start.sh` — or one press of the panel's **COMPANION** switcher ([the one-button switch](the-one-button-switch.md)) |
| **Serve facade** (`:65001`, optional) | Snapshots `active.toml` when **it** starts — unless `[serve.identity]` pins it | Restart the facade (step 3) |
| **Chat clients on the facade** | Read `/v1/models` fresh; each conversation keeps the id it opened with | **No restart.** Start a new chat and the new character id shows |

> **The mental model:** the voice loop and the facade are *two independent readers* of the same selection
> file. Each takes its snapshot when it starts. Restarting one never disturbs the other.

**Net:** edit `active.toml`, run `config.check`, confirm the ESTABLISHED probe is empty, restart the lane,
then let the `Agent` line (or `/v1/models`), the startup line, and your ear all agree.
