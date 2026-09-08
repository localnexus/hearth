"""supervisor/models/ — /admin/models: enroll → render → apply → load, as one
surface.

G1 gave Hearth the vocabulary for weights (roots, a scan, a header, a fit
estimate, an enrolled reference) and G2 gave it the renderer (the launchd unit
derived from config that is already true, rather than kept by hand beside it).
Both arrived as a command line. This package is the other half: the four verbs
behind the launch page's **Models** card, so changing which weights the door
serves is something a person does on a page, in the order the design names —
enroll the file, render the unit, apply it, load the door — with every step
showing what it will do before it does it.

WHY IT IS ITS OWN PACKAGE, beside `routes/` rather than inside it. Guard rail
R4 says the live conversation loop never reaches the scanner, and its test
proves that by reading every file under `supervisor/routes/` and refusing any
import of `hearth.weights`. That rail is worth keeping exactly as strict as it
is, so the admin surface that DOES need the scanner lives one directory over
and is mounted from the route table like `curation`, `roster`, `settings` and
`firstrun`. The bot, the pipeline, `serve/app.py` and the standing lifecycle
routes still cannot see this package's dependencies.

Two consequences of that placement, both deliberate:

  * imports of the weights package name SUBMODULES explicitly
    (`from hearth.weights import enroll as enroll_mod`). The package façade
    re-exports `enroll` and `render` as FUNCTIONS, which shadow the modules of
    those names — the G1 shadowing note, and the reason no file here reaches
    through the façade;
  * every one of those calls is filesystem work — headers, stats, a plist, and
    in the scan's case a walk of every root — so every one of them crosses
    into a worker thread through `asyncio.to_thread`. The event loop is
    serving a launch page that polls every four seconds; it does not read
    38 GB files.

WHAT IT NEVER DOES: start, stop, or signal a process. `apply` writes a unit
file and hands back the two launchctl lines; the card's **Load** and
**Unload** buttons press `door-load` / `door-unload` through the actuator
runner, which is where a bounded, logged, non-child command belongs. Those two
actuators are DERIVED from `[weights.door]` rather than hand-declared (door.py),
so a machine that has named its door gets its buttons for free — and an
operator's own declaration of the same name still wins.

WHAT IT NEVER REPORTS: the path to the door's access key. `api_key_file` is a
path in config, passed to `--api-key-file` and never opened by the renderer;
this surface additionally keeps it out of every response body — argv and diff
rows are redacted, the door view answers `"set"` or `"unset"`. A path is not a
secret; the path to a secret is a map.

API (mounted by routes.build_mount iff [serve.supervisor] enabled; authed like
every /admin route):

    GET  /admin/models                → enrolled models: state (present |
         missing | changed — R3), fit, resident ●, unit (applied | stale |
         unapplied) + diff counts, plus the door and the enrollable targets
    GET  /admin/models/scan[?refresh=1]
                                      → candidates on disk with fit verdicts,
         marked with the model already holding them. Cached for the daemon's
         life; the walk is minutes on a real library
    POST /admin/models/enroll {model | new, identity | path, mmproj?, yes?}
         preview = the exact [weights] block; yes = write it
    POST /admin/models/unenroll {model, yes?}
         drops the reference, never the file
    POST /admin/models/render {model}  → the argv and the classified diff; the
         unit lands in DATA/render/ and nowhere else
    POST /admin/models/apply {model, yes?}
         preview = diff + the archive that would be made; yes = write the unit
         where launchd reads it (running nothing). Companion guard: blocked →
         409, uncertain → 200 with a warning
    GET  /admin/models/door           → [weights.door] facts; the access key as
         "set" | "unset", never its path

── the package layout ───────────────────────────────────────────────────────
    facts.py   the readings the routes share: state, fit, the unit on the
               machine, the enrollable targets, and the key-path redaction
    door.py    the door's own facts, and the two built-in actuators derived
               from [weights.door]
    views.py   the three reading routes (list, scan, door) and the residency
               probe
    verbs.py   the four mutations, preview-then-confirm

This __init__ is the façade: it re-exports every name the parts define, so
``from hearth.supervisor import models`` still reaches all of them.
"""

from __future__ import annotations

from aiohttp import web

from .facts import (
    HIDDEN, KEY_PATH_FLAGS, UnitState, budget_json, counts_of, example_model_toml,
    fit_text, header_facts, machine_budget, model_ctx, model_id, redact_argv,
    state_from, targets, unit_state)
from .door import (
    LAUNCHCTL, LOAD, TIMEOUT_S, UNLOAD, door_actuators, door_json,
    with_door_actuators)
from .views import (
    PROBE_TIMEOUT_S, _door, _models, _scan, budget_of, candidate_by_identity,
    enrolled_by_identity, resident_ids, scan_result, store)
from .verbs import (
    CONFIRM, NEW_NAME_RE, _apply, _enroll, _render, _unenroll)

__all__ = ["add_routes", "door_actuators", "with_door_actuators", "door_json",
           "LOAD", "UNLOAD"]


def add_routes(app: web.Application) -> None:
    """Called by routes.build_mount — same door, same middleware."""
    # The one mutable corner: the app mapping freezes after startup, so the
    # scan cache, its lock and the machine budget live inside this dict.
    app["models"] = {"scan": None, "objects": None, "lock": None, "budget": None}
    app.router.add_get("/admin/models", _models)
    app.router.add_get("/admin/models/scan", _scan)
    app.router.add_get("/admin/models/door", _door)
    app.router.add_post("/admin/models/enroll", _enroll)
    app.router.add_post("/admin/models/unenroll", _unenroll)
    app.router.add_post("/admin/models/render", _render)
    app.router.add_post("/admin/models/apply", _apply)
