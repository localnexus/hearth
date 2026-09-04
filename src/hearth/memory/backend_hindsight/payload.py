"""backend_hindsight/payload.py — what the adapter hands the server to extract
from.

Two facts are taken out of a canonical record and nothing else: its text, and
when it ended. Hindsight's extraction works on prose, so the transcript is
rendered plainly and speaker-labelled; the date is what keeps a REBUILD's
replayed history correctly dated instead of stamped with the replay day.

Kept apart from the adapter because it is the one part of this backend that
touches conversation content, and because it is pure: same record in, same
bytes out, no client, no process, no clock.
"""

from __future__ import annotations

from datetime import datetime

from ..backend import SessionRecord

_MAX_RETAIN_CHARS_DEFAULT = 6000


def _ended_at(record: SessionRecord) -> datetime | None:
    """``record.ended`` as a datetime (retain's fact-dating anchor); None —
    retain falls back to its own clock — when the field is absent or odd."""
    try:
        return datetime.fromisoformat(record.ended) if record.ended else None
    except ValueError:
        return None


def _render_transcript(record: SessionRecord, max_chars: int) -> str:
    """The retain payload: a plain speaker-labelled transcript, tail-capped.

    Hindsight's extraction works on prose; the tail cap bounds session-end
    latency at the cost of dropping the oldest turns of a very long session —
    the canonical record keeps them all, so a later rebuild with a higher cap
    loses nothing.
    """
    lines: list[str] = []
    for m in record.messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        speaker = "User" if role == "user" else "Assistant"
        content = " ".join(str(m.get("content", "")).split())
        if content:
            lines.append(f"{speaker}: {content}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
        cut = text.find("\n")  # drop the partial first line after the cut
        if 0 <= cut < len(text) - 1:
            text = text[cut + 1:]
    return text
