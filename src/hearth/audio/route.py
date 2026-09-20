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

One more thing lives here for the same reason: the exit status a sitting uses
when it closed itself because its device never came back. The supervisor has to
be able to name that reason, and it must not import a pipeline to do it.

``<device-id>`` is ``[A-Za-z0-9._-]{1,64}`` — the same shape the session and
character ids already use, so nothing in a route can ever be a path segment,
a shell word, or a header.
"""

from __future__ import annotations

import re

DESK = "desk"
REMOTE = "remote"

#: The status a sitting exits with when it closed itself because the device it
#: was speaking on never came back. The supervisor reads it off the child and
#: names the reason on the launch page; it lives HERE, in the module with no
#: imports, so nothing has to load pipecat to know what a 3 means.
EXIT_DEVICE_GONE = 3

#: The status for the other absence: the device this sitting was started for
#: never arrived at all, and the start wait ran out. Its own number, because the
#: launch page names the reason and "did not come back" would be a lie about a
#: device that was never there.
EXIT_DEVICE_NEVER_CAME = 4

#: Set once, by the transport, just before it sends itself the Stop signal. The
#: close ladder then runs exactly as the button's does, and the reason survives
#: it: the entry point reads this flag AFTER the ladder and exits on it.
_DEVICE_GONE = False
_DEVICE_NEVER_CAME = False


def mark_device_gone() -> None:
    """The device never came back; this sitting is closing because of it."""
    global _DEVICE_GONE
    _DEVICE_GONE = True


def mark_device_never_came() -> None:
    """No device ever arrived; this sitting is closing because of it."""
    global _DEVICE_NEVER_CAME
    _DEVICE_NEVER_CAME = True


def device_gone() -> bool:
    """Whether this process is closing over a device that went away."""
    return _DEVICE_GONE


def device_never_came() -> bool:
    """Whether this process is closing over a device that never arrived."""
    return _DEVICE_NEVER_CAME


def clear_device_gone() -> None:
    """Forget both again — for a test that sets them, and nothing else."""
    global _DEVICE_GONE, _DEVICE_NEVER_CAME
    _DEVICE_GONE = False
    _DEVICE_NEVER_CAME = False


def exit_status() -> int:
    """The status the bot exits with: ``EXIT_DEVICE_GONE`` when the sitting
    closed itself over a device that did not return, ``EXIT_DEVICE_NEVER_CAME``
    when it closed over one that never arrived, else 0 (which is every other
    path, including the Stop button's)."""
    if _DEVICE_GONE:
        return EXIT_DEVICE_GONE
    if _DEVICE_NEVER_CAME:
        return EXIT_DEVICE_NEVER_CAME
    return 0

#: A device id: word characters, dot, dash, at most 64 of them.
DEVICE_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
#: The whole route word.
ROUTE_RE = re.compile(r"desk|remote:[A-Za-z0-9._-]{1,64}")


def parse(route: str | None) -> tuple[str, str | None]:
    """``route`` → ``(kind, device_id)``. ``None`` and ``""`` mean the desk.

    Raises ``ValueError`` — with the word in it, never a guess at what was
    meant — on anything else.
    """
    word = (route or DESK).strip() or DESK
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

    ``grace_left`` is always null here, and that is the difference between the
    two routes rather than a gap in this one: a desk conversation waits for its
    headset for as long as it takes.
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
            "buffer_ms": None, "shed_ms": 0, "grace_left": None}
