# The config layers — who writes what

*Which file you edit, which files edit themselves, and which one you must never print. Hearth's settings
live in a few different places on purpose — this page tells you who owns each so you don't fight the panel or
leak a secret.*

**Authoritative sources:** knob-by-knob meaning → `docs/config-manual/` (its `README.md` maps request → file →
key, with a topic page per family); every key, default, and range in one generated table →
`docs/config-manual/settings-reference.md` and `settings-reference-gates.md`; the facade gate →
`config/serve.toml.example` (the committed template). This page is the ownership map; those hold the details.

Every layer below ships as a committed `.example` template. Copy it to the real (gitignored) filename before
you use it — the copying is yours either way, and it is the first thing a new install needs. **Once the
serve facade is running you can edit any of these as a form** on `/admin/settings/ui`, which validates
before it writes; until then these files are the only door, which is why this page names them plainly.
Either way this page is about *who owns what*, so it names the files rather than sending you to them. After a
hand-edit, run `python -m hearth.config.check` — it validates every config file
present on this install against its schema and prints a per-file verdict, naming **keys only, never
values**.

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

The golden rule: **know who writes a file before you edit it.** Some are yours; one is the panel's; one is a
secret you only ever manage, never read.

| Layer | Who writes it | You do… | Cross-reference |
|---|---|---|---|
| **`config/active.toml`** | **You** (operator) | Edit the `character` / `model` / `voice` selection (and an optional `persona` variant), then restart | [Switching who's live](switching-who-is-live.md) · `docs/config-manual/llm.md` |
| **`config/overrides.toml`** | **The :65000 panel** (live knobs) | **Don't hand-edit.** Read it to understand a sticky setting; let the panel manage it | `docs/config-manual/README.md` · `docs/runbook/02.5-control-panel.md` · [The live knobs panel](the-live-knobs-panel.md) |
| **`config/models/<model>/`** | **You** | Edit `model.toml` load facts + `system-prompt-template.md` | `docs/config-manual/llm.md` |
| **`config/serve.toml`** | **You** — but it holds a **bearer** | Manage the gate; **never print its contents** | This page, below · `config/serve.toml.example` |
| **`config/tts/<engine>/tts.toml`**, **`config/vad.toml`** | **Shipped baselines** (calibrated) | Leave alone unless you're re-calibrating by ear/mic — or use the panel's **VOICE** / **LISTENING** boxes, which layer live values over these from `overrides.toml`. A copy under your data root replaces the shipped file whole | `docs/config-manual/voice-tts.md` · `docs/config-manual/listening-vad-barge-in.md` · [The live knobs panel](the-live-knobs-panel.md) |
| **`characters/<name>/profile.toml`**, **`…/overrides.toml`** (and per voice) | **The panel** | The companion's saved knob preset, and a live mirror of its identity-scope knobs — they travel with the companion. Hands off | `docs/config-manual/README.md` · [The live knobs panel](the-live-knobs-panel.md) |
| **`config/memory.toml`**, **`config/openclaw.toml`** | **You** | Two more gates, both OFF by default: cross-session memory, and the companion's dispatch "hands" | `docs/memory.md` · `docs/config-manual/settings-reference-gates.md` |

---

## `active.toml` — the selection pointer (edit + restart)

Your one deliberate lever for *who's live*: `character`, `model`, `voice` — plus an optional
`persona = "<variant>"` that picks `persona.<variant>.md` beside the character's `persona.md`. It's read
**once at startup** by whichever lane reads it (a desk `bot.py`, or the facade) — nothing hot-swaps by
itself. Edit, then restart: [Switching who's live](switching-who-is-live.md).

With the supervisor daemon on, that same file is written *for* you by one press of the panel's
**COMPANION** switcher, which then applies it live when it can — [The one-button switch](the-one-button-switch.md).

## `overrides.toml` — the panel's layer (hands off)

This is where the **:65000 control panel** writes every live knob you turn while the companion is running:
`[llm]` temperature and reasoning_effort (the **CHARACTER** box), `[tts]` temperature/top_p/top_k/
repetition_penalty (the **VOICE** box), `[vad]` confidence/start_secs/stop_secs/min_volume (the
**LISTENING** box), and — when you audition a voice — `[voice] ref_wav`. Mechanics for all four boxes are
in [The live knobs panel](the-live-knobs-panel.md). The rule:

> **Operators don't hand-edit `overrides.toml`.** The panel owns it. Its live values **override
> `active.toml` every turn** while set — which is exactly why a voice pick can seem "stuck": a leftover
> `[voice]` section from a live audition wins over your selection until you clear it. **Clearing it is a
> button** — *Reset voice* in the panel's VOICE box, or *Restore ALL to defaults* for every layer at once.
> (That specific fix is `docs/runbook/05-fast-recovery.md`.) When in doubt, *read* it to see what the panel left
> behind — don't edit it by hand.

## `config/models/<model>/` — the model's facts and prompt

Two files per model dir, both **yours** to edit:

- **`model.toml`** — the model's **load facts**: `id`, `temperature`, `reasoning_effort`, and
  **`reliable_context`** — the measured usable-context line the panel's token gauge counts against (set it
  per model; absent, the panel falls back to the window the server reports). What `id` has to be
  depends on the server: `llama-server` (the default) serves its one loaded model whatever you put there,
  while LM Studio needs its own id **verbatim**. Note `context_length` is deliberately *not* here — the
  **live server value wins**.
- **`system-prompt-template.md`** — the **model layer** of the prompt: the envelope, the output-shaping hard
  rules, and the `{{persona}}` slot the character fills. Keep the "short, spoken, no markdown" rules or
  replies read badly aloud.

Every change here needs a **restart** to apply. Per-knob meaning lives in `docs/config-manual/llm.md`.

## `config/serve.toml` — the facade gate (manage, never print)

This is the switch that decides whether the **:65001 facade** runs at all, plus its auth. It's **yours to
manage** — but it holds a **bearer token path**, so it has one hard rule:

> **Never print, `cat`, or echo `config/serve.toml` or `config/serve-token`.** Not to "check the token,"
> not to debug. To confirm the facade is authing correctly, use the **inline-token idiom** instead — it
> feeds the token to a header and displays nothing:
> ```bash
> curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
> ```

What the *committed template* (`config/serve.toml.example`, safe to read) tells you without exposing the live
file:

- **`enabled = false` ⇒ nothing loads, no sockets** — the appliance is byte-identical when off. This is a
  **gate**, so "connection refused" after a bounce can simply mean it's disabled, not broken.
- **`host = "127.0.0.1"`** loopback by default; a tailnet reach is a `100.x` literal or `tailscale serve`
  fronting — **never `0.0.0.0`.**
- **Bearer auth is *always on*** — there is no unauthenticated mode, so a later tailnet bind can never become
  an accidental open door. `token_source` is a **path** to the token, never the token itself.

## The two gates you'll meet later: `memory.toml` and `openclaw.toml`

Both ship **off**, and both are byte-identical no-ops while off — the same house idiom as `serve.toml`.

- **`config/memory.toml`** turns on cross-session continuity: on a graceful stop the seam keeps a record of
  the conversation, and at the next session start the companion opens already knowing the shape of the last
  ones. `[memory.companions]` sets it **per companion**, so one can remember and another can be a stranger
  every time. The felt difference is the whole point — read `docs/memory.md` before enabling it, and
  remember a record is a file you can delete. Once it's on, the panel's status block grows a **Memory**
  line showing it live — see [Reading the panel](reading-the-panel.md). That line's one button (pause/resume
  voice recall) is the single exception to "this file decides": it's a runtime-only poke that never writes
  back to `memory.toml`.
- **`config/openclaw.toml`** gives the companion two narrow tools for dispatching work to an OpenClaw agent.
  One gate drives both the tools and the prompt paragraph that mentions them, so capability and prompt can
  never disagree. (Unrelated to [The OpenClaw voice lane](the-openclaw-voice-lane.md), which is about
  OpenClaw *speaking* in a Hearth voice.)

Which services keep these files loaded, and on which ports, is
[The map of doors](the-map-of-doors.md).

---

## The one-line takeaways

- **Want to change who's live?** `active.toml` + restart — or the panel's **COMPANION** switcher.
- **Want your companions outside the checkout?** Set `HEARTH_DATA`; the layout is identical there.
- **A setting won't stick?** The **panel's** `overrides.toml` is probably winning — clear it via the panel,
  don't hand-edit.
- **Tuning the model or its prompt?** `config/models/<model>/` + restart.
- **The facade won't answer?** Check the `serve.toml` gate — and **never** print it.
- **Hand-edited something and want to be sure?** `python -m hearth.config.check`.
