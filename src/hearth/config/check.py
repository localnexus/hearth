"""check.py — validate every Hearth config file against the settings registry.

The operator-facing half of the settings registry (schema-driven settings,
step 1): discover every config file present on THIS install (data root first,
engine tree second — the same lookup the loaders use), validate each strictly
against its declared schema, and print a per-file verdict table.

    python -m hearth.config.check                  # validate all present files
    python -m hearth.config.check --emit-manual D  # also (re)generate the two
                                                   #   settings-reference pages in D
    python -m hearth.config.check --emit-json P    # also write the JSON Schema
                                                   #   bundle at P

Verdicts: ok · warn (unknown keys / out-of-range values, listed) · INVALID
(type/required violations — exit code 1) · absent (file not present; never an
error) · inert (a gate file whose table is missing/disabled — shape still
checked). Output names KEYS only, never values: config files can sit next to
secrets (serve.toml holds a token PATH), so nothing here ever echoes file
content back.

Strictness split (deliberate): the LOADERS warn on unknown keys and ranges
(a boot that works today must keep working); THIS command is where the full
schema binds — run it after hand-editing, before flipping a gate to enabled.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from hearth.config import config_loader as cl
from hearth.config import settings_registry as sr


# ── discovery (data root first, engine tree second — the loaders' own rule) ──

def _dedup(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        r = p.resolve()
        if r not in seen and r.is_file():
            seen.add(r)
            out.append(r)
    return out


def _both_roots(rel_glob: str) -> list[Path]:
    """Glob a pattern under DATA and ROOT (deduped; DATA first)."""
    return _dedup(
        [p for root in (cl.DATA_DIR, cl._ROOT) for p in sorted(root.glob(rel_glob))]
    )


def discover() -> list[tuple[str, Path]]:
    """(kind, path) for every registry-covered file present on this install."""
    found: list[tuple[str, Path]] = []

    def add(kind: str, paths: list[Path]) -> None:
        found.extend((kind, p) for p in paths)

    add("active", _dedup([cl.ACTIVE_TOML]))
    add("overrides", _dedup([cl.CONFIG_DIR / "overrides.toml"]))
    add("model", _both_roots("config/models/*/model.toml"))
    add("voice", _both_roots("characters/*/voices/*/voice.toml"))
    add("tts-baseline", _both_roots("config/tts/*/tts.toml"))
    add("vad", _both_roots("config/vad.toml"))
    add("serve", _dedup([cl.SERVE_TOML]))
    add("memory", _dedup([cl.MEMORY_TOML]))
    add("openclaw", _dedup([cl.OPENCLAW_TOML]))
    # Panel-written per-companion presets + their live mirrors (same shape).
    add("profile", _both_roots("characters/*/profile.toml")
        + _both_roots("characters/*/voices/*/profile.toml")
        + _both_roots("characters/*/overrides.toml")
        + _both_roots("characters/*/voices/*/overrides.toml"))
    return found


# ── validation of one file ────────────────────────────────────────────────────

def check_file(kind: str, path: Path) -> tuple[str, list[str], list[str]]:
    """→ (verdict, errors, warnings); verdict ∈ ok | warn | INVALID | inert."""
    entry = sr.REGISTRY[kind]
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        return "INVALID", [f"malformed TOML: {exc}"], []
    except OSError as exc:
        return "INVALID", [f"unreadable: {exc.__class__.__name__}"], []

    verdict_floor = "ok"
    if entry.top_key is not None:
        stray = [k for k in data if k != entry.top_key]
        table = data.get(entry.top_key)
        if not isinstance(table, dict):
            return "inert", [], [f"no [{entry.top_key}] table — file is inert"]
        if not table.get("enabled"):
            verdict_floor = "inert"  # gate off; shape still checked below
        data = table
        if stray:
            data = dict(data)  # never mutate the parsed original
        pre_warn = [f"unknown top-level section '{k}'" for k in stray]
    else:
        pre_warn = []

    errors, warnings = sr.strict_check(kind, data)
    warnings = pre_warn + warnings
    if errors:
        return "INVALID", errors, warnings
    if warnings:
        return ("warn" if verdict_floor == "ok" else verdict_floor), [], warnings
    return verdict_floor, [], []


# ── CLI ───────────────────────────────────────────────────────────────────────

def _rel(path: Path) -> str:
    for base, label in ((cl.DATA_DIR, "DATA"), (cl._ROOT, "ROOT")):
        try:
            return f"{label}/{path.relative_to(base.resolve())}"
        except ValueError:
            continue
    return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hearth.config.check",
                                 description=__doc__.split("\n", 1)[0])
    ap.add_argument("--emit-manual", metavar="DIR",
                    help="write the generated settings-reference pages (markdown) into DIR")
    ap.add_argument("--emit-json", metavar="PATH",
                    help="write the JSON Schema bundle (all file kinds) to PATH")
    args = ap.parse_args(argv)

    print(f"settings check — data root {cl.DATA_DIR}")
    print(f"                 engine tree {cl._ROOT}")
    bad = 0
    present: set[str] = set()
    for kind, path in discover():
        present.add(kind)
        verdict, errors, warnings = check_file(kind, path)
        tag = {"ok": "[ok]     ", "warn": "[warn]   ", "inert": "[inert]  ",
               "INVALID": "[INVALID]"}[verdict]
        print(f"{tag} {kind:<12} {_rel(path)}")
        for e in errors:
            print(f"           - {e}")
        for w in warnings:
            print(f"           ~ {w}")
        bad += 1 if verdict == "INVALID" else 0
    for kind in sr.REGISTRY:
        if kind not in present:
            print(f"[absent]  {kind:<12} ({sr.REGISTRY[kind].path})")

    if args.emit_manual:
        outdir = Path(args.emit_manual)
        outdir.mkdir(parents=True, exist_ok=True)
        for fname, text in sr.generate_manual_pages().items():
            (outdir / fname).write_text(text, encoding="utf-8")
            print(f"wrote {outdir / fname}")
    if args.emit_json:
        out = Path(args.emit_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sr.json_schema(), indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"wrote {out}")

    if bad:
        print(f"{bad} file(s) INVALID")
        return 1
    print("all present files pass strict validation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
