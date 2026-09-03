"""python -m hearth.memory — inspect, rebuild, and curate the memory substrate.

Verbs (all default to the ACTIVE companion; --character overrides):

  records [--character c]
      List canonical memory records — METADATA + digest only, message content
      is never printed (session_store's SessionMeta discipline).

  rebuild [--character c] [--clean --yes]
      Replay every canonical record, oldest first, through the configured
      backend's ``store``. This is how a newly adopted backend inherits the
      companion's whole archived history instead of starting amnesiac, and
      how the daily-use A/B stays fair (each contender indexes the same
      records). ``--clean`` wipes the backend's index FIRST (confirm with
      --yes): the recovery path after forgetting pre-keyed content, and the
      one-time migration that turns an existing bank session-keyed.

  forget --session <id> [--character c] [--yes]
      Record-level curation: delete one session's canonical record AND its
      facts from the backend index (keyed backends cascade-delete; the floor
      needs only the record gone). Prints the record's digest first and
      requires --yes — forgetting the wrong session is the failure mode.
      True-delete by design: an archive step would retain what the user
      asked gone.

  fork --as <name> --until <when> [--character c] [--include-sessions] [--yes]
      Branch the companion's memory track at a juncture: a new character
      (persona as it stands today + voices + theme) whose records/ holds the
      shared history up to <when>, enrolled at the source's tier, then
      replayed into a non-floor backend. Records are selected by METADATA
      (ended/started), never filename; the intent slot is never copied; held
      transcripts come along only with --include-sessions. Preview without
      --yes. Full story + caveats: docs/memory.md, "Forking the track at a
      juncture".
"""

from __future__ import annotations

import argparse
import json
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


def _cmd_rebuild(character: str, clean: bool = False, yes: bool = False) -> int:
    from hearth import memory as seam_mod

    seam = seam_mod.maybe_attach(character)
    if seam is None:
        print("memory is not enabled for this companion (config/memory.toml) — "
              "nothing to rebuild", file=sys.stderr)
        return 1
    total = 0
    failed = 0
    try:
        if clean and not yes:
            count = sum(1 for _ in records_mod.iter_records(character))
            print(f"--clean WIPES backend {seam.backend.name!r} for {character!r}, "
                  f"then replays the {count} surviving record(s) — re-run with "
                  "--yes to confirm", file=sys.stderr)
            return 1
        if clean:
            try:
                seam.backend.clear(character)
            except Exception as exc:  # noqa: BLE001 — a failed wipe must not half-replay
                print(f"clear failed ({type(exc).__name__}) — nothing replayed",
                      file=sys.stderr)
                return 1
            print(f"cleared backend {seam.backend.name!r} for {character!r}")
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


def _cmd_forget(character: str, session_id: str, yes: bool) -> int:
    path = records_mod.records_dir(character) / f"{session_id}.json"
    if not path.is_file():
        print(f"no memory record {session_id!r} for {character!r} — "
              "`records` lists what exists", file=sys.stderr)
        return 1
    try:
        record = records_mod.load_record(path)
        when = (record.ended or record.started)[:16].replace("T", " ")
        name = f" “{record.name}”" if record.name else ""
        digest = digest_record(record)
        print(f"  {when}  {record.session_id}{name}\n      {digest}")
    except (ValueError, OSError, json.JSONDecodeError):
        print(f"  {session_id}  (malformed record — no digest available)")
    if not yes:
        print("\nforget deletes this record AND the session's facts from the "
              "memory index, permanently — re-run with --yes to confirm",
              file=sys.stderr)
        return 1

    # Backend first, record second: if the index can't be updated the record
    # stays put, so the verb is safely re-runnable — never half-forgotten.
    from hearth import memory as seam_mod

    seam = seam_mod.maybe_attach(character)
    excised = None
    if seam is not None:
        try:
            excised = seam.backend.forget(character, session_id)
        except Exception as exc:  # noqa: BLE001 — report and keep the record
            print(f"backend forget failed ({type(exc).__name__}) — record kept, "
                  "nothing deleted", file=sys.stderr)
            return 1
        finally:
            seam.close()
    path.unlink()  # true-delete (signed D3): an archive step would retain what was asked gone
    if seam is None:
        print(f"record {session_id} deleted (memory not enabled — no index to update)")
    elif excised:
        print(f"forgot session {session_id}: record deleted, facts excised from "
              f"backend {seam.backend.name!r}")
    else:
        print(f"record {session_id} deleted — but backend {seam.backend.name!r} "
              "holds facts it cannot attribute to this session (stored before "
              "keyed retain): run `rebuild --clean` to excise them", file=sys.stderr)
        return 1
    return 0


def _cmd_fork(character: str, target: str, until: str,
              include_sessions: bool, yes: bool) -> int:
    from . import fork as fork_mod

    try:
        plan = fork_mod.plan(character, target, until,
                             include_sessions=include_sessions)
    except fork_mod.ForkError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"fork {character!r} → {target!r} at juncture {plan.cutoff}")
    if plan.records:
        print(f"  shared history — {len(plan.records)} record(s) copy over:")
        for _path, record in plan.records:
            when = (record.ended or record.started)[:16].replace("T", " ")
            name = f" “{record.name}”" if record.name else ""
            print(f"    {when}  {record.session_id}{name}")
    else:
        print("  shared history — NO records at or before the juncture "
              "(the fork starts amnesiac)")
    if plan.left_behind:
        print(f"  {plan.left_behind} record(s) after the juncture stay with "
              f"{character!r}")
    if plan.undated:
        print(f"  {plan.undated} undated record(s) cannot be placed against a "
              "juncture — they stay behind")
    voices = ", ".join(plan.voices) if plan.voices else "none"
    print(f"  identity — {len(plan.identity)} file(s): personas + voices "
          f"({voices}) + theme, persona AS IT STANDS TODAY (edit after the "
          "fork if the juncture's differed)")
    print(f"  sessions — {len(plan.sessions)} transcript(s) copy over"
          if include_sessions else
          f"  sessions — stay with {character!r} (--include-sessions copies "
          "those started at or before the juncture)")
    print("  intent slot — never copied (it belongs to the source track)")
    print("  memory tier — " + (f"{plan.tier!r} (the source's effective tier)"
                                if plan.tier is not None else
                                "memory disabled, no enrollment"))
    if not yes:
        print("\nnothing written — re-run with --yes to create the fork",
              file=sys.stderr)
        return 1

    try:
        result = fork_mod.execute(plan)
    except fork_mod.ForkError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — rolled back in execute
        print(f"fork failed ({type(exc).__name__}) — nothing was kept",
              file=sys.stderr)
        return 1
    print(f"\nforked: characters/{target}/ created, "
          f"{result['records']} record(s) restamped\n  {result['memory']}")
    if plan.tier in (None, "floor", "none"):
        print("no backend replay needed — "
              + ("the floor reads record files directly" if plan.tier == "floor"
                 else "no indexed backend for this tier"))
        return 0
    if not result["enrolled"]:
        print("backend replay SKIPPED — enroll by hand, then run "
              f"`rebuild --character {target}`", file=sys.stderr)
        return 1
    rc = _cmd_rebuild(target)
    if rc != 0:
        print(f"the fork stands; re-run `rebuild --character {target}` to "
              "finish the replay", file=sys.stderr)
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hearth.memory",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    p_records = sub.add_parser("records", help="list records (metadata + digest only)")
    p_rebuild = sub.add_parser("rebuild", help="replay records into the backend index")
    p_rebuild.add_argument("--clean", action="store_true",
                           help="wipe the backend index first (needs --yes)")
    p_rebuild.add_argument("--yes", action="store_true",
                           help="confirm the --clean wipe")
    p_forget = sub.add_parser("forget",
                              help="delete one session's record + indexed facts")
    p_forget.add_argument("--session", required=True, metavar="ID",
                          help="session id (as `records` lists it)")
    p_forget.add_argument("--yes", action="store_true",
                          help="confirm the deletion (without it: preview only)")
    p_fork = sub.add_parser("fork",
                            help="branch the memory track at a juncture")
    p_fork.add_argument("--as", required=True, metavar="NAME", dest="fork_as",
                        help="the fork's character name (must not exist)")
    p_fork.add_argument("--until", required=True, metavar="WHEN",
                        help="the juncture, inclusive (ISO date or timestamp)")
    p_fork.add_argument("--include-sessions", action="store_true",
                        help="also copy transcripts started at or before the juncture")
    p_fork.add_argument("--yes", action="store_true",
                        help="confirm the fork (without it: preview only)")
    for p in (p_records, p_rebuild, p_forget, p_fork):
        p.add_argument("--character", default=None,
                       help="companion name (default: the active one)")
    args = parser.parse_args(argv)
    character = _resolve_character(args.character)
    if args.verb == "records":
        return _cmd_records(character)
    if args.verb == "forget":
        return _cmd_forget(character, args.session, args.yes)
    if args.verb == "fork":
        return _cmd_fork(character, args.fork_as, args.until,
                         args.include_sessions, args.yes)
    return _cmd_rebuild(character, clean=args.clean, yes=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
