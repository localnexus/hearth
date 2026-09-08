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
counts, stamps; conversation content is never read out). Two routes beside it
get a session FILE out, the same contract holding: POST /admin/sessions/reveal
shows it in the Finder (fixed argv, and a 409 when the browser is not on this
machine — the download is the off-machine answer), and GET
/admin/sessions/file streams the bytes as a download without parsing them.
POST /admin/sessions/deposit brings one IN — an uploaded (or pasted) session
file, checked against the deposit gate in session/verbs.py and written under a
freshly minted id, so a deposit adds to the shelf and can never replace what is
on it. POST /admin/sessions/archive and /unarchive are the soft verb: a move
into (and out of) the companion's .archive/, which takes a conversation off the
resume shelf and out of the fresh-start sweep without deleting anything —
GET /admin/sessions?archived=1 (or =all) is how the archived ones are read
back. POST /admin/sessions/destroy is the hard one, and the only verb here
that takes something away: preview-then-confirm (the confirm has to be the
session's name, or its id when it has none), and then the file AND the
session's memory — record, compaction epochs, indexed facts, through the same
forget the curation pane runs — in ONE act, memory first so a failed index
update leaves everything intact. It answers what it cannot reach as plainly as
what it did, and it is offered to a same-machine caller always, to anyone else
only where [serve.sessions] destroy_for_all says so. All three sit behind the
live-session guard: while a companion is up its whole shelf is read-only,
because the supervisor cannot know which single file the running bot holds.

/admin/memory is the record-level curation surface (curation.py): digest views + a
preview-then-confirm forget — the memory CLI's web half, living here because
the write-layer rule (c) puts every memory mutation behind this door.
POST /admin/bot/start
and the switch's restart rider accept "memory": full | recall-only | off (the
sitting's --memory posture); a live handoff never does — the mode is set at
boot and rides a live switch unchanged.

/admin/models is the weights surface (supervisor/models/): the enrolled
models with their state, fit, residency and unit, a scan of what is on disk,
and the four preview-then-confirm verbs — enroll, unenroll, render, apply. It
lives in its OWN package rather than here because guard rail R4's test reads
every file in this directory and refuses an import of hearth.weights; the
amendment the design signed off is that the admin model-management surface may
import it while the bot, the pipeline, serve/app.py and these lifecycle routes
still may not. When config/weights.toml declares a [weights.door], that package
also derives the two built-in actuators (door-unload / door-load) the mount
hands to ActuatorSet below.

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
    sessions.py   the resume shelf, plus reveal, download, deposit, the
                  archive/unarchive pair with the live-session guard they
                  share, and destroy — file + memory in one act
    lifecycle.py  bot start/stop, manual compaction, daemon restart
    switching.py  switch-companion: the live handoff, the supervised restart,
                  and the routing between them
    proxy.py      the catch-all forward to :65000 and the offline page

The sibling packages the table mounts — curation, roster, settings, firstrun,
models — sit one level up, beside this one.

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
from .. import models as models_mod
from .. import roster as roster_mod
from .. import settings as settings_mod

from .entry import (
    _LAUNCH_PAGE, _PAIR_MAX_TRIES, _PAIR_PAGE, _PAIR_TTL_S, _cookie, _launch,
    _pair_claim, _pair_mint, _pair_ui)
from .state import _actuator_run, _actuators_get, _http_alive, _state
from .sessions import (
    DEPOSIT_SUFFIXES, REVEAL_TIMEOUT_S, _already, _archive_request, _confirm_with,
    _destroy_offered, _guarded, _known_character, _move, _read_deposit_upload,
    _session_archive, _session_deposit, _session_destroy, _session_file,
    _session_reveal, _session_unarchive, _sessions)
from .lifecycle import _bot_start, _bot_stop, _compact_start, _daemon_restart
from .switching import (
    _FACADE_NOTE, _do_restart, _switch_get, _switch_live_get, _switch_post,
    _try_live)
from .proxy import PANEL_URL, _DROP_HEADERS, _OFFLINE_PAGE, _panel_proxy

__all__ = ["build_mount", "PANEL_URL"]


def stop_grace_for(sup_cfg: dict, mem_cfg) -> float:
    """SIGINT grace for the bot child. An explicit [serve.supervisor]
    stop_grace_s wins; otherwise it FOLLOWS the memory close budget
    ([memory] close_budget_s, default 120) plus the base STOP_GRACE_S, so a
    bounded memory close can never be SIGTERMed mid-store (2026-09-06)."""
    explicit = (sup_cfg or {}).get("stop_grace_s")
    if explicit is not None:
        return float(explicit)
    budget = 0.0
    if mem_cfg:
        try:
            budget = max(0.0, float(mem_cfg.get("close_budget_s", 120)))
        except (TypeError, ValueError):
            budget = 0.0
    return float(STOP_GRACE_S) + budget


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
            stop_grace_s=stop_grace_for(sup_cfg, config_loader.load_memory_config()),
            term_grace_s=float(sup_cfg.get("term_grace_s", TERM_GRACE_S)),
        )
        app["bot_child"] = child
        app["keeper"] = keeper.detect()  # who relaunches us; None on a terminal run
        app["panel_url"] = str(sup_cfg.get("panel_url") or PANEL_URL).rstrip("/")
        # Stroke 4: watched externals + declared actuators (never children).
        app["watches"] = {str(n): str(dict(w or {}).get("url") or "")
                          for n, w in dict(sup_cfg.get("watch") or {}).items()}
        # The operator's declared actuators, plus the two BUILT-IN ones derived
        # from [weights.door] when config/weights.toml declares a door
        # (models/door.py): door-unload / door-load, the same bounded,
        # never-a-child frame. A declared name of the same spelling wins, and
        # actuators declared under other names are untouched.
        app["actuators"] = actuators_mod.ActuatorSet(
            models_mod.with_door_actuators(dict(sup_cfg.get("actuators") or {})),
            log_dir=config_loader.DATA_DIR / "logs" / "actuators",
        )
        app.router.add_get("/admin/launch", _launch)
        app.router.add_get("/admin/state", _state)
        app.router.add_get("/admin/sessions", _sessions)
        app.router.add_post("/admin/sessions/reveal", _session_reveal)
        app.router.add_get("/admin/sessions/file", _session_file)
        app.router.add_post("/admin/sessions/deposit", _session_deposit)
        app.router.add_post("/admin/sessions/archive", _session_archive)
        app.router.add_post("/admin/sessions/unarchive", _session_unarchive)
        app.router.add_post("/admin/sessions/destroy", _session_destroy)
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
        # /admin/models — enroll → render → apply → load, the weights surface
        # (its own package, never under routes/: guard rail R4's test reads
        # every file HERE and refuses an import of hearth.weights).
        models_mod.add_routes(app)
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
