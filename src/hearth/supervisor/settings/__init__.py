"""supervisor/settings/ — /admin/settings: the generated settings surface.

Schema-driven settings, STEP 2 (the proposal's "chrome" half): the settings
registry (step 1) declared every file-configurable knob — path, type, range,
default, owner, live path or effect time. This package serves that declaration
over JSON and hosts the generated form page that renders it, so a knob cannot
exist without appearing (the registry's coverage property, now user-facing).

Toolchain decision (step 2's "decide once"): NO front-end toolchain. The form
generator is plain JS in one static shell (settings_page.html, the fifth
auth-exempt page) walking the served JSON Schema — the proposal's own
static-front-end reasoning, satisfied by the facade's proven page idiom
instead of a second build stack.

Reading: every discovered file's verdict comes from the same strict check as
``python -m hearth.config.check``; values are the parsed TOML with
secret-marked fields REDACTED server-side (x-hearth ``secret``: the hindsight
API key and the env maps — their values never leave the file, even behind the
door). Verdict errors/warnings name KEYS only, never values (check.py's
contract).

Writing (preview-then-confirm, per the write-layer rule (c) — all mutation on
the facade, behind the bearer):

- ONE scalar key per write, via comment-preserving line surgery
  (generalizing the roster wizard's memory.toml insertion): replace the value
  on the key's own line (trailing comment kept) or insert the line under its
  section header. The edited text must parse AND equal the intended document
  exactly (everything else byte-equal semantically) or the write is REFUSED
  with "edit by hand" — surgery never guesses.
- Refusals are honest pointers: ``active`` → /admin/switch (a switch is an
  orchestration, not a file poke) · ``overrides``/``profile`` → the :65000
  panel (never fight the panel's own writer) · secret-marked fields → the
  desk · structured values (lists, sub-table maps) → the file.
- A file resolving to the SHIPPED tree copies-on-write into the data root
  (the persona-editor pattern): the engine tree is never edited in place.
- One ``.prev`` backup generation beside an overwritten file; atomic
  tmp → replace.
- Every response states the honest effect time from the field's x-hearth
  stamp (live path · "lands at bot+facade restart" + note) with the file's
  registry ``restart`` as the fallback — nothing pretends to apply live.

API (mounted by routes.build_mount iff [serve.supervisor] enabled; authed):
    GET  /admin/settings          → per-kind registry facts + discovered files
                                    with strict-check verdicts (keys only)
    GET  /admin/settings/schema   → the registry's JSON Schema bundle
    GET  /admin/settings/file?file=<label> → one file's values (redacted)
    POST /admin/settings/set {file, key, value, yes?}
         yes absent/false → validated preview (old → new + effect time),
         nothing written; yes true → surgical write
    GET  /admin/settings/ui       → the generated form page (static chrome,
         auth-exempt beside the launch/roster/memory shells)

── the package layout ───────────────────────────────────────────────────────
Imports run strictly downward this list, and settings_page.html sits beside
page.py:

    fields.py   the registry walk: what a dotted key points at, how a JSON
                value becomes a python one, how it goes back out as TOML,
                and which files exist to write to
    policy.py   what a form may write (and where each refusal points), plus
                what it may never show — the secret redaction
    surgery.py  the line edit itself: aim at one key, keep the comments,
                touch nothing else. It aims; the caller verifies
    page.py     the read side — the shell and the three GETs behind it
    write.py    the set verb, preview-then-confirm, end to end

This __init__ is the façade: it re-exports every name the parts define, so
``from hearth.supervisor import settings`` still reaches all of them.
"""

from __future__ import annotations

from aiohttp import web

from .fields import (
    _MAP_KEY_RE, _coerce, _discovered, _render_toml, _resolve, _scalar_kind,
    _xh)
from .policy import (
    _REDACTED, _REFUSALS, _WRITABLE, _redact, _redacted_values)
from .surgery import _SurgeryRefused, _deep_get, _deep_set, _surgical_set
from .page import _PAGE, _file, _overview, _page, _schema
from .write import _apply, _set

__all__ = ["add_routes"]


def add_routes(app: web.Application) -> None:
    """Called by routes.build_mount — same door, same middleware."""
    app.router.add_get("/admin/settings", _overview)
    app.router.add_get("/admin/settings/schema", _schema)
    app.router.add_get("/admin/settings/file", _file)
    app.router.add_get("/admin/settings/ui", _page)
    app.router.add_post("/admin/settings/set", _set)
