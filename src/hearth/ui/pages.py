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


def splicer(placeholder: str, path: Path | str, what: str,
            where: str) -> Callable[[str], str]:
    """Build the transform that drops one shared file into a page.

    Seven splices now say the same four lines — check the placeholder, raise with
    a useful sentence, `replace()` through `text()` so dev reload reaches the
    shared file too. Writing them out seven times is how `control.py` and
    `supervisor/routes` ended up with two spellings of the same three lines before Q2.

    Raising, rather than passing a page through unchanged, is the load-bearing
    part: a page that silently lost its placeholder would serve markup with no
    behaviour behind it, and in production that failure lands at STARTUP.
    """
    path = Path(path)

    def splice(page: str) -> str:
        if placeholder not in page:
            raise ValueError(
                f"page does not declare {placeholder} — it cannot receive {what} "
                f"(add the placeholder {where})")
        return page.replace(placeholder, text(path))

    splice.__doc__ = f"Return the page with {what} in place of {placeholder}."
    return splice


def chain(*transforms: Callable[[str], str]) -> Callable[[str], str]:
    """Compose page transforms left to right — `chain(card.splice, brand.splice)`.

    A page takes three shared files now (the switcher, the admin shell, the brand
    layer); nesting their calls at each construction site was how the two hosts
    grew two subtly different spellings of the same splice."""
    def run(src: str) -> str:
        for transform in transforms:
            src = transform(src)
        return src
    return run


class Sections:
    """One page's own files: the pieces it was split into, and the transform
    that puts them back.

    Distinct from the SHARED files (brand.css, switch_card.js, admin_shell.js),
    which several pages splice because they must not drift between them. These
    belong to one page and exist to give it seams — so `page` is carried here
    and the guard test asserts no OTHER page takes one.

    Order: each splice is an independent `replace`, so what binds is where the
    PLACEHOLDERS sit in the page, not the order of `modules`. Where two sections
    reach across (a `let` in one read by another) that page's own module says so.
    """

    def __init__(self, page: str, directory: Path | str,
                 modules: tuple[tuple[str, str, str, str], ...]):
        self.page = page
        self.dir = Path(directory)
        #: (placeholder, filename, what it is, where the placeholder belongs)
        self.modules = modules
        self.paths = {name: self.dir / name for _, name, _, _ in modules}
        self.splices = tuple(
            splicer(placeholder, self.dir / name, what, where)
            for placeholder, name, what, where in modules)
        self.splice = chain(*self.splices)

    def text(self, name: str) -> str:
        """One section's source, through `text()` so dev reload reaches it."""
        return text(self.paths[name])

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for _, name, _, _ in self.modules)

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"<Sections {self.page}: {', '.join(self.names)}>"


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
