"""supervisor/routes/ — the /admin surface + the panel reverse-proxy.

Mounted into the standalone facade app by serve/__main__.py iff
[serve.supervisor] enabled = true. Every route rides the facade's existing
bearer middleware (one door; header auth only for now — browser-friendly auth
is a named later refinement). The ONE exception: GET /admin/launch serves a
static, contentless launch shell the middleware exempts (like /health) so a
plain browser can load it; the page asks for the bearer once, keeps it in
localStorage, and reaches every other route by authed fetch. Responses carry
names, states, and booleans only — never tokens, env values, or file contents
(the same posture as `hearth.config.check`, which prints keys and not values).

The catch-all proxy is registered LAST so every real facade route wins; any
other path forwards to the bot's control panel when the bot is up, and answers
an honest "offline — start me" when it is down.

/admin/switch is switch-companion as ONE
action: a registry-validated active.toml write + a supervised warm restart
(the mechanics live in switch.py; the restart runs as a background task so
the response returns before the SIGINT lands on the bot).

The router then routes each switch: a registry-consulted
(switch.live_capable_fields) LIVE handoff to the bot's /switch/live intent
slot when the bot is up and every changed field has a live path — the reply
then says applied: "live" and the bot swaps at its next turn boundary —
falling back to the supervised restart otherwise. The optional body key
"apply" steers it: "auto" (default) | "live" (live or 409, never restarts) |
"restart" (force the supervised-restart path).

GET /admin/first-run is the guided first sitting (firstrun/): the page the
launch page offers while the selected model id is still the shipped
placeholder or nothing has been said on this install — /admin/state carries
those two facts as first_run.

GET /admin/sessions lists the resume shelf (SessionMeta only — ids, names,
counts, stamps; conversation content is never read out). /admin/memory is the
record-level curation surface (curation.py): digest views + a
preview-then-confirm forget — the memory CLI's web half, living here because
the write-layer rule (c) puts every memory mutation behind this door.
POST /admin/bot/start
and the switch's restart rider accept "memory": full | recall-only | off (the
sitting's --memory posture); a live handoff never does — the mode is set at
boot and rides a live switch unchanged.

The operator can also declare watched externals and actuators:
[serve.supervisor.watch.<name>] URLs join /admin/state's
externals, and [serve.supervisor.actuators.<name>] commands — operator-fixed
argv, bounded, output to log files, never children — run via
POST /admin/actuators/<name>/run (GET /admin/actuators lists them). Warm stop
stays the default everywhere; a cold model stop happens only as a declared,
deliberately pressed actuator (§4).

── the package layout ───────────────────────────────────────────────────────
The handlers sit one group per file; none of them imports another, so the
order below is a reading order rather than a dependency chain. build_mount
stays HERE because the route table IS the map of the surface, and a map worth
having is one you can read in one place:

    entry.py      the two static shells, the cookie carrier, and device
                  pairing — everything that exists because a browser cannot
                  attach an Authorization header
    state.py      /admin/state's reachability probes, and the declared
                  actuators (list + run)
    sessions.py   the resume shelf
    lifecycle.py  bot start/stop, manual compaction, daemon restart
    switching.py  switch-companion: the live handoff, the supervised restart,
                  and the routing between them
    proxy.py      the catch-all forward to :65000 and the offline page

The two shells live beside entry.py, which resolves them from __file__.

This __init__ is the façade: it re-exports every name the parts define, so
`from hearth.supervisor import routes` still reaches all of them (serve mounts
build_mount; the page tests take _LAUNCH_PAGE and _PAIR_PAGE).
"""

from __future__ import annotations

from aiohttp import web
from loguru import logger

from ..child import STOP_GRACE_S, TERM_GRACE_S, BotChild
from .. import actuators as actuators_mod
from .. import compact_watch
from .. import curation as curation_mod
from .. import firstrun as firstrun_mod
from .. import keeper
from .. import roster as roster_mod
from .. import settings as settings_mod

from .entry import (
    _LAUNCH_PAGE, _PAIR_MAX_TRIES, _PAIR_PAGE, _PAIR_TTL_S, _cookie, _launch,
    _pair_claim, _pair_mint, _pair_ui)
from .state import _actuator_run, _actuators_get, _http_alive, _state
from .sessions import _sessions
from .lifecycle import _bot_start, _bot_stop, _compact_start, _daemon_restart
from .switching import (
    _FACADE_NOTE, _do_restart, _switch_get, _switch_live_get, _switch_post,
    _try_live)
from .proxy import PANEL_URL, _DROP_HEADERS, _OFFLINE_PAGE, _panel_proxy

__all__ = ["build_mount", "PANEL_URL"]


def build_mount(sup_cfg: dict):
    """→ mount(app) for serve_app.start(..., mount=...). Reads [serve.supervisor]."""

    def mount(app: web.Application) -> None:
        deps = app["deps"]
        overlay = {"LM_BASE_URL": deps.lm_base_url}
        if deps.lm_token and deps.lm_token != "lm-studio":
            overlay["LM_API_TOKEN"] = deps.lm_token
        overlay.update({str(k): str(v) for k, v in dict(sup_cfg.get("env") or {}).items()})

        from hearth.config import config_loader  # lazy: mirror the package gate idiom

        child = BotChild(
            env_overlay=overlay,
            log_path=config_loader.DATA_DIR / "logs" / "bot.log",
            stop_grace_s=float(sup_cfg.get("stop_grace_s", STOP_GRACE_S)),
            term_grace_s=float(sup_cfg.get("term_grace_s", TERM_GRACE_S)),
        )
        app["bot_child"] = child
        app["keeper"] = keeper.detect()  # who relaunches us; None on a terminal run
        app["panel_url"] = str(sup_cfg.get("panel_url") or PANEL_URL).rstrip("/")
        # Stroke 4: watched externals + declared actuators (never children).
        app["watches"] = {str(n): str(dict(w or {}).get("url") or "")
                          for n, w in dict(sup_cfg.get("watch") or {}).items()}
        app["actuators"] = actuators_mod.ActuatorSet(
            dict(sup_cfg.get("actuators") or {}),
            log_dir=config_loader.DATA_DIR / "logs" / "actuators",
        )
        app.router.add_get("/admin/launch", _launch)
        app.router.add_get("/admin/state", _state)
        app.router.add_get("/admin/sessions", _sessions)
        app.router.add_post("/admin/bot/start", _bot_start)
        app.router.add_post("/admin/bot/stop", _bot_stop)
        app.router.add_post("/admin/compact", _compact_start)
        app.router.add_post("/admin/daemon/restart", _daemon_restart)
        # Mutated IN PLACE at runtime (the app mapping is frozen after startup):
        # last = the most recent switch-intent's phase/outcome; task = the
        # in-flight supervised restart (one at a time).
        app["switch_state"] = {"last": None, "task": None}
        app.router.add_get("/admin/switch", _switch_get)
        app.router.add_get("/admin/switch/live", _switch_live_get)
        app.router.add_post("/admin/switch", _switch_post)
        app.router.add_get("/admin/actuators", _actuators_get)
        app.router.add_post("/admin/cookie", _cookie)
        # One active code, mutated in place (the app mapping freezes at startup).
        app["pair"] = {"code": "", "expires": 0.0, "tries": 0}
        app.router.add_post("/admin/pair", _pair_mint)
        app.router.add_post("/admin/pair/claim", _pair_claim)
        app.router.add_get("/admin/pair/ui", _pair_ui)
        app.router.add_post("/admin/actuators/{name}/run", _actuator_run)
        # /admin/memory — record-level curation (preview-then-confirm forget +
        # digest views; the CLI's web half, write-layer rule (c)).
        curation_mod.add_routes(app)
        # /admin/roster — the onboarding wizard (create-only; facade-hosted
        # operator-layer writes per rule (c); page shell exempt like /admin/launch).
        roster_mod.add_routes(app)
        # /admin/settings — the generated settings forms (schema-driven step 2:
        # registry-declared knobs, preview-then-confirm scalar writes, rule (c)).
        settings_mod.add_routes(app)
        # /admin/first-run — the guided first sitting behind the door hearth.init
        # opens (the first-run path's second half; shell exempt like /admin/launch).
        firstrun_mod.add_routes(app)
        # LAST on purpose: registered facade routes always win over the proxy.
        app.router.add_route("*", "/{tail:.*}", _panel_proxy)
        app.on_startup.append(_adopt_on_start)
        # The auto-compaction watch (design: auto-compaction-on-close) — runs
        # queued close-time requests once no bot is alive; lock-arbitrated.
        if bool(sup_cfg.get("compact_watch", True)):
            app.on_startup.append(compact_watch.start)
            app.on_cleanup.append(compact_watch.stop)
        app.on_cleanup.append(_release)
        logger.info("[supervisor] daemon face mounted (panel {})", app["panel_url"])

    return mount


async def _adopt_on_start(app: web.Application) -> None:
    # Adopt-don't-collide: a bot that predates (or outlived) this daemon is
    # reported, never killed or duplicated.
    await app["bot_child"].adopt()


async def _release(app: web.Application) -> None:
    # Daemon shutdown ABANDONS the child by design (own process group): a
    # daemon restart must never cost a live conversation. Re-adopted on start.
    app["bot_child"].close()
