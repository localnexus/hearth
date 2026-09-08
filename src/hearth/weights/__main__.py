"""python -m hearth.weights — roots, scanning, and enrollment.

Seven verbs. The reading ones only read; every one that writes asks twice.

  roots
      The directories Hearth will look in, where each came from, and whether it
      exists. Plus the landing folder's three role subfolders and the door
      binary this machine will validate `[server]` keys against.

  scan [--root NAME] [--ctx N] [--json]
      Everything found, one line each: display key, size, architecture, the
      context the file was trained for, its KV-head count, and whether it fits
      here. `dup` marks a file whose bytes also live somewhere else — this
      machine holds the incumbent twice, once as a plain file and once as an
      Ollama blob. Projectors are listed under the model they pair with.

  list [--json]
      What is enrolled: every model directory carrying a [weights] table, and
      whether the file it points at is still there.

  enroll <model> (--path P | --key KEY) [--mmproj auto|none|PATH] [--yes]
      Bind one set of weights to one model directory. Without --yes it shows
      what it would write and writes nothing.

  unenroll <model> [--yes]
      Drop the reference. The weights file is never touched.

  render <model> [--diff] [--against PLIST]
      The launchd unit this model's config comes to: the whole argv printed,
      and the plist written to DATA/render/<label>.plist — never into
      ~/Library/LaunchAgents. With --diff it is compared instead against the
      unit already on the machine (default ~/Library/LaunchAgents/<label>.plist)
      and every difference is classified: `placement` (what --fit manages),
      `deprecated-form` (--mlock / --no-direct-io against today's --load-mode),
      or `real`. Exit 1 if anything is real.

  apply <model> [--yes]
      Put that unit where launchd reads it. Without --yes it only shows what it
      would do. With --yes the file already there is archived beside it first
      (.prev-<date>, never deleted) and the two launchctl lines are printed for
      you to run — this command never runs launchctl, and it refuses while a
      companion is talking.

  check [<model>] [--llama-server P]
      Is everything still where it was: present, same size, same file, shards
      intact, projector intact, and every `[server]` key a flag this door
      actually accepts. Exit 1 if anything is wrong.

Exit codes: 0 fine · 1 something to act on (a refusal, a preview, a failed
check) · 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from hearth.config import config_loader as cl

from . import fit as fit_mod
from . import render as render_mod
from . import roots as roots_mod
from . import scan as scan_mod
# Explicit, because the package façade re-exports `enroll` as a FUNCTION: the
# submodule of that name is shadowed on the package and must not be reached for.
from .enroll import (
    WeightsError,
    check as check_model,
    data_model_toml,
    enroll as enroll_weights,
    enrolled_models,
    load_enrolled,
    unenroll as unenroll_model,
)

GB = 1000 ** 3


def _gb(n: int) -> str:
    return f"{n / GB:.1f} GB"


def _candidate_json(c) -> dict:
    return {
        "display_key": c.display_key, "path": str(c.path), "root": c.root_name,
        "layout": c.layout, "kind": c.kind, "size_bytes": c.size_bytes,
        "shards": [str(s) for s in c.shards], "identity": c.identity,
        "duplicates": [str(p) for p in c.duplicates],
        "header": asdict(c.header) if c.header else None,
        "header_error": c.header_error,
        "mmproj": [str(m.path) for m in c.mmproj_candidates],
    }


# ── roots ────────────────────────────────────────────────────────────────────

def _cmd_roots() -> int:
    cfg = roots_mod.load_weights_config()
    print(f"weights config — {cfg.source if cfg.source else '(none; defaults)'}")
    resolved = roots_mod.resolve_roots(cfg)
    if not resolved:
        print("  no roots — name your own in config/weights.toml ([weights].roots)")
    for root in resolved:
        print(f"  {root.name:<16} {root.kind:<9} {root.path}")
    if cfg.product_dirs:
        shown = {r.kind for r in resolved}
        for name, raw in roots_mod.PRODUCT_POINTERS:
            if name in shown:
                continue
            real = Path(raw).expanduser()
            why = "absent" if not real.exists() else "already inside one of your roots"
            print(f"  {name:<16} pointer   {real}  ({why})")
    base = roots_mod.landing_dir(cfg)
    print(f"landing folder — {base if base else '(none; no roots declared)'}")
    for role, path in roots_mod.role_dirs(cfg):
        state = "present" if path.is_dir() else "absent"
        note = "" if role == "llm" else "  (not scanned yet)"
        print(f"  {role:<4} {state:<8} {path}{note}")
    door = roots_mod.llama_server_path(cfg)
    print(f"door binary — {door if door else '(not found)'}")
    return 0


# ── scan ─────────────────────────────────────────────────────────────────────

def _cmd_scan(root_name: str | None, ctx: int | None, as_json: bool) -> int:
    cfg = roots_mod.load_weights_config()
    resolved = roots_mod.resolve_roots(cfg)
    if root_name:
        resolved = [r for r in resolved if r.name == root_name]
        if not resolved:
            print(f"no root named {root_name!r} — `roots` lists them", file=sys.stderr)
            return 1
    found = scan_mod.scan_all(resolved)
    if as_json:
        print(json.dumps([_candidate_json(c) for c in found], indent=1))
        return 0

    budget = fit_mod.machine_budget(roots_mod.llama_server_path(cfg))
    print(f"budget — {_gb(budget.available)} available "
          f"({_gb(budget.working_set_bytes)} working set − {_gb(budget.reserve_bytes)} "
          f"for speech; {budget.source})")
    models = [c for c in found if c.kind == "model"]
    elsewhere = [c for c in found if c.kind == "elsewhere"]
    if not models and not elsewhere:
        print("no weights found")
        return 0
    for cand in models:
        head = cand.header
        arch = (head.architecture if head else None) or "?"
        trained = head.context_length if head and head.context_length else None
        kv_heads = head.head_count_kv if head else None
        at = ctx or trained or fit_mod.MIN_CTX
        est = fit_mod.estimate(cand, at, cand.mmproj_candidates[0]
                               if cand.mmproj_candidates else None)
        verdict = fit_mod.verdict(est, budget, head)
        dup = "  dup" if cand.duplicates else ""
        shard = f"  {len(cand.shards)} shards" if cand.shards else ""
        print(f"  {cand.display_key}")
        print(f"      {_gb(cand.size_bytes):>9}  {arch:<12} ctx_train "
              f"{trained if trained else '?':<8} kv-heads {kv_heads if kv_heads else '?':<4} "
              f"{verdict} @ {at}{dup}{shard}")
        if cand.header_error:
            print(f"      header unreadable: {cand.header_error}")
        for proj in cand.mmproj_candidates:
            print(f"      mmproj  {_gb(proj.size_bytes):>9}  {proj.display_key}")
    for cand in elsewhere:
        print(f"  {cand.display_key}")
        print(f"      not followed: {cand.header_error}")

    print(f"{len(models)} candidate(s) in {len(resolved)} root(s)")
    return 0


# ── list ─────────────────────────────────────────────────────────────────────

def _cmd_list(as_json: bool) -> int:
    rows = []
    for name in enrolled_models():
        got = load_enrolled(name)
        if got is None:
            continue
        rows.append({
            "model": name, "path": str(got.path),
            "state": "present" if got.path.is_file() else "missing",
            "size_bytes": got.size_bytes,
            "architecture": got.header.get("architecture"),
            "display_key": got.display_key,
            "mmproj": str(got.mmproj) if got.mmproj else None,
        })
    if as_json:
        print(json.dumps(rows, indent=1))
        return 0
    if not rows:
        print(f"nothing enrolled under {cl.MODELS_DIR}")
        return 0
    for row in rows:
        print(f"  {row['model']:<20} {row['state']:<8} {_gb(row['size_bytes']):>9}  "
              f"{row['architecture'] or '?':<12} {row['path']}")
    print(f"{len(rows)} enrolled model(s)")
    return 0


# ── enroll ───────────────────────────────────────────────────────────────────

def _from_path(raw: str):
    """A candidate for one file, read from ITS directory only (no full scan)."""
    path = Path(raw).expanduser()
    if not path.is_file():
        return None, f"no such file: {path}"
    real = Path(path.resolve())
    root = roots_mod.Root(real.parent.name or "path", real.parent, "user")
    for cand in scan_mod.scan_dir(real.parent, root):
        if cand.path == real or real in cand.shards:
            return cand, None
    return None, f"{path} is not a GGUF file this reader recognises"


def _from_key(key: str):
    found = scan_mod.scan_all()
    models = [c for c in found if c.kind == "model"]
    exact = [c for c in models if c.display_key == key]
    if len(exact) == 1:
        return exact[0], None
    suffix = [c for c in models if c.display_key.endswith(key)]
    if len(suffix) == 1:
        return suffix[0], None
    if not exact and not suffix:
        return None, f"no candidate matches {key!r} — `scan` lists the display keys"
    matches = exact or suffix
    names = ", ".join(sorted(c.display_key for c in matches)[:5])
    return None, f"{key!r} matches {len(matches)} candidates ({names}) — be more specific"


def _resolve_mmproj(cand, choice: str):
    if choice == "none":
        return None, None
    if choice != "auto":
        proj, err = _from_path(choice)
        return proj, err
    options = cand.mmproj_candidates
    if not options:
        return None, None
    if len(options) > 1:
        names = ", ".join(p.display_key for p in options)
        return None, (f"--mmproj auto is ambiguous: {len(options)} projectors beside "
                      f"this model ({names}) — name one with --mmproj PATH, or "
                      "--mmproj none")
    return options[0], None


def _cmd_enroll(model: str, path: str | None, key: str | None,
                mmproj_choice: str, yes: bool) -> int:
    cand, err = _from_path(path) if path else _from_key(key or "")
    if err is not None:
        print(err, file=sys.stderr)
        return 1
    proj, err = _resolve_mmproj(cand, mmproj_choice)
    if err is not None:
        print(err, file=sys.stderr)
        return 1

    try:
        facts = cl.load_model(model)
    except cl.ConfigError:
        facts = {}
    head = cand.header
    at = facts.get("reliable_context") or (head.context_length if head else None) \
        or fit_mod.MIN_CTX
    cfg = roots_mod.load_weights_config()
    budget = fit_mod.machine_budget(roots_mod.llama_server_path(cfg))
    est = fit_mod.estimate(cand, at, proj)

    print(f"enroll {model!r}")
    print(f"  weights     {cand.path}")
    print(f"  display key {cand.display_key}  ({cand.layout}, root {cand.root_name})")
    print(f"  size        {_gb(cand.size_bytes)}  identity {cand.identity}")
    if cand.shards:
        print(f"  shards      {len(cand.shards)}")
    print(f"  projector   {proj.path if proj else '(none)'}")
    if head:
        print(f"  header      {head.architecture} · {head.block_count} blocks "
              f"(+{head.nextn_predict_layers} mtp) · ctx_train {head.context_length} "
              f"· {head.head_count_kv} kv-heads · key/value {head.key_length}/"
              f"{head.value_length} · interval {head.full_attention_interval} "
              f"· experts {head.expert_count}")
    if cand.header_error:
        print(f"  header      unreadable: {cand.header_error}")
    print(f"  fit @ {at}  weights {_gb(est.weights_bytes)} + mmproj "
          f"{_gb(est.mmproj_bytes)} + kv {_gb(est.kv_bytes)} + margin "
          f"{_gb(est.margin_bytes)} = {_gb(est.total)} vs {_gb(budget.available)} "
          f"available → {fit_mod.verdict(est, budget, head)}")
    for other in cand.duplicates:
        print(f"  duplicate   the same bytes also at {other}")

    if not yes:
        print("nothing written — re-run with --yes to write", file=sys.stderr)
        return 1
    try:
        written = enroll_weights(model, cand, proj)
    except WeightsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"wrote {written}")
    return 0


def _cmd_unenroll(model: str, yes: bool) -> int:
    got = load_enrolled(model)
    target = data_model_toml(model)
    if got is None or not target.is_file():
        print(f"{model!r} has no [weights] table under {cl.MODELS_DIR}", file=sys.stderr)
        return 1
    print(f"unenroll {model!r} — removes the reference to {got.path}")
    print("  the weights file itself is never touched")
    if not yes:
        print("nothing written — re-run with --yes to write", file=sys.stderr)
        return 1
    unenroll_model(model)
    print(f"wrote {target}")
    return 0


# ── render ───────────────────────────────────────────────────────────────────

def _print_argv(argv: list[str]) -> None:
    print(f"  {argv[0]}")
    i = 1
    while i < len(argv):
        token = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if nxt is not None and not nxt.startswith("-"):
            print(f"    {token} {nxt}")
            i += 2
        else:
            print(f"    {token}")
            i += 1


def _cmd_render(model: str, diff: bool, against: str | None) -> int:
    try:
        cfg = roots_mod.load_weights_config()
        if not diff:
            unit = render_mod.render_unit(model, cfg, write=True)
            print(f"unit for {model!r} — label {unit.label}")
            _print_argv(unit.argv)
            print(f"wrote {unit.path}")
            print("  not loaded: `apply` puts it where launchd reads it")
            return 0
        target = Path(against).expanduser() if against else (
            render_mod.launch_agents_dir() / f"{cfg.door.label}.plist")
        unit, differences = render_mod.diff_unit(model, target, cfg)
    except (WeightsError, cl.ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"rendered {model!r} vs {target}")
    if not differences:
        print("  identical — every flag and value matches")
        return 0
    width = max(len(d.flag) for d in differences)
    for d in sorted(differences, key=lambda d: (d.kind != "real", d.flag)):
        print(f"  {d.kind:<15} {d.flag:<{width}}  rendered {d.shown('rendered')}"
              f"  ·  on disk {d.shown('live')}")
    real = [d for d in differences if d.is_real]
    counts = {kind: sum(1 for d in differences if d.kind == kind)
              for kind in ("real", "placement", "deprecated-form")}
    print(f"{counts['real']} real · {counts['placement']} placement "
          f"(--fit manages these) · {counts['deprecated-form']} deprecated-form "
          "(--load-mode replaces --mlock/--no-direct-io)")
    return 1 if real else 0


# ── apply ────────────────────────────────────────────────────────────────────

def _cmd_apply(model: str, yes: bool) -> int:
    try:
        cfg = roots_mod.load_weights_config()
        guard = render_mod.companion_guard(cfg)
        if guard.blocked:
            print(f"refused — {guard.text}", file=sys.stderr)
            return 1
        done = render_mod.apply_unit(model, yes=yes, cfg=cfg)
    except (WeightsError, cl.ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"apply {model!r} → {done.target}")
    _print_argv(done.unit.argv)
    if not guard.certain:
        print(f"  note: {guard.text}")
    if not yes:
        print(f"  would archive the file already there as "
              f"{render_mod.archive_name(done.target).name}"
              if done.target.exists() else "  nothing is there today — a fresh unit")
        print("nothing written — re-run with --yes to write", file=sys.stderr)
        return 1
    if done.archived is not None:
        print(f"  archived the previous unit as {done.archived}")
    print(f"  wrote {done.target}  (and {done.unit.path})")
    print("load it yourself, when the moment is yours to choose:")
    for line in done.lines:
        print(f"    {line}")
    return 0


# ── check ────────────────────────────────────────────────────────────────────

def _cmd_check(model: str | None, llama_server: str | None) -> int:
    door = llama_server or roots_mod.llama_server_path()
    names = [model] if model else enrolled_models()
    if not names:
        print(f"nothing enrolled under {cl.MODELS_DIR}")
        return 0
    bad = 0
    for name in names:
        for finding in check_model(name, door):
            tag = {"ok": "[ok]   ", "warn": "[warn] ", "error": "[ERROR]"}[finding.level]
            print(f"{tag} {finding.text}")
            bad += 1 if finding.level == "error" else 0
    return 1 if bad else 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hearth.weights",
                                     description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="verb", required=True)

    sub.add_parser("roots", help="the directories Hearth looks in")

    p_scan = sub.add_parser("scan", help="what is on disk, and whether it fits")
    p_scan.add_argument("--root", help="only this root (see `roots`)")
    p_scan.add_argument("--ctx", type=int, help="judge fit at this context length")
    p_scan.add_argument("--json", action="store_true")

    p_list = sub.add_parser("list", help="what is enrolled")
    p_list.add_argument("--json", action="store_true")

    p_enroll = sub.add_parser("enroll", help="bind weights to a model directory")
    p_enroll.add_argument("model")
    src = p_enroll.add_mutually_exclusive_group(required=True)
    src.add_argument("--path", help="the GGUF file itself")
    src.add_argument("--key", help="a scan display key, or a unique suffix of one")
    p_enroll.add_argument("--mmproj", default="auto",
                          help="auto (the one beside it) | none | a path")
    p_enroll.add_argument("--yes", action="store_true")

    p_un = sub.add_parser("unenroll", help="drop the reference (never the file)")
    p_un.add_argument("model")
    p_un.add_argument("--yes", action="store_true")

    p_render = sub.add_parser("render", help="the launchd unit this config comes to")
    p_render.add_argument("model")
    p_render.add_argument("--diff", action="store_true",
                          help="compare against the unit already on the machine")
    p_render.add_argument("--against", help="the plist to compare against "
                                            "(default ~/Library/LaunchAgents/<label>.plist)")

    p_apply = sub.add_parser("apply", help="put the rendered unit where launchd reads it")
    p_apply.add_argument("model")
    p_apply.add_argument("--yes", action="store_true")

    p_check = sub.add_parser("check", help="is everything still where it was")
    p_check.add_argument("model", nargs="?")
    p_check.add_argument("--llama-server", dest="llama_server",
                         help="the door binary to validate [server] keys against")

    args = parser.parse_args(argv)
    if args.verb == "roots":
        return _cmd_roots()
    if args.verb == "scan":
        return _cmd_scan(args.root, args.ctx, args.json)
    if args.verb == "list":
        return _cmd_list(args.json)
    if args.verb == "enroll":
        return _cmd_enroll(args.model, args.path, args.key, args.mmproj, args.yes)
    if args.verb == "unenroll":
        return _cmd_unenroll(args.model, args.yes)
    if args.verb == "render":
        return _cmd_render(args.model, args.diff, args.against)
    if args.verb == "apply":
        return _cmd_apply(args.model, args.yes)
    return _cmd_check(args.model, args.llama_server)


if __name__ == "__main__":
    sys.exit(main())
