"""fork.py — branch a companion's memory track at a juncture (the fork verb).

Sanctioned by the records-as-substrate invariant (docs/memory.md): canonical
per-session records are the truth and every backend is a derived, disposable
index rebuilt by replaying them — so a fork is simply a new character whose
records/ holds a copy of the shared history up to the juncture. Banks key on
companion name, so the fork's index is isolated by construction and recall
never crosses tracks. From the fork on, both branches share the pre-fork past
and diverge freely.

What forks (and what doesn't):

  identity   — persona.md + persona.<variant>.md siblings, every voices/
               bundle, and theme/ if present, each resolved DATA-first (the
               startup lookup rule). The persona is copied AS IT STANDS TODAY —
               personas evolve in place with no versioning, so reconstructing
               "as of the juncture" is the operator's edit after the fork,
               not the verb's guess.
  memory     — record files whose ``ended`` (fallback ``started``) is at or
               before the juncture, selected by METADATA never filename (names
               are not uniformly dated). Each copy is restamped
               companion=<fork> and carries a ``forked_from`` provenance key;
               unknown keys survive the rewrite (raw JSON, not the dataclass).
  enrollment — the fork joins [memory.companions] at the source's EFFECTIVE
               tier; the CLI then replays a non-floor backend (rebuild).
  sessions   — held/saved transcripts stay with the source unless
               --include-sessions (both-branch resumability is the exception,
               not the rule); when copied, selection is by SessionMeta
               ``started``, never filename.
  intent     — never: the consume-once slot belongs to the track that stated it.

Preview-then---yes like forget: without --yes the same call reports the full
plan and touches nothing. A failure mid-copy rolls the new character dir back.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import records as records_mod

_DATE_LEN = len("2026-01-01")


class ForkError(Exception):
    """A plan-time validation failure, worded for the operator."""


@dataclass
class ForkPlan:
    source: str
    target: str
    cutoff: str                                   # normalized ISO juncture (inclusive)
    records: list = field(default_factory=list)   # [(src Path, SessionRecord)]
    left_behind: int = 0                          # records after the juncture
    undated: int = 0                              # records with no timestamp (never copied)
    identity: list = field(default_factory=list)  # [(src Path, rel dest str)]
    voices: list = field(default_factory=list)    # voice tags (verification list)
    sessions: list = field(default_factory=list)  # [(src Path, rel dest str)]
    tier: str | None = None                       # effective backend, None = memory disabled


def _normalize_cutoff(until: str) -> str:
    """A bare date is inclusive of its whole day; otherwise the ISO timestamp
    stands as given. Validated by fromisoformat — records compare as strings,
    so the cutoff must actually be ISO-shaped."""
    until = (until or "").strip()
    try:
        _dt.datetime.fromisoformat(until)
    except ValueError as exc:
        raise ForkError(
            f"--until {until!r} is not an ISO date/timestamp "
            "(e.g. 2026-08-30 or 2026-08-30T16:00)") from exc
    if len(until) == _DATE_LEN:
        return until + "T23:59:59.999999"
    return until


def plan(source: str, target: str, until: str,
         include_sessions: bool = False) -> ForkPlan:
    """Everything validated and enumerated; nothing touched."""
    from hearth.config import config_loader

    for label, name in (("source", source), ("fork", target)):
        if not config_loader._NAME_RE.match(name or "") or name.startswith("."):
            raise ForkError(f"invalid {label} character name: {name!r}")
    if source == target:
        raise ForkError("a fork needs its own name")
    if not config_loader.persona_path(source).is_file():
        raise ForkError(f"unknown character {source!r} (no persona.md in either root)")
    for root in (config_loader._DATA, config_loader._ROOT):
        if (root / "characters" / target).exists():
            raise ForkError(f"character {target!r} already exists — fork is "
                            "create-only (pick a fresh name)")
    p = ForkPlan(source=source, target=target, cutoff=_normalize_cutoff(until))

    # Records: selected by metadata, never filename. Undated records cannot be
    # placed relative to a juncture, so they stay behind — counted, named.
    src_records = records_mod.records_dir(source)
    if src_records.is_dir():
        for path in sorted(src_records.glob("*.json")):
            try:
                record = records_mod.load_record(path)
            except (ValueError, OSError, json.JSONDecodeError):
                continue  # iter_records' discipline: a corrupt record costs itself only
            when = record.ended or record.started
            if not when:
                p.undated += 1
            elif when <= p.cutoff:
                p.records.append((path, record))
            else:
                p.left_behind += 1
    p.records.sort(key=lambda pair: (pair[1].ended or pair[1].session_id))

    # Identity: personas (default + variants, DATA shadows ROOT per name),
    # every voice bundle (per-voice lookup), theme/ if either root has one.
    personas: dict[str, Path] = {}
    for root in (config_loader._DATA, config_loader._ROOT):
        cdir = root / "characters" / source
        for f in sorted(cdir.glob("persona*.md")):
            personas.setdefault(f.name, f)
    for fname, src_path in sorted(personas.items()):
        p.identity.append((src_path, fname))
    for tag in config_loader.list_voices(source):
        vdir = config_loader.voice_dir(source, tag)
        for f in sorted(vdir.iterdir()):
            if f.is_file():
                p.identity.append((f, f"voices/{tag}/{f.name}"))
        p.voices.append(tag)
    for root in (config_loader._DATA, config_loader._ROOT):
        theme = root / "characters" / source / "theme"
        if theme.is_dir():
            for f in sorted(theme.rglob("*")):
                if f.is_file():
                    p.identity.append((f, f"theme/{f.relative_to(theme)}"))
            break  # DATA-first, whole-tree — the two roots don't merge

    # Sessions: opt-in, metadata-selected (started ≤ juncture).
    if include_sessions:
        from hearth.session import session_store

        sdir = config_loader.companion_state_dir(source, "sessions")
        for meta in session_store.list_sessions(sdir):
            if (meta.started or "") and meta.started <= p.cutoff:
                p.sessions.append((meta.path, f"sessions/{meta.path.name}"))

    # Enrollment: the source's EFFECTIVE tier ([memory.companions], else default).
    cfg = config_loader.load_memory_config()
    if cfg is not None:
        p.tier = str(dict(cfg.get("companions") or {}).get(
            source, cfg.get("backend", "floor")))
    return p


def execute(fork_plan: ForkPlan) -> dict:
    """Worker: scaffold → copy identity + sessions → write restamped records →
    VERIFY with the startup loaders → enroll. A failure before enrollment rolls
    the whole new character dir back (ours, created this call); the caller owns
    the backend replay (rebuild) so a failed replay never undoes the fork."""
    from hearth.config import config_loader

    from .enroll import enroll_memory_tier

    char_dir = config_loader._DATA / "characters" / fork_plan.target
    if char_dir.exists():  # plan-to-execute race — create-only stands
        raise ForkError(f"character {fork_plan.target!r} appeared since the "
                        "preview — nothing written")
    char_dir.mkdir(parents=True)
    try:
        for src_path, rel in fork_plan.identity + fork_plan.sessions:
            dest = char_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_path, dest)
        rec_dir = char_dir / "memory" / "records"
        records_mod._ensure_dir(rec_dir)
        stamp = {"companion": fork_plan.source,
                 "juncture": fork_plan.cutoff,
                 "forked": _dt.date.today().isoformat()}
        for src_path, record in fork_plan.records:
            with open(src_path, "r", encoding="utf-8") as f:
                raw = json.load(f)  # raw dict: unknown keys survive the rewrite
            raw["companion"] = fork_plan.target
            raw["forked_from"] = stamp
            records_mod._atomic_write_json(rec_dir / src_path.name, raw)
        # The loader-verification probe = the startup path itself, not a copy.
        config_loader.compose_persona(fork_plan.target)
        for tag in fork_plan.voices:
            config_loader.load_voice(fork_plan.target, tag)
    except BaseException:
        shutil.rmtree(char_dir, ignore_errors=True)
        raise
    note = (enroll_memory_tier(fork_plan.target, fork_plan.tier)
            if fork_plan.tier is not None else
            "memory disabled (no config/memory.toml) — no enrollment")
    return {"records": len(fork_plan.records), "memory": note,
            "enrolled": note.startswith("enrolled")}
