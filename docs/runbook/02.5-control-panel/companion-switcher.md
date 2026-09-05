# 2.5a Companion switcher (launch page)

> Part of [2.5 Control panel & live status](../02.5-control-panel.md).

Switching who is live from the panel, and what Hearth does about it.

With `[serve.supervisor]` enabled in `serve.toml` **and** the standalone Hearth up, the panel
grows a **COMPANION** box: pick character / voice / persona / model and press **Switch**. The
panel relays the click to Hearth's authed `POST /admin/switch`, which validates the
selection (registry + on-disk existence — a refused switch writes nothing), writes
`config/active.toml` (previous copy kept as `active.toml.prev`), and then applies it the
lightest way it can:

- **Live** — when every changed piece has a live path (persona · voice · model *field* among
  models your model server already holds; such models show a ● in the picker), Hearth hands
  the bundle to the running companion and it applies **at your next words**: the old session
  finalizes exactly as a graceful stop would (memory record, hold honored), the new
  companion's context seeds with its own recall, and the voice re-clones — no restart, the
  page stays up and shows *switched ✓*.
- **Warm restart** — anything heavier, a companion that is down, or a refused live arm falls back to
  the restart path: the companion warm-restarts (your model server is never touched), the page goes
  down with it and reloads itself when the new one is up (≈10–30 s).

**Which voice a character comes up in.** Voice and persona belong to the character, so picking
a different one re-derives both. Staying on the current character keeps what is live; moving to
another reaches for **their remembered voice** — `voice = "<bundle>"` at the top of
`characters/<c>/profile.toml`, a hand-edit you make once (see
[bring-your-own-voice.md](../../bring-your-own-voice.md), "Multiple voices"). Without a pin the
picker offers whichever bundle sorts first, which is only right by luck once a character holds
a dozen auditions and the keeper is the last of them. The pin is a picker default and nothing more: `active.toml`
is still the selection, a save from the panel's preset buttons carries the key through
untouched, and a pin naming a bundle that has been renamed away falls back to first-in-list
rather than pre-selecting a voice that cannot load.

Tick **keep this session** to drop a hold marker first, so the current session is kept as a
**named (held)** one (the `stop.sh --hold` semantics; sessions save by default either way, and
for a recall-only sitting the marker is what keeps the transcript) — honored on BOTH paths. The box hides
itself when Hearth isn't configured, isn't reachable, or the panel is LAN-exposed
(`WEB_HOST` not loopback — use Hearth's authed `/admin/switch` directly there).

## The same box on Hearth

The switcher is **one implementation** (`src/hearth/ui/switch_card.js`), spliced into both the
panel page and Hearth's [launch page](launch-page.md) at import. Same fields, same body,
same live-vs-restart reading — Hearth does the routing either way, so the two surfaces
cannot answer differently. What each host still owns is only what genuinely differs: the
transport (the panel's unauthed loopback relay vs. Hearth's access key), the start-only riders
(session + memory-mode, which the launch page adds on a cold start), and the aftermath — the
panel dies with a restarting companion and waits for its own return, while Hearth page stays up.

On the launch page the same box reads **Start** while the companion is down and **Switch** while it is
up, so a warm switch no longer needs the desk. `tests/supervisor/test_shared_switch_card.py`
guards the sharing: it fails if either page rebuilds the pickers locally.

| Route | What |
|---|---|
| `GET /admin/switch/live` | Read-through to the companion's own `GET /switch/live` — model **residency** (the ● marks) and the moment a live handoff actually lands, both facts only the companion holds. Never an error: a down, older, or unreachable companion answers `{"ok": false, "reason": …}` and the card degrades to plain names. Names and states only. |
