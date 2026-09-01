"""hearth.supervisor — the daemon face: the always-on serve process owns the bot.

ADR 007 stroke 1 (the supervisor core). Gate: config/serve.toml
[serve.supervisor] enabled = true — mounted ONLY by the standalone entry
(`python -m hearth.serve`); absent/false ⇒ nothing here imports, no routes
join, the facade is byte-identical (the house gate idiom). The in-process
bot attach never mounts this — a bot cannot supervise itself.

One door (ADR 002 / D7): every /admin route and the panel reverse-proxy ride
the facade's existing bearer middleware; X-03 stays strict. Ownership
(ADR 007 §3): the voice bot is the ONE owned child; the LLM server, Open
WebUI, StreamCore, and the audio server are watched externals (reachability
booleans on /admin/state — actuators are stroke 4); memory sidecars stay
owned by the glue that spawns them. Deleting this directory (and the one
mount call in serve/__main__.py) restores the pre-ADR-007 facade — the
deletability test, by construction.
"""

from .routes import build_mount  # noqa: F401
