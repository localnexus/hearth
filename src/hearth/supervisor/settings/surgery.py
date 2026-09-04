"""settings/surgery.py — comment-preserving line surgery: aim at one key, touch
nothing else.

The write path's sharp instrument, kept alone in its own file. It sets one key
under one section and leaves every other byte — comments, ordering, spacing —
exactly as the operator wrote it. Trailing comments on the edited line survive.

It AIMS; it does not decide. The caller must parse the result and compare it
to the document it intended, and refuse the write on any difference. That
division is the whole safety property: surgery never guesses, and a refusal
leaves the file byte-identical.

One part of the /admin/settings surface; the package __init__ carries
the map of the whole and re-exports every name defined here.
"""

from __future__ import annotations

import re


class _SurgeryRefused(Exception):
    """The edit cannot be made without guessing — the caller reports
    'edit by hand' and the file stays byte-identical."""


def _surgical_set(text: str, section: str, key: str, rendered: str) -> str:
    """Set `key = rendered` under [section] ("" = the root table), touching
    nothing else. The caller MUST parse-verify the result against the intended
    document before writing — this function aims, the verification decides."""
    line = f"{key} = {rendered}"
    if section:
        m = re.search(rf"(?m)^\[[ \t]*{re.escape(section)}[ \t]*\][ \t]*$", text)
        if m is None:  # no such section header yet: append a fresh one
            base = text if not text or text.endswith("\n") else text + "\n"
            sep = "\n" if base.strip() else ""
            return base + sep + f"[{section}]\n{line}\n"
        start = m.end()
        nxt = re.compile(r"(?m)^\[").search(text, start)
        end = nxt.start() if nxt is not None else len(text)
    else:
        start = 0
        nxt = re.compile(r"(?m)^\[").search(text)
        end = nxt.start() if nxt is not None else len(text)
    span = text[start:end]
    km = re.search(rf"(?m)^(?P<ind>[ \t]*){re.escape(key)}[ \t]*=[ \t]*(?P<rest>[^\n]*)$",
                   span)
    if km is None:  # key not present: insert at the end of the section's span
        if section:  # right below the header keeps related keys together
            return text[:start] + "\n" + line + text[start:]
        seg = text[:end]
        if seg and not seg.endswith("\n"):
            seg += "\n"
        return seg + line + "\n" + text[end:]
    rest = km.group("rest")
    # Trailing comment survives. Callers refuse string values containing '#'
    # upstream, so a '#' in `rest` here can only start a comment.
    idx = rest.find("#")
    comment = rest[idx:].rstrip() if idx >= 0 else ""
    new_line = km.group("ind") + line + (("  " + comment) if comment else "")
    new_span = span[:km.start()] + new_line + span[km.end():]
    return text[:start] + new_span + text[end:]


def _deep_set(doc: dict, parts: list[str], value) -> None:
    cur = doc
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _deep_get(doc: dict, parts: list[str], default=None):
    cur = doc
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur
