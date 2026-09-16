"""audio/route.py — the sitting's audio route, as a word.

A sitting plays and listens either on the desk (the pinned local device, the
path that has always existed) or on one enrolled remote device reached over a
WebSocket. The choice is fixed at Start and never changes mid-sitting: a route
change is a new sitting.

The word travels a long way — the launch page's radio, ``body.route`` on the
start call, ``--audio`` on the child's argv, the bind in ``bot.py`` — and four
places have to agree on what a valid one looks like. So the grammar lives here,
in a module with NO imports beyond ``re``: the supervisor can validate a start
body without dragging in pipecat, and the bot can parse its own flag without
dragging in the supervisor.

    desk                 the local device (default)
    remote:<device-id>   the enrolled device with that id

``<device-id>`` is ``[A-Za-z0-9._-]{1,64}`` — the same shape the session and
character ids already use, so nothing in a route can ever be a path segment,
a shell word, or a header.
"""

from __future__ import annotations

import re

DESK = "desk"
REMOTE = "remote"

#: A device id: word characters, dot, dash, at most 64 of them.
DEVICE_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
#: The whole route word.
ROUTE_RE = re.compile(r"desk|remote:[A-Za-z0-9._-]{1,64}")


def parse(route: str | None) -> tuple[str, str | None]:
    """``route`` → ``(kind, device_id)``. ``None`` and ``""`` mean the desk.

    Raises ``ValueError`` — with the word in it, never a guess at what was
    meant — on anything else.
    """
    word = (route or DESK).strip()
    if not ROUTE_RE.fullmatch(word):
        raise ValueError(
            f"unknown audio route {word!r} (desk | remote:<device-id>, "
            "where a device id is letters, digits, dot, dash or underscore, "
            "up to 64 of them)")
    if word == DESK:
        return DESK, None
    return REMOTE, word.split(":", 1)[1]


def valid(route: str | None) -> bool:
    """True iff ``parse`` would accept it."""
    try:
        parse(route)
    except ValueError:
        return False
    return True


def device_of(route: str | None) -> str | None:
    """The device id a remote route names; ``None`` for the desk."""
    return parse(route)[1]


def label(route: str | None) -> str:
    """How a person reads the route back — the launch page's Stop line."""
    kind, device = parse(route)
    return "the desk" if kind == DESK else str(device)


def desk_route_state(audio_state: dict | None) -> dict:
    """The desk's own words, folded into the same shape ``/route`` answers for
    the remote route, so one reader serves both.

    The desk transport already reports per-direction device state; the route
    line takes the worst of the two, because a sitting with one half lost is a
    sitting that is not working.
    """
    words = []
    for half in ("input", "output"):
        entry = (audio_state or {}).get(half)
        if isinstance(entry, dict) and entry.get("state"):
            words.append(str(entry["state"]))
    for worst in ("lost", "recovered", "unpinned"):
        if worst in words:
            state = worst
            break
    else:
        state = "pinned"
    return {"kind": "desk", "device": None, "state": state, "path": None,
            "buffer_ms": None, "shed_ms": 0}
