"""firstrun/model.py — recording the model id the server actually advertises.

The one write this surface owns. The id comes from the server's own
/v1/models listing, so the string that lands in model.toml is the string the
server answers to — the template's "VERBATIM" comment made mechanical. An id
the server does not advertise, or a server that does not answer, is refused
with the plain reason; "yes": true records it anyway, for the person who
knows their server better than a probe does.

The write itself is the bootstrap's own (hearth.init.set_model_id): comment-
preserving line surgery, parse-verified, schema-checked, refused untouched on
any doubt. Around it this route adds what a bootstrap on fresh files never
needed — one .prev generation beside the file, and copy-on-write when the
selection still resolves to the shipped tree.

One part of the /admin/first-run surface; the package __init__ carries the map
of the whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import shutil

from aiohttp import web
from loguru import logger

from .detect import model_facts, model_path, selection

_MAX_ID_LEN = 200

#: The honest effect time. The bot reads model.toml when it starts; the facade
#: snapshots the id at ITS start (serve/__main__.py), so its own /v1 chat leg
#: keeps sending the old one until the facade is restarted.
_EFFECT = ("the companion reads it at Start; Hearth's own /v1 chat endpoint "
           "follows at its next restart")


def _record(model_id: str, confirmed: bool, lm_url: str, lm_token: str):
    """Worker thread → (http_status, payload)."""
    from hearth import init  # lazy: mirrors the package gate idiom
    from hearth.config import config_loader

    sel = selection()
    if sel is None:
        return 409, {"ok": False, "error": "no readable config/active.toml — "
                                           "run python -m hearth.init first"}
    facts = model_facts(sel)
    path = model_path(facts["name"] or "")
    if not facts["name"] or not path.is_file():
        return 409, {"ok": False, "error": f"no model.toml for model {facts['name']!r} "
                                           "— run python -m hearth.init first"}
    advertised = init.probe_models(lm_url, lm_token)
    if model_id not in (advertised or []) and not confirmed:
        why = (f"{model_id!r} is not among the ids your model server lists"
               if advertised is not None else
               "your model server did not answer, so the id cannot be checked")
        return 409, {"ok": False, "error": why, "advertised": advertised,
                     "confirm": 'repeat with "yes": true to record it anyway'}
    # Shipped tree: copy-on-write into the data root (the settings forms' rule).
    root = config_loader._ROOT.resolve()
    data = config_loader._DATA.resolve()
    resolved = path.resolve()
    copied = False
    if resolved.is_relative_to(root) and not resolved.is_relative_to(data):
        target = config_loader._DATA / resolved.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        path, copied = target, True
    backup = None
    if not copied:  # one .prev generation beside an overwritten file
        backup = path.with_name(path.name + ".prev")
        shutil.copyfile(path, backup)
    rep = init.Report()
    try:
        init.set_model_id(path, model_id, rep)
    except init.InitError as exc:
        return 409, {"ok": False, "error": str(exc)}
    written = "set" in rep.states()
    if written:
        logger.info("[first-run] model id recorded for {}", facts["name"])
    return 200, {"ok": True, "written": written, "model": facts["name"], "id": model_id,
                 "advertised": model_id in (advertised or []),
                 "target": ("copied to the data root (shipped file untouched)"
                            if copied else "in place"),
                 "backup": backup.name if backup is not None else None,
                 "effect": _EFFECT}


async def _model_post(request: web.Request) -> web.Response:
    """POST /admin/first-run/model {id, yes?} — see the module docstring."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is an invalid request
        body = None
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "JSON body required"}, status=400)
    model_id = str(body.get("id") or "").strip()
    if not model_id or len(model_id) > _MAX_ID_LEN or "\n" in model_id:
        return web.json_response(
            {"ok": False, "error": f"'id' required — one line, at most {_MAX_ID_LEN} "
                                   "characters"}, status=400)
    deps = request.app["deps"]
    status, payload = await asyncio.to_thread(
        _record, model_id, bool(body.get("yes")), deps.lm_base_url, deps.lm_token)
    return web.json_response(payload, status=status)
