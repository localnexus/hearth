"""models/door.py — the door itself: its facts, its residency, and the
built-in actuators that stop, start and restart it.

Until now the pair of commands that bounce the model server had to be typed
into `serve.toml` by hand — the exact two lines `weights apply` prints, copied
into an actuator block with the machine's uid and the LaunchAgents path baked
in. Every fact in them is already in `[weights.door]`, so this module derives
them instead:

    door-unload   launchctl bootout gui/<uid>/<label>
    door-load     launchctl bootstrap gui/<uid> <LaunchAgents>/<label>.plist
    door-reload   door-unload, wait for the door to leave, door-load

They are ordinary members of the existing actuator frame — fixed argv, no
shell, bounded, output to a 0600 log, never children of this daemon — and they
carry `guard = "companion"`, because taking the door down (or replacing it
under a load) is a cost the next turn pays. Nothing here runs them: they are
CONFIG, handed to `ActuatorSet` at mount, and the actuator runner presses them.

The third is the one the card's **Load** button presses. `bootout` returns
the moment launchd has accepted the order (10 ms on a 38 GB door, measured
2026-09-16), while the process itself takes seconds to leave — so a bare
`bootstrap` pressed right after fails with "already loaded", and a bare Load
pressed against a running door does nothing at all. The stepped shape holds
the two lines together: bootout (exit 3, "not loaded", counts as done), then
wait until nothing answers on the door's port, then bootstrap, pressed again
once a second for a bounded while if launchd is still tearing the old job
down. Both halves stay the same two launchctl argvs; only the runner presses
them.

Two rules on registration:
  * they appear only when the operator's `config/weights.toml` actually
    declares a `[weights.door]` table — a machine that never named a door gets
    no buttons for one;
  * an operator-declared actuator of the same name WINS. A declaration in
    serve.toml is the operator saying the words themselves, and a derived
    default never overwrites that.

Actuators the operator declared under OTHER names (the hand-written
`lm-load` / `lm-unload` pair this design replaces) are untouched and keep
working. They simply have a built-in equivalent now.
"""

from __future__ import annotations

import os
from importlib import import_module

from hearth.config import config_loader as cl

# The weights submodules, by NAME rather than by attribute. The package façade
# re-exports `enroll` as a FUNCTION, so `from hearth.weights import enroll`
# hands back the function and shadows the module of that name (the G1 shadowing
# note). `import_module` resolves the real submodule every time, and is the one
# spelling that cannot quietly become the wrong object as the façade grows.
render_mod = import_module("hearth.weights.render")
roots_mod = import_module("hearth.weights.roots")

#: launchd's own front door. An absolute path, because an actuator's argv is
#: exec'd directly — there is no shell and no PATH lookup.
LAUNCHCTL = "/bin/launchctl"

#: The three names. Fixed, so the card can find them and the manual can name them.
UNLOAD = "door-unload"
LOAD = "door-load"
RELOAD = "door-reload"

#: A bootstrap that has to read a 38 GB file off a cold disk is not a two
#: second act; the probe is what actually says "up".
TIMEOUT_S = 60.0
#: How long the reload waits for the old door to leave its port, and for how
#: long it keeps pressing bootstrap while launchd still holds the old job.
RELOAD_WAIT_S = 45.0
RELOAD_RETRY_S = 20.0
#: launchctl's exit for "no such service" on bootout — an unloaded door is
#: already where a reload's first step wants it.
_BOOTOUT_NOT_LOADED = 3


def door_actuators(cfg: roots_mod.WeightsConfig | None = None,
                   uid: int | None = None) -> dict:
    """The two derived actuator blocks, or {} when no door is declared."""
    cfg = cfg or roots_mod.load_weights_config()
    if not cfg.door_declared:
        return {}
    door = cfg.door
    uid = os.getuid() if uid is None else int(uid)
    unit = render_mod.launch_agents_dir() / f"{door.label}.plist"
    probe = f"http://{door.host}:{door.port}/health"
    return {
        UNLOAD: {
            "command": [LAUNCHCTL, "bootout", f"gui/{uid}/{door.label}"],
            "timeout_s": TIMEOUT_S,
            "probe_url": probe,
            "guard": "companion",
            "note": f"stop the door ({door.label}) — built in from [weights.door]",
        },
        LOAD: {
            "command": [LAUNCHCTL, "bootstrap", f"gui/{uid}", str(unit)],
            "timeout_s": TIMEOUT_S,
            "probe_url": probe,
            "guard": "companion",
            "note": f"start the door ({door.label}) from the unit `apply` wrote "
                    "— built in from [weights.door]",
        },
        RELOAD: {
            "steps": [
                {"actuator": UNLOAD, "accept": [0, _BOOTOUT_NOT_LOADED],
                 "until": "probe-down", "wait_s": RELOAD_WAIT_S},
                {"actuator": LOAD, "retry_s": RELOAD_RETRY_S},
            ],
            "timeout_s": TIMEOUT_S,
            "probe_url": probe,
            "guard": "companion",
            "note": f"restart the door ({door.label}) on the unit `apply` wrote: "
                    "stop it, wait for it to leave, start it — built in from "
                    "[weights.door]",
        },
    }


def with_door_actuators(declared: dict, cfg: roots_mod.WeightsConfig | None = None,
                        uid: int | None = None) -> dict:
    """The operator's declared actuators, plus the built-in pair.

    Declared names win. A malformed or unreadable weights.toml costs the two
    built-ins and nothing else — the mount must never fail on a file that is
    not about the supervisor.
    """
    out = dict(declared or {})
    try:
        derived = door_actuators(cfg, uid)
    except cl.ConfigError:
        return out
    for name, block in derived.items():
        out.setdefault(name, block)
    return out


# ── the door's facts, as a response ──────────────────────────────────────────

def door_json(cfg: roots_mod.WeightsConfig) -> dict:
    """`[weights.door]` for the card. The access key is reported as SET or
    UNSET and its path never appears — see facts.py's rule."""
    door = cfg.door
    return {
        "declared": cfg.door_declared,
        "label": door.label,
        "host": door.host,
        "port": door.port,
        "threads": door.threads,
        "load_mode": door.load_mode,
        "webui": door.webui,
        "api_key": "set" if door.api_key_file else "unset",
        "log_file": str(door.log_file) if door.log_file else None,
        "unit_path": str(render_mod.launch_agents_dir() / f"{door.label}.plist"),
        "rendered_path": str(cl.DATA_DIR / render_mod.RENDER_SUBDIR
                             / f"{door.label}.plist"),
        "actuators": ({"load": LOAD, "unload": UNLOAD, "reload": RELOAD}
                      if cfg.door_declared else {}),
        "source": str(cfg.source) if cfg.source else None,
    }
