"""brand.py — the Hearth brand layer: one palette, one set of artwork, both hosts.

The control panel (:65000, in-bot) and the facade pages (:65001) are served by
different processes and had drifted into two visual languages. This module is
the single definition both splice at import, the same mechanism and the same
reason as ``ui/switch_card.js``: a shared file with a divergence-guard test
beats six hand-copied palettes.

Artwork is SERVED rather than inlined. The favicon and mark were 12.7 KB of
base64 sitting in one page; inlining them into six pages would have cost ~63 KB
and pushed launch_page.html past the 16 KB line. Two cacheable routes cost a
few bytes per page instead.

The facade routes are unauthed (serve/app.py's _AUTH_EXEMPT) — these are two
static images that ship publicly in docs/brand/ and disclose nothing. That is a
deliberate widening of a deliberately small set; see the note there.
"""

from __future__ import annotations

import re
from pathlib import Path

from aiohttp import web

from hearth.ui import pages

_DIR = Path(__file__).parent
_BRAND_DIR = _DIR / "brand"

#: The placeholder every page declares inside its own <style> block.
PLACEHOLDER = "/*BRAND_CSS*/"

#: A page names its own section and gets the header markup built here, so six
#: pages cannot end up with six subtly different header structures — and so the
#: markup costs each page ~25 bytes instead of ~175.
HEAD_PLACEHOLDER = re.compile(r"<!--BRANDHEAD:([^>]*?)-->")

CSS_PATH = _DIR / "brand.css"

#: The palette as of import. splice() re-reads through pages.text(), so editing
#: brand.css under HEARTH_DEV_RELOAD lands without a restart; this stays the
#: import-time value the divergence guards compare pages against.
CSS = pages.text(CSS_PATH)

#: filename → bytes, read once at import (the pages' read-once property).
ASSETS = {name: (_BRAND_DIR / name).read_bytes()
          for name in ("favicon.png", "mark.png")}

#: The URL path of each asset — the exact strings serve/app.py exempts.
ROUTES = tuple(f"/ui/brand/{name}" for name in sorted(ASSETS))

# A day is long enough that a walk never re-fetches the mark, short enough that
# a rebrand lands without anyone clearing a cache by hand.
_CACHE_CONTROL = "public, max-age=86400"


def header(section: str) -> str:
    """The brand header: mark, wordmark, and the page's own section label."""
    return ('<header class="brandhead">'
            '<span class="brandmark" aria-hidden="true"></span>'
            f'<div><h1>Hearth</h1><div class="brandsub">{section}</div></div>'
            '</header>')


def splice(page: str) -> str:
    """Return ``page`` with the brand CSS and header in place of its placeholders.

    Raises if the CSS placeholder is absent: a page that silently lost it would
    render unbranded and un-themed, which is exactly the drift this file exists
    to prevent. The header placeholder is optional — pair_page and any future
    fragment may legitimately not carry a header.
    """
    if PLACEHOLDER not in page:
        raise ValueError(
            f"page does not declare {PLACEHOLDER} — it cannot receive the brand "
            "layer (add the placeholder inside its <style> block)")
    page = HEAD_PLACEHOLDER.sub(lambda m: header(m.group(1)), page)
    return page.replace(PLACEHOLDER, pages.text(CSS_PATH))


def _serve(blob: bytes):
    async def handler(_request: web.Request) -> web.Response:
        return web.Response(body=blob, content_type="image/png",
                            headers={"Cache-Control": _CACHE_CONTROL})
    return handler


def add_routes(app: web.Application) -> None:
    """Register the brand artwork on an aiohttp app (both hosts call this)."""
    for name, blob in ASSETS.items():
        app.router.add_get(f"/ui/brand/{name}", _serve(blob))
