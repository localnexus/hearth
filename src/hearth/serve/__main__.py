"""python -m hearth.serve — run the facade standalone (sidecar mode).

Same gate, same app as the bot.py in-process attach; use it when the facade
should outlive (or precede) a voice session — chat clients up while the voice
appliance is down. Running both at once is safe: the
in-process attach finds the port busy and skips itself (warning; appliance
unharmed), and the standalone serves a snapshot of the config from ITS start
time. With a [serve.identity] pin in serve.toml the snapshot-staleness risk
is gone for persona/voice (they're fixed by config, not by bounce timing);
only the LLM-leg params still come from active.toml as of facade start.

Run from the repo root: `uv run python -m hearth.serve` (needs the .venv, same as bot.py).
"""

from __future__ import annotations

import asyncio
import sys

from hearth.config import config_loader


async def _main() -> int:
    cfg = config_loader.load_serve_config()
    if not cfg:
        print("[serve] config/serve.toml absent or enabled=false — nothing to run", file=sys.stderr)
        return 2
    active = config_loader.load_active()

    from . import app as serve_app

    # [serve.supervisor]: the daemon face mounts ONLY here, in the
    # standalone process — gate off/absent => mount=None, facade byte-identical.
    mount = None
    sup_cfg = dict(cfg.get("supervisor") or {})
    if sup_cfg.get("enabled"):
        from hearth.supervisor import build_mount  # lazy: loads only past the gate

        mount = build_mount(sup_cfg)

    runner = await serve_app.start(active, cfg, "", "", mount=mount)
    if runner is None:
        return 1
    try:
        await asyncio.Event().wait()  # serve until interrupted
    finally:
        await runner.cleanup()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
