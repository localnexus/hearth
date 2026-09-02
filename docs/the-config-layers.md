# The config layers — who writes what

*Which file you edit, which files edit themselves, and which one you must never print.
Hearth's settings live in a few different places on purpose — this page tells you who owns
each, so you don't fight the panel or leak a secret.*

Copy each layer from its committed `.example` template into the real (gitignored) file before
you use it.

---

## Two roots: the engine tree and your data

Every path below is relative to one of two anchors:

| Anchor | Env var | Holds | Default |
|---|---|---|---|
| **Engine tree** | `HEARTH_ROOT` | the code, the shipped baselines (`config/tts/`, `config/vad.toml`), the `example` character and model config, the `.example` templates | the checkout (found from the package) |
| **Data root** | `HEARTH_DATA` | **everything you own**: `characters/` (persona, voices — and each companion's `sessions/`, `transcripts/`, `captures/`, `profile.toml`), `config/models/`, `config/active.toml`, `config/overrides.toml`, `config/serve.toml` + token | **the engine tree** |

Leave `HEARTH_DATA` unset and the checkout doubles as your data root — the layout is
exactly the one described here, and the public `.gitignore` keeps your companions, model
configs, and runtime files out of git. Set it (a vault, `~/.hearth`, anywhere) to keep
companions outside the checkout entirely: one directory per companion is then the whole
companion — copy it to move it, delete it to erase it. Lookup is **data root first, then
the engine tree**, so the shipped `example` stays reachable from an empty data root, and
your own `characters/example/` would shadow it. `./start.sh --check` prints both roots.

## The layers at a glance

The golden rule: **know who writes a file before you edit it.** Some are yours; one is the
control panel's; one is a secret you only ever manage, never read.

| Layer | Who writes it | You do… |
|---|---|---|
| **`config/active.toml`** | **You** (operator) | Edit the `character` / `model` / `voice` selection (and an optional `persona` variant), then restart |
| **`config/overrides.toml`** | **The control panel** (live knobs) | **Don't hand-edit.** Read it to understand a sticky setting; let the panel manage it |
| **`config/models/<model>/`** | **You** | Edit `model.toml` load facts + `system-prompt-template.md` |
| **`config/serve.toml`** | **You** — but it holds a **bearer token path** | Manage the gate; **never print its contents** |
| **`config/tts/<engine>/tts.toml`**, **`config/vad.toml`** | **Shipped baselines** (calibrated) | Leave alone unless you are re-calibrating by ear/mic; the panel's `overrides.toml` layers over them. A copy under your data root replaces the shipped file |
| **`characters/<name>/profile.toml`**, **`…/overrides.toml`** (and per voice) | **The control panel** | The companion's saved knob preset, and a live mirror of its identity-scope knobs — they travel with the companion. Hands off |
| **`config/memory.toml`**, **`config/openclaw.toml`** | **You** (operator) | Two more gates, both **OFF** by default: cross-session memory, and the companion's OpenClaw dispatch "hands" |

---

## `active.toml` — the selection pointer (edit + restart)

Your one deliberate lever for *who's live*: `character`, `model`, `voice` — plus an optional
`persona = "<variant>"` that picks `persona.<variant>.md` beside the character's `persona.md`.
It's read **once at startup** — nothing hot-swaps. Edit, then restart.

With the **supervisor daemon** enabled (`[serve.supervisor]` in `serve.toml`), that ritual is
one action: the control panel's **COMPANION** switcher — or `POST /admin/switch` at the facade —
validates your selection against the settings registry, writes this file (previous copy kept
beside it as `active.toml.prev`), and applies it the lightest way it can: LIVE at your next
words when every changed piece has a live path (persona · voice · a model the server already
holds), else a warm bot restart. Same file, same semantics, one button; hand-edit + restart
keeps working unchanged (comments don't survive a daemon write).

## `overrides.toml` — the panel's layer (hands off)

This is where the **control panel** writes the live knobs you turn while a session is running
(temperature, and when you audition a voice, a `[voice] ref_wav`). The rule:

> **Operators don't hand-edit `overrides.toml`.** The panel owns it. Its live values
> **override `active.toml` every turn** while set — which is exactly why a voice pick can seem
> "stuck": a leftover `[voice]` section from a live audition wins over your `active.toml` edit
> until you clear it and restart. When in doubt, *read* it to see what the panel left behind —
> don't edit it by hand.

## `config/tts/<engine>/tts.toml` and `config/vad.toml` — the shipped baselines

These two are **calibration, not selection**, and they ship pre-set. `tts.toml [live]` holds
the synth knobs (temperature, top_p, top_k, repetition_penalty) — every value equals the
engine's own default, so the file is a documented no-op until you change it — and
`[tag_profiles.<tag>]` holds the per-tag deltas that deepen a style tag (`[crying]`,
`[happy]`, …) for the one chunk that carries it. Those deltas are **ear-calibrated**: change
them by listening, not by arithmetic. `vad.toml [live]` is the listening calibration —
mic, room, your speech habits — and is the right file to touch if the companion cuts you
off or waits too long. Both are overlaid by the panel's `overrides.toml` at a turn boundary.

## `config/models/<model>/` — the model's facts and prompt

Two files per model dir, both **yours** to edit:

- **`model.toml`** — the model's **load facts**: `id` (the id your inference server advertises,
  verbatim), `temperature`, `reasoning_effort`, and **`reliable_context`** — the measured
  usable-context line the panel's token gauge counts against (if absent, the panel falls back
  to the advertised window). Note `context_length` is deliberately *not* here — the live value
  from your server wins.
- **`system-prompt-template.md`** — the **model layer** of the prompt: the envelope, the
  output-shaping hard rules, and the `{{persona}}` slot the character fills. Keep the "short,
  spoken, no markdown" rules or replies read badly aloud.

Every change here needs a **restart** to apply.

## `config/serve.toml` — the facade gate (manage, never print)

This is the switch that decides whether the optional bearer-authed **serve facade** runs at
all, plus its auth. It's **yours to manage** — but it holds a **bearer token path**, so it has
one hard rule:

> **Never print, `cat`, or echo `config/serve.toml` or `config/serve-token`.** Not to "check
> the token," not to debug. To confirm the facade is authing correctly, feed the token to a
> header without displaying it:
> ```bash
> curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
> ```

What the committed template (`config/serve.toml.example`, safe to read) tells you without
exposing the live file:

- **`enabled = false` ⇒ nothing loads, no sockets** — the appliance is byte-identical when
  off. This is a **gate**, so "connection refused" after a restart can simply mean it's
  disabled, not broken.
- **`host = "127.0.0.1"`** loopback by default; if you ever reach it from elsewhere, front it
  with a private overlay network — **never `0.0.0.0`.**
- **Bearer auth is *always on*** — there is no unauthenticated mode, so a later network bind
  can never become an accidental open door. `token_source` is a **path** to the token, never
  the token itself.

---

## The one-line takeaways

- **Want to change who's live?** `active.toml` + restart — or the panel's **COMPANION** switcher (supervisor daemon on; live at your next words when the pieces allow).
- **Want your companions outside the checkout?** Set `HEARTH_DATA`; the layout is identical there.
- **A setting won't stick?** The **panel's** `overrides.toml` is probably winning — clear it
  via the panel, don't hand-edit.
- **Tuning the model or its prompt?** `config/models/<model>/` + restart.
- **The facade won't answer?** Check the `serve.toml` gate — and **never** print it.
