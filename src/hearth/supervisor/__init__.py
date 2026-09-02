"""hearth.supervisor — the daemon face: the always-on serve process owns the bot.

The supervisor core. Gate: config/serve.toml
[serve.supervisor] enabled = true — mounted ONLY by the standalone entry
(`python -m hearth.serve`); absent/false ⇒ nothing here imports, no routes
join, the facade is byte-identical (the house gate idiom). The in-process
bot attach never mounts this — a bot cannot supervise itself.

One door: every /admin route and the panel reverse-proxy ride the facade's
existing bearer middleware; the admin surface never exposes secret values.
Ownership: the voice bot is the ONE owned child; the LLM server, Open
WebUI, StreamCore, and the audio server are watched externals (reachability
booleans on /admin/state — never owned, though the operator may declare
actuators for them); memory sidecars stay owned by the glue that spawns
them. Deleting this directory (and the one mount call in serve/__main__.py)
restores the plain facade — the deletability test, by construction.

Also here (same deletability): /admin/switch — switch-companion as one
action, a registry-validated active.toml write + a supervised warm
restart (switch.py).

And the switch goes LIVE when it can — the
router consults the registry (switch.live_capable_fields) and hands the
bundle to the running bot's turn-boundary intent slot (bot-side half:
pipeline/switcher.py + the /switch/live panel routes); the supervised
restart stays the fallback and the cold path. Same button either way.
"""

from .routes import build_mount  # noqa: F401
