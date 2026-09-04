"""pages.py — the UI source files: read once in production, per request in dev.

Every page is `read_text()` at IMPORT and served from that one string. That is the
right production property — the bytes a request gets cannot change under it, and no
request ever touches the disk — and it is also why editing a page costs a process
restart: the facade for its five pages, the bot for the control panel. On page work
that restart is the whole tax, and it is paid per edit.

`HEARTH_DEV_RELOAD=1` inverts it for the process you set it on: every read goes back
to disk, so a page edit costs a browser refresh instead. Two consequences worth
stating out loud, because they are why this stays off by default:

  - A malformed edit becomes a 500 at request time rather than a startup failure
    (`brand.splice` refuses a page that lost its placeholder). In dev that is the
    point; in production a page must never be able to fail per-request.
  - The read happens on the request path.

The flag is read ONCE, here, at import — a running production process cannot drift
into dev behaviour, and the two modes cannot interleave within one process.

Shared sources (brand.css, switch_card.js) go through `text()` too, so a page that
splices them re-reads THEM as well: the point of dev reload is editing the card,
and reloading the page without the card would be a trap.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

ENV_VAR = "HEARTH_DEV_RELOAD"


def _flag(value: str) -> bool:
    """Off unless explicitly on: unset, 0, false, no (any case) all mean off."""
    return value.strip().lower() not in ("", "0", "false", "no")


DEV_RELOAD = _flag(os.environ.get(ENV_VAR, ""))

_CACHE: dict[Path, str] = {}


def text(path: Path | str) -> str:
    """One UI source file (page HTML, shared CSS or JS), decoded UTF-8.

    Cached per path — so the import-time read stays the only read — unless dev
    reload is on, in which case every call re-reads."""
    path = Path(path)
    if DEV_RELOAD:
        return path.read_text(encoding="utf-8")
    cached = _CACHE.get(path)
    if cached is None:
        cached = _CACHE[path] = path.read_text(encoding="utf-8")
    return cached


class Page:
    """A page file plus the transform that turns it into the served HTML.

    Callable — `web.Response(text=_PAGE())`. In production the transform runs once,
    at construction, which preserves the import-time behaviour exactly: the handler
    hands back the same string every time. Under dev reload nothing is kept and the
    page (with whatever `text()` its transform pulls in) is rebuilt per call.
    """

    def __init__(self, path: Path | str, transform: Optional[Callable[[str], str]] = None):
        self.path = Path(path)
        self._transform = transform
        self._built: Optional[str] = None if DEV_RELOAD else self._build()

    def _build(self) -> str:
        src = text(self.path)
        return self._transform(src) if self._transform else src

    def __call__(self) -> str:
        return self._build() if self._built is None else self._built

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        mode = "dev-reload" if self._built is None else "cached"
        return f"<Page {self.path.name} ({mode})>"
