"""python -m hearth.memory — inspect canonical records · rebuild a backend index.

Verbs (all default to the ACTIVE companion; --character overrides):

  records [--character c]
      List canonical memory records — METADATA + digest only, message content
      is never printed (session_store's SessionMeta discipline).

  rebuild [--character c]
      Decider 7 made operational: replay every canonical record, oldest first,
      through the configured backend's ``store``. This is how a newly adopted
      backend inherits the companion's whole archived history instead of
      starting amnesiac, and how the daily-use A/B stays fair (each contender
      indexes the same records).
"""

from __future__ import annotations

import argparse
import sys

from .backend import digest_record
from . import records as records_mod


def _resolve_character(value: str | None) -> str:
    if value:
        return value
    from hearth.config import config_loader

    return config_loader.load_active_selection()["character"]


def _cmd_records(character: str) -> int:
    count = 0
    for record in records_mod.iter_records(character, newest_first=True):
        count += 1
        when = (record.ended or record.started)[:16].replace("T", " ")
        name = f" “{record.name}”" if record.name else ""
        turns = sum(1 for m in record.messages if m.get("role") == "user")
        digest = digest_record(record)
        if len(digest) > 100:
            digest = digest[:99] + "…"
        print(f"  {when}  {record.session_id}{name}  ({turns} user turns)\n"
              f"      {digest}")
    print(f"{count} record(s) for {character!r}" if count else
          f"no memory records for {character!r}")
    return 0


def _cmd_rebuild(character: str) -> int:
    from hearth import memory as seam_mod

    seam = seam_mod.maybe_attach(character)
    if seam is None:
        print("memory is not enabled for this companion (config/memory.toml) — "
              "nothing to rebuild", file=sys.stderr)
        return 1
    total = 0
    failed = 0
    try:
        for record in records_mod.iter_records(character, newest_first=False):
            total += 1
            try:
                seam.backend.store(character, record)
            except Exception as exc:  # noqa: BLE001 — count, continue, report
                failed += 1
                print(f"  ! {record.session_id}: {type(exc).__name__}", file=sys.stderr)
    finally:
        seam.close()
    print(f"replayed {total - failed}/{total} record(s) into backend "
          f"{seam.backend.name!r} for {character!r}")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hearth.memory",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("records", "rebuild"):
        p = sub.add_parser(verb)
        p.add_argument("--character", default=None,
                       help="companion name (default: the active one)")
    args = parser.parse_args(argv)
    character = _resolve_character(args.character)
    if args.verb == "records":
        return _cmd_records(character)
    return _cmd_rebuild(character)


if __name__ == "__main__":
    raise SystemExit(main())
