# 2.5a Companion switcher (supervisor daemon)

> Part of [2.5 Control panel & live status](../02.5-control-panel.md).

Switching who is live from the panel, and what the daemon does about it.

With `[serve.supervisor]` enabled in `serve.toml` **and** the standalone facade up, the panel
grows a **COMPANION** box: pick character / voice / persona / model and press **Switch**. The
panel relays the click to the daemon's authed `POST /admin/switch`, which validates the
selection (registry + on-disk existence — a refused switch writes nothing), writes
`config/active.toml` (previous copy kept as `active.toml.prev`), and then applies it the
lightest way it can:

- **Live** — when every changed piece has a live path (persona · voice · model *field* among
  models your LLM server already holds; such models show a ● in the picker), the daemon hands
  the bundle to the running bot and it applies **at your next words**: the old session
  finalizes exactly as a graceful stop would (memory record, hold honored), the new
  companion's context seeds with its own recall, and the voice re-clones — no restart, the
  page stays up and shows *switched ✓*.
- **Warm restart** — anything heavier, a bot that is down, or a refused live arm falls back to
  the restart path: the bot warm-restarts (your LLM server is never touched), the page goes
  down with it and reloads itself when the new one is up (≈10–30 s).

Tick **keep this session** to drop a hold marker first, so the current session is kept as a
**named (held)** one (the `stop.sh --hold` semantics; sessions save by default either way, and
for a recall-only sitting the marker is what keeps the transcript) — honored on BOTH paths. The box hides
itself when the daemon isn't configured, isn't reachable, or the panel is LAN-exposed
(`WEB_HOST` not loopback — use the facade's authed `/admin/switch` directly there).

## The same box on the facade

The switcher is **one implementation** (`src/hearth/ui/switch_card.js`), spliced into both the
panel page and the facade's [launch page](launch-page.md) at import. Same fields, same body,
same live-vs-restart reading — the daemon does the routing either way, so the two surfaces
cannot answer differently. What each host still owns is only what genuinely differs: the
transport (the panel's unauthed loopback relay vs. the facade's bearer), the start-only riders
(session + memory-mode, which the launch page adds on a cold start), and the aftermath — the
panel dies with a restarting bot and waits for its own return, while the facade page stays up.

On the launch page the same box reads **Start** while the bot is down and **Switch** while it is
up, so a warm switch no longer needs the desk. `tests/supervisor/test_shared_switch_card.py`
guards the sharing: it fails if either page rebuilds the pickers locally.

| Route | What |
|---|---|
| `GET /admin/switch/live` | Read-through to the bot's own `GET /switch/live` — model **residency** (the ● marks) and the moment a live handoff actually lands, both facts only the bot holds. Never an error: a down, older, or unreachable bot answers `{"ok": false, "reason": …}` and the card degrades to plain names. Names and states only. |
