"""weights/render.py — the launchd unit, rendered from the config that is already true.

Today the unit file IS the truth: a plist under `~/Library/LaunchAgents` holds
the door's whole command line, and every fact in it — which weights, which
context, which port — is duplicated in Hearth's config by hand. This module
turns that around. The argv is DERIVED:

    [weights] in config/models/<model>/model.toml   →  --model, --mmproj
    [server]  in the same file                      →  this model's door flags
    [weights.door] in config/weights.toml           →  the door's own facts

and the plist is a rendering of the derived argv. Nothing here reads a secret:
`api_key_file` is a PATH, passed to `--api-key-file` and never opened.

Three acts, and only the third writes outside the data folder:

  render      → RenderedUnit(plist_text, argv, path); the file lands in
                DATA/render/<label>.plist, never in ~/Library/LaunchAgents.
  render --diff → the rendered argv against a plist already on the machine, as
                sets of (flag, value). Differences are classified: `placement`
                (the flags `--fit` manages), `deprecated-form` (`--mlock` /
                `--no-direct-io` against today's `--load-mode`), or `real`.
                Only a `real` difference is a finding.
  apply       → copies the rendered plist over `~/Library/LaunchAgents/<label>.plist`
                with `--yes`, archiving whatever was there beside it first, and
                PRINTS the two launchctl lines. It does not run launchctl, and
                it refuses while a companion is running.

The renderer is enroll-time code like the rest of this package: nothing in the
live conversation loop imports it (guard rail R4), and it starts no program at
all — the door's own vocabulary is read from the model's config, not by asking
another product anything (R1).
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hearth.config import config_loader as cl

from . import roots as roots_mod
from .enroll import WeightsError, load_enrolled, model_toml

#: Where a rendered unit is written. Under the data folder, always.
RENDER_SUBDIR = "render"

#: The flags `--fit` decides for itself. A live unit that sets them by hand and
#: a rendered one that leaves them alone are the SAME unit for our purposes —
#: the design record's row-3 pick (header estimate + --fit + /props).
PLACEMENT_FLAGS = frozenset({"n-gpu-layers", "main-gpu", "tensor-split", "split-mode"})

#: The loading trio the door deprecated in favour of `--load-mode`. A unit
#: carrying the old spelling and one carrying the new say the same thing.
DEPRECATED_LOAD_FLAGS = frozenset({"mlock", "no-mlock", "mmap", "no-mmap",
                                   "direct-io", "no-direct-io", "load-mode"})

#: Short spellings a `[server]` key or an older unit may use, mapped to the
#: long flag the diff compares on. Only pairs the door itself documents.
FLAG_ALIASES: dict[str, str] = {
    "m": "model", "c": "ctx-size", "t": "threads", "b": "batch-size",
    "ub": "ubatch-size", "np": "parallel", "ngl": "n-gpu-layers",
    "fa": "flash-attn", "ctk": "cache-type-k", "ctv": "cache-type-v",
    "lm": "load-mode", "dio": "direct-io", "ndio": "no-direct-io",
    "mg": "main-gpu", "ts": "tensor-split", "sm": "split-mode",
}

#: The plist keys, in the order they are written. Deterministic on purpose: a
#: rendered unit must be byte-comparable with the one rendered yesterday.
PLIST_KEY_ORDER = ("Label", "ProgramArguments", "RunAtLoad", "KeepAlive",
                   "ThrottleInterval", "StandardOutPath", "StandardErrorPath",
                   "WorkingDirectory")

#: launchd restart policy — the shape today's live unit already has.
THROTTLE_INTERVAL = 10


@dataclass(frozen=True)
class RenderedUnit:
    model_name: str
    label: str
    argv: list[str]
    plist_text: str
    path: Path              # where render writes it (DATA/render/<label>.plist)


@dataclass(frozen=True)
class Difference:
    """One flag the two command lines disagree about.

    A flag can differ three ways, and the booleans keep them apart: it is in one
    and not the other (`in_rendered` / `in_live`), or it is in both with
    different values, or it is in both and one of them carries no value at all
    (a bare switch) — which is why a value of None is not the same fact as a
    flag being absent.
    """
    flag: str               # long form, no dashes
    rendered: str | None    # the value we would pass; None = a bare switch
    live: str | None        # the value the plist on disk passes
    kind: str               # placement | deprecated-form | real
    in_rendered: bool = True
    in_live: bool = True

    @property
    def is_real(self) -> bool:
        return self.kind == "real"

    def shown(self, side: str) -> str:
        """One side of the row, in words: absent, a bare switch, or a value."""
        present = self.in_rendered if side == "rendered" else self.in_live
        value = self.rendered if side == "rendered" else self.live
        if not present:
            return "(absent)"
        return "(set, no value)" if value is None else value


@dataclass(frozen=True)
class Guard:
    """What the machine says about a companion being up, and whether that
    blocks an apply. `certain` is False when nothing authoritative answered."""
    blocked: bool
    certain: bool
    text: str


# ── argv ─────────────────────────────────────────────────────────────────────

def _flag(key: str) -> str:
    """A `[server]` key as the door spells it: one letter is a short flag."""
    key = str(key).lstrip("-")
    return f"-{key}" if len(key) == 1 else f"--{key}"


def _value(value: Any) -> str:
    """A TOML scalar as one argv token. Floats keep the spelling TOML gave."""
    if isinstance(value, float):
        text = repr(value)
        return text[:-2] if text.endswith(".0") else text
    return str(value)


def server_argv(server: dict) -> list[str]:
    """A model's `[server]` table as argv, in the order the file declares.

    A boolean is a switch: `true` is the bare flag, `false` is the flag left
    out (never `--no-x` — the door spells its own negations, and a config that
    wants one names it). Everything else is a valued flag, `flash-attn = "on"`
    included: it takes a value on this door and keeps it.
    """
    out: list[str] = []
    for key, value in (server or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                out.append(_flag(key))
            continue
        if isinstance(value, list):
            for item in value:
                out += [_flag(key), _value(item)]
            continue
        out += [_flag(key), _value(value)]
    return out


def door_argv(door: roots_mod.DoorConfig) -> list[str]:
    """The door's own flags, in a fixed order. `api_key_file` stays a path."""
    out = ["--host", str(door.host), "--port", str(door.port)]
    if door.api_key_file:
        out += ["--api-key-file", str(Path(door.api_key_file).expanduser())]
    if door.threads is not None:
        out += ["--threads", str(door.threads)]
    if door.load_mode:
        out += ["--load-mode", str(door.load_mode)]
    if door.log_file:
        out += ["--log-file", str(Path(door.log_file).expanduser())]
    if not door.webui:
        out.append("--no-webui")
    out += list(door.args)
    return out


def launchd_log_path(door: roots_mod.DoorConfig) -> Path:
    """Where launchd's own capture goes: the door's log with `.launchd.log`
    for a suffix (`llm-server.log` → `llm-server.launchd.log`), or a file named
    for the label under DATA/logs when the door writes no log of its own."""
    if door.log_file:
        log = Path(door.log_file).expanduser()
        return log.with_suffix(".launchd.log")
    return cl.DATA_DIR / "logs" / f"{door.label}.launchd.log"


def build_argv(model_name: str, cfg: roots_mod.WeightsConfig | None = None) -> list[str]:
    """The whole command line for one model: binary, weights, model, door."""
    cfg = cfg or roots_mod.load_weights_config()
    binary = roots_mod.llama_server_path(cfg)
    if not binary:
        raise WeightsError(
            "no llama-server binary found — name one in config/weights.toml "
            "([weights].llama_server) or put it on PATH; the unit needs its "
            "absolute path")
    enrolled = load_enrolled(model_name)
    if enrolled is None:
        raise WeightsError(
            f"{model_name!r} has no [weights] table — nothing to render. "
            f"Enroll first: python -m hearth.weights enroll {model_name} --key ...")
    argv = [str(binary), "--model", str(enrolled.path)]
    if enrolled.mmproj:
        argv += ["--mmproj", str(enrolled.mmproj)]
    try:
        facts = cl._read_toml(model_toml(model_name))
    except cl.ConfigError as exc:
        raise WeightsError(str(exc)) from None
    server = facts.get("server")
    if server is not None and not isinstance(server, dict):
        raise WeightsError(f"{model_name}: [server] must be a table")
    argv += server_argv(server or {})
    argv += door_argv(cfg.door)
    return argv


# ── plist ────────────────────────────────────────────────────────────────────

def build_plist(label: str, argv: list[str], launchd_log: Path,
                working_dir: Path | None = None) -> str:
    """The unit as launchd wants it. Same shape as the hand-written one: run at
    load, come back after a non-zero exit, ten seconds between attempts."""
    unit: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": list(argv),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE_INTERVAL,
        "StandardOutPath": str(launchd_log),
        "StandardErrorPath": str(launchd_log),
        "WorkingDirectory": str(working_dir or cl.DATA_DIR),
    }
    ordered = {key: unit[key] for key in PLIST_KEY_ORDER if key in unit}
    return plistlib.dumps(ordered, sort_keys=False).decode("utf-8")


def render_dir() -> Path:
    return cl.DATA_DIR / RENDER_SUBDIR


def render_unit(model_name: str, cfg: roots_mod.WeightsConfig | None = None,
                write: bool = False) -> RenderedUnit:
    """One model's launchd unit. Writes nothing unless `write` is asked for,
    and then only into DATA/render/."""
    cfg = cfg or roots_mod.load_weights_config()
    argv = build_argv(model_name, cfg)
    label = cfg.door.label
    text = build_plist(label, argv, launchd_log_path(cfg.door))
    target = render_dir() / f"{label}.plist"
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return RenderedUnit(model_name=model_name, label=label, argv=argv,
                        plist_text=text, path=target)


# ── the equivalence check ────────────────────────────────────────────────────

def read_program_arguments(plist_path: Path) -> list[str]:
    """ProgramArguments out of a plist on disk. Nothing else is read."""
    plist_path = Path(plist_path)
    try:
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
    except OSError as exc:
        raise WeightsError(f"cannot read {plist_path} ({type(exc).__name__})") from None
    except Exception as exc:                      # plistlib's own parse errors
        raise WeightsError(f"{plist_path} is not a readable plist: {exc}") from None
    argv = data.get("ProgramArguments")
    if not isinstance(argv, list) or not argv:
        raise WeightsError(f"{plist_path} has no ProgramArguments array")
    return [str(a) for a in argv]


def _is_flag(token: str) -> bool:
    return token.startswith("-") and not _looks_numeric(token)


def _looks_numeric(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def flag_pairs(argv: list[str]) -> dict[str, str | None]:
    """argv → {long flag name: value or None}. argv[0] rides as `(program)`.

    A token that is not a flag follows the flag before it. A repeated flag
    keeps its last value — the door's own rule.
    """
    out: dict[str, str | None] = {}
    if argv:
        out["(program)"] = argv[0]
    i = 1
    while i < len(argv):
        token = argv[i]
        if not _is_flag(token):
            i += 1
            continue
        name = token.lstrip("-")
        name = FLAG_ALIASES.get(name, name)
        value = None
        if i + 1 < len(argv) and not _is_flag(argv[i + 1]):
            value = argv[i + 1]
            i += 1
        out[name] = value
        i += 1
    return out


def classify(flag: str) -> str:
    if flag in PLACEMENT_FLAGS:
        return "placement"
    if flag in DEPRECATED_LOAD_FLAGS:
        return "deprecated-form"
    return "real"


def _same_value(mine: str | None, yours: str | None) -> bool:
    """Equal, or two spellings of one file.

    A hand-written unit often reaches a weights file through a product's
    dot-directory symlink (`~/.lmstudio/models/...`) while enrollment stores the
    resolved real path (guard rail R2). Same file, same door: not a difference.
    Only a path that EXISTS is resolved, so a typo never quietly matches.
    """
    if mine == yours:
        return True
    if not mine or not yours:
        return False
    if not (mine.startswith("/") and yours.startswith("/")):
        return False
    try:
        a, b = Path(mine), Path(yours)
        if not (a.exists() and b.exists()):
            return False
        return a.resolve() == b.resolve()
    except OSError:
        return False


def diff_argv(rendered: list[str], live: list[str]) -> list[Difference]:
    """Every flag the two command lines disagree about, classified."""
    ours, theirs = flag_pairs(rendered), flag_pairs(live)
    out: list[Difference] = []
    for flag in sorted(set(ours) | set(theirs)):
        mine, yours = ours.get(flag, ...), theirs.get(flag, ...)
        if mine is not ... and yours is not ... and _same_value(mine, yours):
            continue
        if mine is ... and yours is ...:            # unreachable, kept honest
            continue
        out.append(Difference(
            flag=flag,
            rendered=None if mine is ... else mine,
            live=None if yours is ... else yours,
            kind=classify(flag),
            in_rendered=mine is not ...,
            in_live=yours is not ...))
    return out


def diff_unit(model_name: str, against: Path,
              cfg: roots_mod.WeightsConfig | None = None
              ) -> tuple[RenderedUnit, list[Difference]]:
    unit = render_unit(model_name, cfg)
    return unit, diff_argv(unit.argv, read_program_arguments(against))


# ── apply ────────────────────────────────────────────────────────────────────

def launch_agents_dir(home: Path | None = None) -> Path:
    """`~/Library/LaunchAgents` — where launchd reads per-user units.

    Two ways to point it somewhere else, because writing into the real one is
    the only irreversible thing this module does: an explicit `home`, or
    `HEARTH_LAUNCH_AGENTS` in the environment (what the tests use, so no test
    ever goes near the machine's own).
    """
    override = os.environ.get("HEARTH_LAUNCH_AGENTS", "").strip()
    if override and home is None:
        return Path(override).expanduser()
    base = Path(home) if home is not None else Path.home()
    return base / "Library" / "LaunchAgents"


def archive_name(existing: Path, when: datetime | None = None) -> Path:
    """`<label>.plist.prev-<date>`, seconds appended if that name is taken.
    An archive is never overwritten and a unit is never deleted."""
    when = when or datetime.now()
    candidate = existing.with_name(f"{existing.name}.prev-{when:%Y-%m-%d}")
    if candidate.exists():
        candidate = existing.with_name(f"{existing.name}.prev-{when:%Y-%m-%d-%H%M%S}")
    return candidate


def launchctl_lines(unit_path: Path, label: str) -> list[str]:
    """The two lines the operator runs. Printed, never executed — a running
    door belongs to whoever is talking through it."""
    return [f"launchctl bootout gui/$UID/{label}",
            f"launchctl bootstrap gui/$UID {unit_path}"]


def _serve_endpoint() -> tuple[str, str] | None:
    """(base URL, bearer) for the facade, or None when there is nothing to ask.

    The bearer is resolved from a PATH the way everything else in Hearth does
    (`SERVE_TOKEN` wins), held for one request, and never printed.
    """
    try:
        cfg = cl.load_serve_config()
    except cl.ConfigError:
        return None
    if not cfg:
        return None
    token = os.environ.get("SERVE_TOKEN", "").strip()
    if not token:
        src = Path(str(cfg.get("token_source") or "")).expanduser()
        if not src.is_absolute():
            src = cl.DATA_DIR / src
        try:
            token = src.read_text(encoding="utf-8").strip()
        except OSError:
            return None
    if not token:
        return None
    return f"http://127.0.0.1:{int(cfg.get('port') or 65001)}", token


def _get_json(url: str, bearer: str | None = None, timeout: float = 2.0):
    request = urllib.request.Request(url)
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def companion_guard(cfg: roots_mod.WeightsConfig | None = None) -> Guard:
    """Is a companion up? Process truth from the facade's /admin/state when it
    answers; otherwise the door's own /health and a plain warning.

    Replacing the unit under a live session is the one thing this command must
    not do quietly: the next turn would meet a door that is being restarted.
    """
    cfg = cfg or roots_mod.load_weights_config()
    endpoint = _serve_endpoint()
    if endpoint is not None:
        base, bearer = endpoint
        try:
            state = _get_json(f"{base}/admin/state", bearer)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            state = None
        if isinstance(state, dict):
            bot = state.get("bot") if isinstance(state.get("bot"), dict) else {}
            phase = str(bot.get("state") or "unknown")
            if phase in ("starting", "running"):
                return Guard(True, True,
                             f"a companion is {phase} — the next turn would meet a "
                             "door mid-restart. Stop it first (the launch page's "
                             "STOP, or POST /admin/bot/stop), then apply.")
            return Guard(False, True, f"no companion running (facade says {phase!r})")
    door = cfg.door
    try:
        _get_json(f"http://{door.host}:{door.port}/health")
        reachable = True
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        reachable = False
    if reachable:
        return Guard(False, False,
                     "the facade did not answer, so nothing here knows whether a "
                     f"companion is talking — and the door at {door.host}:{door.port} "
                     "IS up. Check that no session is live before you load the unit.")
    return Guard(False, False,
                 "the facade did not answer and the door is down — nothing appears "
                 "to be running, but this is a guess, not process truth.")


@dataclass(frozen=True)
class Applied:
    unit: RenderedUnit
    target: Path
    archived: Path | None
    lines: list[str] = field(default_factory=list)


def apply_unit(model_name: str, yes: bool = False, home: Path | None = None,
               agents_dir: Path | None = None,
               cfg: roots_mod.WeightsConfig | None = None) -> Applied:
    """Put the rendered unit where launchd reads it. Preview unless `yes`.

    Whatever is already at the target is ARCHIVED beside it first, never
    deleted, and launchctl is not run: the two lines come back for the operator
    to run when the moment is theirs to choose.
    """
    cfg = cfg or roots_mod.load_weights_config()
    unit = render_unit(model_name, cfg, write=yes)
    target = (Path(agents_dir) if agents_dir is not None
              else launch_agents_dir(home)) / f"{unit.label}.plist"
    lines = launchctl_lines(target, unit.label)
    if not yes:
        return Applied(unit=unit, target=target, archived=None, lines=lines)
    archived = None
    if target.exists():
        archived = archive_name(target)
        shutil.copy2(target, archived)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(unit.plist_text, encoding="utf-8")
    return Applied(unit=unit, target=target, archived=archived, lines=lines)
