"""models/verbs.py — the four mutations: enroll, unenroll, render, apply.

Every one of them is preview-then-confirm, the same habit `curation._forget`
set and the `weights` CLI keeps: a call without ``"yes": true`` answers what it
WOULD do and changes nothing, and only the second call — the one a person made
after reading the first — writes. The previews are not summaries either; they
are the artefact itself, so there is nothing left to be surprised by:

  enroll     the exact ``[weights]`` / ``[weights.header]`` block that would be
             appended to that model's model.toml, as text
  unenroll   the reference that goes, and the sentence that the file stays
  render     the whole command line, and every flag the unit on the machine
             disagrees about, classified
  apply      the same diff, plus the name the existing unit would be archived
             under, plus the two launchctl lines

What none of them do is start, stop, or signal anything. `apply` writes a file
and hands back two lines of shell for the operator — or, on the card, for the
built-in `door-load` / `door-unload` buttons, which press the actuator runner
where a bounded, logged, non-child command belongs. `render.companion_guard`
is what stands between a live conversation and a replaced unit: blocked is a
409 and writes nothing; merely uncertain is a 200 that says so in a warning,
because a guess must not be dressed as process truth.

The key path rule from facts.py holds through all of it: argv and diff rows
are redacted before they are serialized, so `--api-key-file`'s value never
reaches a response body.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from importlib import import_module
from pathlib import Path

from aiohttp import web

from hearth.config import config_loader as cl

# The weights submodules, by NAME rather than by attribute. The package façade
# re-exports `enroll` as a FUNCTION, so `from hearth.weights import enroll`
# hands back the function and shadows the module of that name (the G1 shadowing
# note). `import_module` resolves the real submodule every time, and is the one
# spelling that cannot quietly become the wrong object as the façade grows.
enroll_mod = import_module("hearth.weights.enroll")
render_mod = import_module("hearth.weights.render")
roots_mod = import_module("hearth.weights.roots")
scan_mod = import_module("hearth.weights.scan")

from . import facts as facts_mod
from . import views as views_mod

#: A new model directory's name. The same shape config_loader accepts for a
#: directory name, narrowed to lower case: it becomes a path segment.
NEW_NAME_RE = re.compile(r"^[a-z0-9._-]+$")

CONFIRM = 'repeat the call with "yes": true'


async def _body(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is an invalid request
        return None
    return body if isinstance(body, dict) else None


def _cfg():
    return roots_mod.load_weights_config()


async def _config(request: web.Request):
    """The weights config, or the response that says why there isn't one."""
    try:
        return await asyncio.to_thread(_cfg), None
    except cl.ConfigError as exc:
        return None, web.json_response({"error": str(exc)}, status=500)


# ── enroll ───────────────────────────────────────────────────────────────────

def _candidate_from_path(raw: str):
    """A candidate for ONE named file, read from its own directory only.

    The CLI's `--path` road: no full scan, because the operator has already
    told us which file they mean.
    """
    path = Path(raw).expanduser()
    if not path.is_file():
        return None, f"no such file: {path}"
    real = Path(path.resolve())
    root = roots_mod.Root(real.parent.name or "path", real.parent, "user")
    for cand in scan_mod.scan_dir(real.parent, root):
        if cand.path == real or real in cand.shards:
            return cand, None
    return None, f"{path} is not a GGUF file this reader recognises"


def _projector(cand, choice: str):
    """`auto` (the one beside it) | `none` | a path. Ambiguity is a refusal."""
    if choice == "none":
        return None, None
    if choice != "auto":
        return _candidate_from_path(choice)
    options = cand.mmproj_candidates
    if not options:
        return None, None
    if len(options) > 1:
        names = ", ".join(p.display_key for p in options)
        return None, (f"{len(options)} projectors sit beside this model ({names}) "
                      "— name one as \"mmproj\", or say \"none\"")
    return options[0], None


def _create_from_example(name: str) -> str:
    """A new model directory, copied from the shipped example. → the path."""
    target_dir = cl.MODELS_DIR / name
    target = target_dir / "model.toml"
    source = facts_mod.example_model_toml()
    if not source.is_file():
        raise enroll_mod.WeightsError(
            f"no shipped example to copy from at {source}")
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return str(target)


async def _enroll(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return web.json_response({"error": "JSON body required"}, status=400)
    cfg, refusal = await _config(request)
    if refusal is not None:
        return refusal

    new_name = str(body.get("new") or "").strip()
    model = str(body.get("model") or "").strip()
    existing = await asyncio.to_thread(facts_mod.targets)
    if new_name:
        if not NEW_NAME_RE.fullmatch(new_name):
            return web.json_response(
                {"error": "a new model name may hold lower-case letters, digits, "
                          "dot, dash and underscore only"}, status=400)
        if new_name in existing or (cl.MODELS_DIR / new_name).exists():
            return web.json_response(
                {"error": f"{new_name!r} already exists — enroll into it as "
                          '"model" instead'}, status=409)
        model = new_name
    elif model not in existing:
        return web.json_response(
            {"error": f"no model directory {model!r} under the data folder",
             "targets": existing}, status=404)

    identity = str(body.get("identity") or "").strip()
    path = str(body.get("path") or "").strip()
    if identity:
        budget = await views_mod.budget_of(request.app, cfg)
        cand = await views_mod.candidate_by_identity(request.app, cfg, budget,
                                                     identity)
        error = None if cand is not None else (
            f"no scanned candidate with identity {identity!r} — rescan "
            "(GET /admin/models/scan?refresh=1) and try again")
    elif path:
        cand, error = await asyncio.to_thread(_candidate_from_path, path)
    else:
        return web.json_response(
            {"error": 'name the weights: "identity" (from the scan) or "path"'},
            status=400)
    if error is not None:
        return web.json_response({"error": error}, status=404)

    proj, error = await asyncio.to_thread(
        _projector, cand, str(body.get("mmproj") or "auto"))
    if error is not None:
        return web.json_response({"error": error}, status=409)

    table = enroll_mod.weights_table(cand, proj)
    budget = await views_mod.budget_of(request.app, cfg)
    fit, at = facts_mod.fit_text(
        cand.size_bytes, cand.header, budget,
        mmproj_bytes=proj.size_bytes if proj is not None else 0)
    preview = {
        "model": model,
        "creates": str(cl.MODELS_DIR / model / "model.toml") if new_name else None,
        "writes": str(enroll_mod.data_model_toml(model)),
        "block": enroll_mod.block_text(table),
        "weights": {"path": str(cand.path), "display_key": cand.display_key,
                    "size_bytes": cand.size_bytes, "identity": cand.identity,
                    "architecture": cand.architecture,
                    "shards": len(cand.shards),
                    "duplicates": [str(p) for p in cand.duplicates]},
        "mmproj": str(proj.path) if proj is not None else None,
        "fit": fit, "fit_at_ctx": at,
    }
    if not bool(body.get("yes")):
        return web.json_response({
            "ok": True, "enrolled": False, "preview": preview,
            "confirm": "enrolling writes the block above into that model.toml "
                       "and touches nothing else — " + CONFIRM})

    def _write() -> dict:
        created = _create_from_example(model) if new_name else None
        written = enroll_mod.enroll(model, cand, proj)
        return {"created": created, "wrote": str(written)}

    try:
        done = await asyncio.to_thread(_write)
    except (enroll_mod.WeightsError, OSError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=409)
    result = {"ok": True, "enrolled": True, "preview": preview, **done}
    if new_name:
        result["note"] = (f"{model}'s model.toml is the shipped example with the "
                          "weights bound to it — its `id` is still the placeholder. "
                          "Set it to the id the door will advertise (the settings "
                          "page writes that key) before selecting this model.")
    return web.json_response(result)


# ── unenroll ─────────────────────────────────────────────────────────────────

async def _unenroll(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return web.json_response({"error": "JSON body required"}, status=400)
    model = str(body.get("model") or "").strip()
    enrolled = await asyncio.to_thread(enroll_mod.load_enrolled, model) \
        if model else None
    if enrolled is None:
        return web.json_response(
            {"error": f"{model!r} has no [weights] table — nothing to remove"},
            status=404)
    preview = {"model": model, "weights": str(enrolled.path),
               "writes": str(enroll_mod.data_model_toml(model)),
               "keeps": "the weights file itself is never touched"}
    if not bool(body.get("yes")):
        return web.json_response({
            "ok": True, "unenrolled": False, "preview": preview,
            "confirm": "this removes the reference and nothing else — " + CONFIRM})
    written = await asyncio.to_thread(enroll_mod.unenroll, model)
    return web.json_response({"ok": True, "unenrolled": True, "preview": preview,
                              "wrote": str(written) if written else None})


# ── render ───────────────────────────────────────────────────────────────────

async def _render(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return web.json_response({"error": "JSON body required"}, status=400)
    model = str(body.get("model") or "").strip()
    cfg, refusal = await _config(request)
    if refusal is not None:
        return refusal
    if await asyncio.to_thread(enroll_mod.load_enrolled, model) is None:
        return web.json_response(
            {"error": f"{model!r} has no [weights] table — enroll first"},
            status=404)
    # write=True: into DATA/render/ only. LaunchAgents is `apply`'s road.
    unit = await asyncio.to_thread(facts_mod.unit_state, model, cfg, True)
    if unit.state == "unknown" and not unit.argv:
        return web.json_response({"ok": False, "error": unit.note}, status=409)
    return web.json_response({
        "ok": True, "model": model, "label": cfg.door.label,
        "argv": unit.argv,
        "wrote": str(cl.DATA_DIR / render_mod.RENDER_SUBDIR / f"{cfg.door.label}.plist"),
        "against": str(unit.target) if unit.target else None,
        "unit": unit.state, "diff": unit.counts, "rows": unit.rows,
        "note": unit.note or "not loaded: apply puts it where launchd reads it",
    })


# ── apply ────────────────────────────────────────────────────────────────────

async def _apply(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return web.json_response({"error": "JSON body required"}, status=400)
    model = str(body.get("model") or "").strip()
    cfg, refusal = await _config(request)
    if refusal is not None:
        return refusal
    if await asyncio.to_thread(enroll_mod.load_enrolled, model) is None:
        return web.json_response(
            {"error": f"{model!r} has no [weights] table — enroll first"},
            status=404)

    unit = await asyncio.to_thread(facts_mod.unit_state, model, cfg)
    if unit.state == "unknown" and not unit.argv:
        return web.json_response({"ok": False, "error": unit.note}, status=409)

    guard = await asyncio.to_thread(render_mod.companion_guard, cfg)
    if guard.blocked:
        return web.json_response(
            {"ok": False, "error": guard.text, "guard": "companion"}, status=409)

    target = unit.target
    preview = {
        "model": model, "label": cfg.door.label,
        "target": str(target) if target else None,
        "archives": (str(render_mod.archive_name(target))
                     if target is not None and target.exists() else None),
        "argv": unit.argv, "unit": unit.state, "diff": unit.counts,
        "rows": unit.rows,
        "lines": render_mod.launchctl_lines(target, cfg.door.label)
        if target is not None else [],
    }
    if not bool(body.get("yes")):
        answer = {"ok": True, "applied": False, "preview": preview,
                  "confirm": "this replaces the unit launchd reads (the old one "
                             "archived beside it) and runs nothing — " + CONFIRM}
        if not guard.certain:
            answer["warning"] = guard.text
        return web.json_response(answer)

    try:
        done = await asyncio.to_thread(render_mod.apply_unit, model, True, None,
                                       None, cfg)
    except (enroll_mod.WeightsError, cl.ConfigError, OSError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=409)
    answer = {
        "ok": True, "applied": True, "preview": preview,
        "wrote": str(done.target),
        "archived": str(done.archived) if done.archived else None,
        "rendered": str(done.unit.path),
        "lines": done.lines,
        "note": "written, not loaded — press Load (or run the two lines) when "
                "the moment is yours to choose",
    }
    if not guard.certain:
        answer["warning"] = guard.text
    return web.json_response(answer)
