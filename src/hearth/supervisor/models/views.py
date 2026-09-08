"""models/views.py — the three reading routes: the list, the scan, the door.

`GET /admin/models` is the card's per-poll read, so everything in it is cheap:
the enrolled `[weights]` table, the header facts that were cached at enrollment
(never a re-read of a 38 GB file's header), one `stat` per file through
`enroll.check`, the plist already on the machine, and one residency listing
shared by every row.

`GET /admin/models/scan` is the opposite and says so. A cold walk of every root
takes the better part of a minute on a machine with a real model library, and
the choice made here is the SIMPLER of the two the design offered: run it
synchronously in a worker thread and let the request hold. No background task,
no job id, no poll loop, no second state machine to get wrong — and the wait is
honest, the same shape the actuator runner already has ("the wait IS the
spinner"). The answer is then kept for the daemon's life; `?refresh=1` walks
again. One scan at a time: a second request waits on the same lock and gets the
answer the first one produced rather than starting a duplicate walk.

Residency (the ● on a row) reuses the /admin/state idiom rather than importing
it: one GET at the facade's configured model endpoint, any answer at all being
the truth, a failure being an honest `null` rather than a claim. A model is
resident when the door lists the id its `model.toml` advertises — the same
alias match the panel's own residency check makes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from importlib import import_module

import aiohttp
from aiohttp import web

from hearth.config import config_loader as cl

# The weights submodules, by NAME rather than by attribute. The package façade
# re-exports `enroll` as a FUNCTION, so `from hearth.weights import enroll`
# hands back the function and shadows the module of that name (the G1 shadowing
# note). `import_module` resolves the real submodule every time, and is the one
# spelling that cannot quietly become the wrong object as the façade grows.
enroll_mod = import_module("hearth.weights.enroll")
roots_mod = import_module("hearth.weights.roots")
scan_mod = import_module("hearth.weights.scan")

from . import door as door_mod
from . import facts as facts_mod

#: How long a residency listing may take before it is simply unknown.
PROBE_TIMEOUT_S = 2.0


def store(app: web.Application) -> dict:
    """This package's mutable corner of the app (the mapping itself freezes at
    startup, so everything that changes at runtime lives inside this dict)."""
    return app["models"]


# ── residency: the /admin/state probe idiom, not the /admin/state module ─────

async def resident_ids(app: web.Application) -> list | None:
    """The model ids the door is holding, or None when nothing answered."""
    deps = app["deps"]
    session = getattr(deps, "session", None)
    if session is None:
        return None
    url = str(deps.lm_base_url or "").rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {deps.lm_token}"} if deps.lm_token else None
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S)) as r:
            body = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        return None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None
    return [str(row.get("id")) for row in data
            if isinstance(row, dict) and row.get("id")]


# ── the budget, once per daemon ──────────────────────────────────────────────

async def budget_of(app: web.Application, cfg: roots_mod.WeightsConfig):
    held = store(app).get("budget")
    if held is None:
        held = await asyncio.to_thread(facts_mod.machine_budget, cfg)
        store(app)["budget"] = held
    return held


# ── GET /admin/models ────────────────────────────────────────────────────────

def _row(name: str, cfg: roots_mod.WeightsConfig, budget) -> dict:
    """One enrolled model, read from files only. Blocking; runs in a thread."""
    enrolled = enroll_mod.load_enrolled(name)
    # `llama_server=None` deliberately: validating [server] keys means running
    # the door's own --help, which is a subprocess this surface will not pay
    # for on a poll. The CLI's `check` is where that lives.
    findings = enroll_mod.check(name, None)
    state, state_text = facts_mod.state_from(findings)
    header = facts_mod.header_facts(enrolled.header if enrolled else None)
    fit, at = facts_mod.fit_text(
        enrolled.size_bytes if enrolled else 0, header, budget,
        ctx=facts_mod.model_ctx(name))
    unit = facts_mod.unit_state(name, cfg)
    return {
        "name": name,
        "id": facts_mod.model_id(name),
        "state": state,
        "state_text": state_text,
        "fit": fit,
        "fit_at_ctx": at,
        "unit": unit.state,
        "unit_note": unit.note,
        "unit_path": str(unit.target) if unit.target else None,
        "diff": unit.counts,
        "weights": {
            "path": str(enrolled.path) if enrolled else None,
            "display_key": enrolled.display_key if enrolled else "",
            "size_bytes": enrolled.size_bytes if enrolled else 0,
            "identity": enrolled.identity if enrolled else "",
            "architecture": (header.architecture if header else None),
            "mmproj": str(enrolled.mmproj) if enrolled and enrolled.mmproj else None,
            "enrolled": enrolled.enrolled if enrolled else "",
        },
        "findings": [{"level": f.level, "text": f.text} for f in findings
                     if f.level != "ok"],
    }


def _read_all(cfg: roots_mod.WeightsConfig, budget) -> dict:
    names = enroll_mod.enrolled_models()
    return {"models": [_row(n, cfg, budget) for n in names],
            "targets": facts_mod.targets()}


async def _models(request: web.Request) -> web.Response:
    app = request.app
    try:
        cfg = await asyncio.to_thread(roots_mod.load_weights_config)
    except cl.ConfigError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    budget = await budget_of(app, cfg)
    body, resident = await asyncio.gather(
        asyncio.to_thread(_read_all, cfg, budget), resident_ids(app))
    for row in body["models"]:
        # None is honest silence — the door did not answer, so nothing here
        # knows what it holds. False is a real "not loaded".
        row["resident"] = (None if resident is None
                           else bool(row["id"] and row["id"] in resident))
    body["door"] = door_mod.door_json(cfg)
    body["budget"] = facts_mod.budget_json(budget)
    return web.json_response(body)


# ── GET /admin/models/scan ───────────────────────────────────────────────────

def _candidate_json(cand, budget, enrolled_by_identity: dict) -> dict:
    header = cand.header
    fit, at = facts_mod.fit_text(cand.size_bytes, header, budget)
    return {
        "display_key": cand.display_key,
        "path": str(cand.path),
        "root": cand.root_name,
        "layout": cand.layout,
        "size_bytes": cand.size_bytes,
        "identity": cand.identity,
        "architecture": header.architecture if header else None,
        "context_length": header.context_length if header else None,
        "header_error": cand.header_error,
        "shards": len(cand.shards),
        "duplicates": [str(p) for p in cand.duplicates],
        "mmproj": [{"display_key": m.display_key, "path": str(m.path),
                    "size_bytes": m.size_bytes} for m in cand.mmproj_candidates],
        "fit": fit,
        "fit_at_ctx": at,
        "enrolled_as": enrolled_by_identity.get(cand.identity),
    }


def enrolled_by_identity() -> dict:
    """identity → the model name already holding it. The scan list marks its
    own rows with this, so nobody enrolls the same file twice by accident."""
    out: dict = {}
    for name in enroll_mod.enrolled_models():
        got = enroll_mod.load_enrolled(name)
        if got is not None and got.identity:
            out.setdefault(got.identity, name)
    return out


def _walk(cfg: roots_mod.WeightsConfig, budget) -> tuple[dict, list]:
    """The scan itself. Minutes of filesystem work; always in a thread.

    Returns the JSON body AND the Candidate objects behind it: enrolling by
    identity needs the object the scan built (its header facts, its shards,
    the projectors beside it), and re-walking the disks to find one file again
    would be the whole minute paid twice.
    """
    resolved = roots_mod.resolve_roots(cfg)
    found = scan_mod.scan_all(resolved)
    known = enrolled_by_identity()
    models = [c for c in found if c.kind == "model"]
    body = {
        "scanned": datetime.now().astimezone().isoformat(timespec="seconds"),
        "roots": [{"name": r.name, "kind": r.kind, "path": str(r.path)}
                  for r in resolved],
        "candidates": [_candidate_json(c, budget, known) for c in models],
    }
    return body, models


async def scan_result(app: web.Application, cfg: roots_mod.WeightsConfig,
                      budget, refresh: bool = False) -> dict:
    """The cached scan, walking the roots when there is nothing to serve.

    One at a time: the lock means a second caller waits for the first walk
    rather than starting a second one over the same disks.
    """
    held = store(app)
    if held.get("lock") is None:
        held["lock"] = asyncio.Lock()   # made here: we are on the loop
    async with held["lock"]:
        if refresh or held.get("scan") is None:
            body, objects = await asyncio.to_thread(_walk, cfg, budget)
            held["scan"], held["objects"] = body, objects
            return dict(body, cached=False)
        # A cached list still refreshes which rows are already enrolled — that
        # changes on this surface, and it costs one directory read.
        known = await asyncio.to_thread(enrolled_by_identity)
        for row in held["scan"]["candidates"]:
            row["enrolled_as"] = known.get(row["identity"])
        return dict(held["scan"], cached=True)


async def candidate_by_identity(app: web.Application,
                                cfg: roots_mod.WeightsConfig, budget,
                                identity: str):
    """The Candidate a scan row stands for. Scans first if nothing is held."""
    await scan_result(app, cfg, budget)
    for cand in store(app).get("objects") or []:
        if cand.identity == identity:
            return cand
    return None


async def _scan(request: web.Request) -> web.Response:
    try:
        cfg = await asyncio.to_thread(roots_mod.load_weights_config)
    except cl.ConfigError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    budget = await budget_of(request.app, cfg)
    body = await scan_result(request.app, cfg, budget,
                             refresh=request.query.get("refresh") == "1")
    body["targets"] = await asyncio.to_thread(facts_mod.targets)
    body["budget"] = facts_mod.budget_json(budget)
    return web.json_response(body)


# ── GET /admin/models/door ───────────────────────────────────────────────────

async def _door(request: web.Request) -> web.Response:
    try:
        cfg = await asyncio.to_thread(roots_mod.load_weights_config)
    except cl.ConfigError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    body = door_mod.door_json(cfg)
    body["resident_models"] = await resident_ids(request.app)
    return web.json_response(body)
