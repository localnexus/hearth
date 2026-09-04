"""settings/page.py — the read side: the generated form's shell, and the three
GETs it walks.

The shell is auth-exempt and contentless like the other admin pages; every
fact it renders arrives from the three GETs here. Each of them runs its file
reads in a worker thread — the strict check walks every discovered file, and
the form must not block the loop while it does.

Verdicts name KEYS and never values (check.py's contract), and values come
back through the redaction in policy.py.

One part of the /admin/settings surface; the package __init__ carries
the map of the whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

from aiohttp import web

from hearth.ui import admin_shell, brand, pages, settings_sections

from .fields import _discovered
from .policy import _REFUSALS, _WRITABLE, _redacted_values

# The page keeps its markup, the state every section reads, and the wiring that
# starts it; the schema walkers, the file list, the generated form and the
# confirm step are four files under ui/ — see ui/settings_sections.py.
_PAGE = pages.Page(Path(__file__).parent / "settings_page.html",
                   pages.chain(settings_sections.splice, admin_shell.splice,
                               brand.splice))


async def _overview(request: web.Request) -> web.Response:
    """Registry facts per kind + every discovered file's strict verdict
    (errors/warnings name keys only — check.py's contract)."""
    def _scan() -> list[dict]:
        from hearth.config import check
        from hearth.config import settings_registry as sr

        kinds = {k: {"kind": k, "title": e.title, "path": e.path, "role": e.role,
                     "owner": e.owner, "layer": e.layer, "restart": e.restart,
                     "note": e.note, "top_key": e.top_key,
                     "writable": k in _WRITABLE,
                     "pointer": _REFUSALS.get(k), "files": []}
                 for k, e in sr.REGISTRY.items()}
        for kind, path in check.discover():
            verdict, errors, warnings = check.check_file(kind, path)
            kinds[kind]["files"].append({
                "file": check._rel(path), "verdict": verdict,
                "errors": errors, "warnings": warnings,
            })
        return list(kinds.values())

    return web.json_response({"kinds": await asyncio.to_thread(_scan)})


async def _schema(request: web.Request) -> web.Response:
    """The step-2 form contract, verbatim from the registry."""
    def _emit() -> dict:
        from hearth.config import settings_registry as sr

        return sr.json_schema()

    return web.json_response({"schema": await asyncio.to_thread(_emit)})


async def _file(request: web.Request) -> web.Response:
    """One discovered file's parsed values (secret fields redacted)."""
    label = request.query.get("file") or ""

    def _load():
        from hearth.config import check
        from hearth.config import settings_registry as sr

        disc = _discovered()
        if label not in disc:
            return None
        kind, path = disc[label]
        entry = sr.REGISTRY[kind]
        verdict, errors, warnings = check.check_file(kind, path)
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        if entry.top_key is not None:
            inner = data.get(entry.top_key)
            data = inner if isinstance(inner, dict) else {}
        return {"kind": kind, "file": label, "verdict": verdict,
                "errors": errors, "warnings": warnings,
                "writable": kind in _WRITABLE, "pointer": _REFUSALS.get(kind),
                "values": _redacted_values(entry.model, data)}

    got = await asyncio.to_thread(_load)
    if got is None:
        return web.json_response(
            {"error": f"unknown file {label!r} — GET /admin/settings lists them"},
            status=404)
    return web.json_response(got)


async def _page(_req: web.Request) -> web.Response:
    """The generated form page — static chrome (see settings_page.html's
    security contract; the serve middleware exempts exactly this path)."""
    return web.Response(text=_PAGE(), content_type="text/html")
