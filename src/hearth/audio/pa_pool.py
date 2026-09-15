"""pa_pool.py — a process-level pool for the audio library's instances
(`pyaudio.PyAudio`), so a device that comes back can actually be SEEN.

Why a pool at all. PortAudio enumerates devices exactly once: when its
initialise count goes 0 -> 1. Every later `pyaudio.PyAudio()` only bumps that
count and inherits the list captured by the first one. So a process that
keeps its start-time instance alive after a device drops out can never see
that device return — a "fresh" instance shows the stale list, the pinned name
resolves to the index of the device that went away, and the open fails. That
is not a theory: measured live, 284 consecutive reopen failures at 2s over
one sitting, while a brand-new process opened the same device first try.

The pool's whole job is therefore to know whether the count has really gone
back to zero, and to refuse to hand out an instance until it has:

  - `acquire()` returns an instance to enumerate with, or None while anything
    is still terminating. Creating one then would only bump the count and
    re-read nothing, so None means "wait", not "fail".
  - `release(pa)` gives an instance back. It is idempotent: releasing the same
    instance twice (both halves of a transport share one) is a no-op.
  - `clear` is true only when every instance the pool knows of has finished
    terminating.

Nothing here ever blocks the caller. `terminate()` can block forever on a
wedged stream — the same wedge the transport's abandon path exists for — so
`release()` runs it on an unjoined daemon thread and the instance stays
"releasing", and the pool unclear, until that call returns on its own. That
is the rule the audio code lives by: a call that can wedge never runs where
something is waiting on it, least of all the event loop.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

import pyaudio

_LIVE = "live"
_RELEASING = "releasing"
_DONE = "done"


class PyAudioPool:
    """The process's audio-library instances, and whether they have let go.

    `factory` exists for tests; production uses `pyaudio.PyAudio` itself.
    """

    def __init__(self, factory: Optional[Callable[[], object]] = None) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._records: List[dict] = []

    def _make(self):
        # Looked up at call time, never bound at construction: the pool is a
        # module-level singleton built at import, long before anything could
        # stand in for the library.
        if self._factory is not None:
            return self._factory()
        return pyaudio.PyAudio()

    @property
    def clear(self) -> bool:
        """True when nothing the pool knows of is still terminating."""
        with self._lock:
            return not any(r["state"] == _RELEASING for r in self._records)

    def acquire(self):
        """An instance whose device list can be trusted, or None.

        None while anything is still terminating: a new instance made then
        would only bump the initialise count and inherit the stale list.

        When an instance is already live — the peer half reopened onto it a
        moment ago — that same one is handed back rather than a second one
        made. It enumerated at 0 -> 1 after the device returned, so its list
        is the fresh list; a second instance would add nothing but a second
        thing to release.
        """
        with self._lock:
            if any(r["state"] == _RELEASING for r in self._records):
                return None
            for record in self._records:
                if record["state"] == _LIVE:
                    return record["pa"]
            pa = self._make()
            self._records.append({"pa": pa, "state": _LIVE})
            return pa

    def release(self, pa) -> None:
        """Give an instance back. Idempotent; never blocks the caller.

        The instance is marked releasing straight away, so the pool reads
        unclear from this moment, and the terminate that may never return is
        handed to a daemon thread that is never joined.
        """
        if pa is None:
            return

        with self._lock:
            record = next((r for r in self._records if r["pa"] is pa), None)
            if record is None:
                # An instance the pool never handed out — the start-time one
                # the transport was built with. It counts all the same.
                record = {"pa": pa, "state": _LIVE}
                self._records.append(record)
            if record["state"] != _LIVE:
                return  # already releasing, or already finished
            record["state"] = _RELEASING

        def _terminate_and_mark() -> None:
            try:
                pa.terminate()
            except Exception:
                pass
            with self._lock:
                record["state"] = _DONE

        threading.Thread(target=_terminate_and_mark, daemon=True).start()


# The process-level pool. One PortAudio initialise count, one pool.
pool = PyAudioPool()
