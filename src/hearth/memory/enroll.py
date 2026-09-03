"""enroll.py — the [memory.companions] tier entry (targeted, comment-preserving).

One implementation, two callers: the roster wizard's onboarding
(supervisor/roster.py) and the fork verb (memory/fork.py) both finish by
recording a companion's tier here. Insert-only — an existing enrollment is
never rewritten (curation of a living entry is the operator's hand edit).
"""

from __future__ import annotations

import re
import tomllib


def enroll_memory_tier(name: str, tier: str) -> str:
    """Insert `<name> = "<tier>"` under [memory.companions]; parse-verified
    before the atomic replace. Returns a human note — never raises past the
    caller's containment (a failed enrollment must not undo an onboarding)."""
    from hearth.config import config_loader

    path = config_loader.MEMORY_TOML  # the one path every consumer reads
    if not path.is_file():
        return ("memory.toml absent — tier not recorded (enable memory, then "
                f'add {name} = "{tier}" under [memory.companions])')
    text = path.read_text(encoding="utf-8")
    try:
        existing = dict(tomllib.loads(text).get("memory", {}).get("companions") or {})
    except tomllib.TOMLDecodeError:
        return "memory.toml did not parse — tier not recorded (fix it, then enroll by hand)"
    if name in existing:
        return f"already enrolled as {existing[name]!r} — left untouched"
    line = f'{name} = "{tier}"'
    m = re.search(r"(?m)^\[memory\.companions\][ \t]*$", text)
    if m:
        new_text = text[:m.end()] + "\n" + line + text[m.end():]
    else:
        new_text = text.rstrip("\n") + f"\n\n[memory.companions]\n{line}\n"
    parsed = dict(tomllib.loads(new_text).get("memory", {}).get("companions") or {})
    if parsed.get(name) != tier:  # never replace the file with a bad edit
        return "edit verification failed — tier not recorded (enroll by hand)"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return (f'enrolled: {name} = "{tier}" — lands at the next bot/facade start '
            "(nothing under [memory] applies live)")
