"""audio/remote_grace.py — how long a conversation waits for a device to come
back, and what it knows while it waits.

The desk and the remote route lose a device differently, and the difference is
the whole reason this module exists. A desk headset that is unplugged is
silence you can wait out: nobody else is holding the room, so the conversation
waits indefinitely. A phone that drops off is a socket that ended — a screen
that slept, a tab that was killed, a lift with no signal — and waiting
indefinitely for one means a conversation left open all night, with the model
resident and the transcript unwritten.

So the remote route waits a **bounded** time: the window opens when the socket
goes, closes when the device says hello again, and runs out into a close that
takes the Stop button's own path. The length is the away lane's proven figure,
180 seconds, overridable by ``HEARTH_AUDIO_GRACE_S``.

The same object serves a second wait: the one **before** any device has
arrived. A conversation started for a device that never opens its talk page
(the wrong radio picked, a phone left in a drawer) would otherwise sit with the
model resident for ever. That window opens when the socket starts listening,
closes at the first hello, and runs out the same way; its length is
``HEARTH_AUDIO_START_WAIT_S``, default 180 seconds as well.

Everything here is pure and clock-injected: no sleeping, no loop, no socket, so
every case in the window's life is a unit test rather than a thing you find out
about four minutes into a conversation.
"""

from __future__ import annotations

import math
import os
import time

#: The away lane's proven figure. Three minutes is long enough to walk through
#: a dead spot, ride a lift, or answer the door; it is not long enough to leave
#: a conversation open all night on a phone in a pocket.
DEFAULT_GRACE_S = 180

#: The environment word that overrides it.
GRACE_ENV = "HEARTH_AUDIO_GRACE_S"

#: How long a conversation waits for its device to arrive in the first place.
#: The same figure: long enough to unlock a phone and open a page, not long
#: enough to hold the model for a device that was never going to come.
DEFAULT_START_WAIT_S = 180

#: The environment word that overrides that one.
START_WAIT_ENV = "HEARTH_AUDIO_START_WAIT_S"


def _seconds_from_env(env: str, default: float) -> float:
    """A window length from the environment, else ``default``.

    A value that is missing, unreadable or non-positive falls back rather than
    failing a conversation: an operator who typed the wrong thing should get
    the proven window, not a route that refuses to wait at all (or one that
    closes the moment a device blinks).
    """
    raw = os.environ.get(env, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    if value <= 0 or math.isinf(value) or math.isnan(value):
        return float(default)
    return value


def grace_seconds() -> float:
    """The loss window's length from the environment, else the default."""
    return _seconds_from_env(GRACE_ENV, DEFAULT_GRACE_S)


def start_wait_seconds() -> float:
    """The start wait's length from the environment, else its default."""
    return _seconds_from_env(START_WAIT_ENV, DEFAULT_START_WAIT_S)


class GraceWindow:
    """The wait, as an object: open it on a loss, close it on a return.

    ``seconds`` is the window's length — ``None`` takes it from the
    environment, and a non-positive one is treated the same way the
    environment's is. ``clock`` is injected so a test can move time without
    spending any.
    """

    def __init__(self, seconds: float | None = None, clock=time.monotonic) -> None:
        if seconds is None:
            length = grace_seconds()
        else:
            try:
                length = float(seconds)
            except (TypeError, ValueError):
                length = float(DEFAULT_GRACE_S)
            if length <= 0:
                length = float(DEFAULT_GRACE_S)
        self._seconds = length
        self._clock = clock
        self._lost_at: float | None = None

    # ── the window itself ─────────────────────────────────────────────────
    @property
    def seconds(self) -> float:
        """How long this window waits, whole."""
        return self._seconds

    @property
    def active(self) -> bool:
        """True while a device is away and the window is running."""
        return self._lost_at is not None

    def lose(self) -> None:
        """The device went. The window starts now; a second loss restarts it,
        because the wait is for the device that just left."""
        self._lost_at = self._clock()

    def rejoin(self) -> float:
        """The device came back. Answers how long it was away, in seconds, and
        closes the window. ``0.0`` when no window was open — a first connection
        is not a return."""
        if self._lost_at is None:
            return 0.0
        gap = max(0.0, self._clock() - self._lost_at)
        self._lost_at = None
        return gap

    # ── what the surface reads ────────────────────────────────────────────
    def elapsed(self) -> float:
        """Seconds since the loss; ``0.0`` when no window is open."""
        if self._lost_at is None:
            return 0.0
        return max(0.0, self._clock() - self._lost_at)

    def remaining(self) -> float:
        """Seconds of the window still to run, as a float, never below zero.
        ``0.0`` when no window is open."""
        if self._lost_at is None:
            return 0.0
        return max(0.0, self._seconds - self.elapsed())

    def left(self) -> int:
        """What the Stop card counts down: whole seconds remaining, rounded up
        so the first reading is the whole window and the last is 1, never below
        zero. ``0`` when no window is open, which is also what an expired one
        says — the state word says which of the two it is."""
        return int(math.ceil(self.remaining()))

    def expired(self) -> bool:
        """True once an open window has run out. A window that was never opened
        has nothing to expire, and says so."""
        return self._lost_at is not None and self.remaining() <= 0.0
