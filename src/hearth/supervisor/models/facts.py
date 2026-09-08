"""models/facts.py — the readings every route in this package shares.

Four kinds of fact, and one rule that governs all of them.

  STATE      what `enroll.check` found, boiled down to one word: `present`,
             `missing` (guard rail R3 — a file that went away is a reported
             state, never an exception and never a fall-back to somebody
             else's copy) or `changed` (same path, different bytes).
  FIT        the header estimate against this machine's budget, in the words
             `fit.verdict` uses. The budget costs a `sysctl` and, when the
             door binary is known, one `--list-devices` — so it is read once
             per daemon and kept.
  UNIT       the rendered launchd unit against the one already on the machine:
             `applied` (no real differences), `stale` (some), `unapplied` (no
             unit there at all). `unknown` when the render itself refuses.
  TARGETS    the model directories under the data folder — what a scan result
             can be enrolled INTO.

The rule: **a key path is never a fact this surface reports.** `[weights.door]`
carries `api_key_file` as a PATH; the renderer passes it to `--api-key-file`
and never opens it, and nothing here puts it in a response body either — the
argv and every difference row go through `redact_argv` / `redact_difference`
first, and the door view reports `"set"` or `"unset"`. A path is not a secret,
but the path to a secret is the one string that turns a read-only admin view
into a map of where to look.

Every function here is BLOCKING (files, headers, one subprocess deep inside
`hearth.weights.fit`). The routes call them through `asyncio.to_thread`.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from hearth.config import config_loader as cl

# The weights submodules, by NAME rather than by attribute. The package façade
# re-exports `enroll` as a FUNCTION, so `from hearth.weights import enroll`
# hands back the function and shadows the module of that name (the G1 shadowing
# note). `import_module` resolves the real submodule every time, and is the one
# spelling that cannot quietly become the wrong object as the façade grows.
enroll_mod = import_module("hearth.weights.enroll")
fit_mod = import_module("hearth.weights.fit")
header_mod = import_module("hearth.weights.header")
render_mod = import_module("hearth.weights.render")
roots_mod = import_module("hearth.weights.roots")

#: Flags whose VALUE is the path to key material. Their values never leave
#: this process — not in an argv, not in a difference row.
KEY_PATH_FLAGS = frozenset({"api-key-file", "api_key_file"})

#: What a redacted value reads as. Deliberately not a path shape.
HIDDEN = "(a key path — set)"


# ── the machine budget, read once ────────────────────────────────────────────

def machine_budget(cfg: roots_mod.WeightsConfig) -> fit_mod.Budget:
    """What one model may occupy here. Costs a `sysctl` and (when the door
    binary is known) one `--list-devices`; the caller keeps the answer for the
    daemon's life, because it is a fact about the machine, not about a model."""
    return fit_mod.machine_budget(roots_mod.llama_server_path(cfg))


def budget_json(budget: fit_mod.Budget) -> dict:
    return {"total_bytes": budget.total_bytes,
            "working_set_bytes": budget.working_set_bytes,
            "reserve_bytes": budget.reserve_bytes,
            "available_bytes": budget.available,
            "source": budget.source}


# ── fit ──────────────────────────────────────────────────────────────────────

def header_facts(declared: dict | None) -> header_mod.HeaderFacts | None:
    """`[weights.header]` (or a scan's header dict) back into HeaderFacts.

    A file that declared fields this build does not know about is not an
    error — it is an older or newer header, and the fields we DO know still
    make the estimate. Unknown keys are dropped rather than raising."""
    if not declared:
        return None
    known = header_mod.HeaderFacts.__dataclass_fields__
    return header_mod.HeaderFacts(**{k: v for k, v in declared.items() if k in known})


def fit_text(size_bytes: int, header: header_mod.HeaderFacts | None,
             budget: fit_mod.Budget, ctx: int | None = None,
             mmproj_bytes: int = 0) -> tuple[str, int]:
    """(verdict, the context it was judged at). "unknown" when nothing said."""
    at = ctx or (header.context_length if header and header.context_length else None) \
        or fit_mod.MIN_CTX
    stand_in = SimpleNamespace(header=header, size_bytes=int(size_bytes))
    proj = SimpleNamespace(size_bytes=int(mmproj_bytes)) if mmproj_bytes else None
    est = fit_mod.estimate(stand_in, at, proj)
    return fit_mod.verdict(est, budget, header), at


def model_ctx(model_name: str) -> int | None:
    """The context this model will actually be served at, if its config says.

    `[server].ctx-size` is what the door is GIVEN, so it beats what the file
    was trained for; `reliable_context` is the panel's ceiling and comes next.
    """
    try:
        facts = cl._read_toml(enroll_mod.model_toml(model_name))
    except (cl.ConfigError, OSError):
        return None
    server = facts.get("server")
    if isinstance(server, dict):
        for key in ("ctx-size", "ctx_size", "c"):
            value = server.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return int(value)
    value = facts.get("reliable_context")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def model_id(model_name: str) -> str | None:
    """The id this model's config advertises — what the door serves it under
    (`--alias`), and so what a residency listing has to match."""
    try:
        facts = cl._read_toml(enroll_mod.model_toml(model_name))
    except (cl.ConfigError, OSError):
        return None
    value = facts.get("id")
    return str(value) if value else None


# ── state (guard rail R3: missing is a state) ────────────────────────────────

#: Findings that mean the file is gone, and findings that mean it changed.
_GONE = ("weights missing", "shards missing", "projector missing")
_CHANGED = ("identity changed", "size changed")


def state_from(findings: list[enroll_mod.Finding]) -> tuple[str, str]:
    """(state, the line that explains it) — `present` | `missing` | `changed`.

    R3 in one function: a deleted file comes back as the word `missing` and
    the plain sentence `check` wrote, never as an exception and never as a
    quiet substitution of some other copy.
    """
    errors = [f for f in findings if f.level == "error"]
    for kind, marks in (("missing", _GONE), ("changed", _CHANGED)):
        for finding in errors:
            if any(mark in finding.text for mark in marks):
                return kind, finding.text
    if errors:
        return "changed", errors[0].text
    return "present", ""


# ── the unit on the machine ──────────────────────────────────────────────────

@dataclass(frozen=True)
class UnitState:
    state: str                       # applied | stale | unapplied | unknown
    counts: dict                     # real / placement / deprecated-form
    rows: list                       # redacted difference rows
    target: Path | None
    argv: list                       # redacted
    note: str = ""


def _difference_row(diff: render_mod.Difference) -> dict:
    """One diff row, as the card draws it — with key paths taken out."""
    hide = diff.flag in KEY_PATH_FLAGS
    return {"flag": diff.flag, "kind": diff.kind,
            "rendered": HIDDEN if (hide and diff.in_rendered) else diff.shown("rendered"),
            "live": HIDDEN if (hide and diff.in_live) else diff.shown("live")}


def redact_argv(argv: list[str]) -> list[str]:
    """The command line with every key-file VALUE replaced. The flag stays —
    that a key is configured is a fact worth showing; where it lives is not."""
    out: list[str] = []
    hide_next = False
    for token in argv:
        out.append(HIDDEN if hide_next else token)
        hide_next = token.lstrip("-") in KEY_PATH_FLAGS
    return out


def counts_of(differences: list[render_mod.Difference]) -> dict:
    return {kind: sum(1 for d in differences if d.kind == kind)
            for kind in ("real", "placement", "deprecated-form")}


def unit_state(model_name: str, cfg: roots_mod.WeightsConfig,
               write: bool = False) -> UnitState:
    """The rendered unit against the one launchd reads today.

    `write` renders into DATA/render/ as well — never into LaunchAgents, which
    only `apply` reaches, and only through the injectable helper.
    """
    target = render_mod.launch_agents_dir() / f"{cfg.door.label}.plist"
    try:
        unit = render_mod.render_unit(model_name, cfg, write=write)
    except (enroll_mod.WeightsError, cl.ConfigError) as exc:
        return UnitState("unknown", counts_of([]), [], target, [], str(exc))
    argv = redact_argv(unit.argv)
    if not target.is_file():
        return UnitState("unapplied", counts_of([]), [], target, argv,
                         "no unit at that path yet")
    try:
        live = render_mod.read_program_arguments(target)
    except enroll_mod.WeightsError as exc:
        return UnitState("unknown", counts_of([]), [], target, argv, str(exc))
    differences = render_mod.diff_argv(unit.argv, live)
    rows = [_difference_row(d) for d in differences]
    counts = counts_of(differences)
    return UnitState("stale" if counts["real"] else "applied", counts, rows,
                     target, argv)


# ── what a scan can be enrolled into ─────────────────────────────────────────

def targets() -> list[str]:
    """Model directories under the DATA folder, in name order.

    Only the data folder: enrollment writes there and nowhere else, so the
    shipped `example` in the engine tree is not on this list — it is what a
    NEW directory is copied from, not a directory to write into.
    """
    out: list[str] = []
    if not cl.MODELS_DIR.is_dir():
        return out
    for entry in sorted(p for p in cl.MODELS_DIR.iterdir() if p.is_dir()):
        if entry.name.startswith(".") or not (entry / "model.toml").is_file():
            continue
        out.append(entry.name)
    return out


def example_model_toml() -> Path:
    """The shipped model.toml a new model directory is copied from."""
    return cl.ROOT_CONFIG_DIR / "models" / "example" / "model.toml.example"
