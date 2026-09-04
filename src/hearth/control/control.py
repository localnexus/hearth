"""
control.py — control seam: the aiohttp web control box.

The two in-pipeline FrameProcessors this box drives (MuteGate, SpeakingTap) live in
control_taps.py; this module holds only the web layer.

Exports:
    fetch_engine_info — pure probe of LM Studio's engine facts (bot.py calls it at
                        startup and on a slow re-poll)
    build_web_app     — build the aiohttp Application with all routes wired
    start_web_server  — AppRunner/TCPSite lifecycle helper (call before runner.run())

Bind constants (local-only by default):
    WEB_HOST = "127.0.0.1"       (override with WEB_HOST env var; 0.0.0.0 = LAN)
    WEB_PORT = 65000             (override with WEB_PORT env var)

# NOTE — LAN binding
# Loopback-only by default (127.0.0.1). To reach the box from a phone on the LAN,
# launch with the WEB_HOST env var set to "0.0.0.0" (e.g. `WEB_HOST=0.0.0.0 ./start.sh`)
# — that exposes the control channel (mic mute, text inject) + the read-only status
# block to every device on the LAN. An OWNER opt-in, not a default.
"""

# ─── STABLE CORE ────────────────────────────────────────────────────────────────
# Build new panel functionality in a SIBLING feature module and register its routes
# via control_routes.register — this file takes ZERO edits per feature.
# Sanctioned seams:  • control_routes.register (new routes)  • control_page.html (UI)
# ────────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web
from loguru import logger
from pipecat.frames.frames import (
    InterruptionFrame,
    LLMRunFrame,
)

from hearth.control import control_routes
from hearth.ui import brand
from hearth.ui import pages
from hearth.ui import panel
from hearth.ui import switch_card

if TYPE_CHECKING:
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from hearth.control.control_taps import MuteGate, SpeakingTap
    from hearth.recording.recording import Recorder
    from hearth.session.token_meter import TokenMeter

# ── Bind constants ─────────────────────────────────────────────────────────────
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")  # loopback by default; opt into LAN with WEB_HOST=0.0.0.0
WEB_PORT = int(os.environ.get("WEB_PORT", "65000"))  # high dynamic-range port (low collision risk); override with WEB_PORT env


# ── Engine info (probed at startup; bot.py re-polls on a slow cadence) ──────────

async def fetch_engine_info(base_url: str, token: str, target_model: str | None = None) -> dict:
    """One authenticated GET {lm_host}/api/v0/models → current engine facts.

    Returns a dict with keys: provider, model_id, allotted (loaded_context_length),
    model_max (max_context_length). On ANY failure (server down, 401, model not
    loaded, timeout, malformed body) the value keys are None so the panel degrades
    to '—'. Never raises; never blocks startup; never logs the token.

    `base_url` is the OpenAI-compat base (…/v1); we strip the trailing /v1 to reach
    the LM Studio native API root, then hit /api/v0/models and pick the model Hearth
    targets (`target_model` == LM_MODEL) — NOT merely the first loaded, since LM Studio
    may serve several models to several clients at once (e.g. a separate agent session).
    Falls back to first-loaded only when no target is given.
    """
    info = {
        "provider": "LM Studio",  # config-known from base_url; probe confirms it
        "model_id": None,
        "allotted": None,
        "model_max": None,
    }
    lm_host = base_url.rstrip("/")
    if lm_host.endswith("/v1"):
        lm_host = lm_host[: -len("/v1")]
    url = f"{lm_host}/api/v0/models"
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning("[control] engine probe HTTP {} — panel shows —", resp.status)
                    return info
                body = await resp.json()
        models = body.get("data", []) if isinstance(body, dict) else []
        # Pick the model HEARTH targets, not merely the first loaded — LM Studio serves
        # several models to several clients at once (e.g. a separate agent session),
        # so first-loaded-wins can silently report another app's model (and its context).
        if target_model:
            loaded = next((m for m in models if m.get("id") == target_model), None)
            if loaded is not None and loaded.get("state") != "loaded":
                logger.warning("[control] Hearth's model '{}' present but NOT loaded — panel shows —", target_model)
                loaded = None
        else:
            loaded = next((m for m in models if m.get("state") == "loaded"), None)
        if loaded is None:
            logger.warning("[control] engine probe: target model not loaded — panel shows —")
            return info
        info["model_id"] = loaded.get("id")
        pub = loaded.get("publisher")
        if info["model_id"] and pub:
            info["model_id"] = f"{info['model_id']} (pub {pub})"
        info["allotted"] = loaded.get("loaded_context_length")
        info["model_max"] = loaded.get("max_context_length")
        logger.info("[control] engine info: model_id set · allotted={} · model_max={}",
                    info["allotted"], info["model_max"])
    except Exception as exc:  # noqa: BLE001 — must never crash startup
        logger.warning("[control] engine probe failed ({}) — panel shows —", type(exc).__name__)
    return info


# ── Pipeline processors ─────────────────────────────────────────────────────────
# MuteGate + SpeakingTap moved to control_taps.py. The routes
# below operate on instances passed in by bot.py; the classes are imported under
# TYPE_CHECKING above only for annotations.


# ── HTML control page ──────────────────────────────────────────────────────────
# The page markup/CSS/JS lives in control_page.html (lifted out of this module so
# panel work is plain HTML/CSS/JS diffs, not Python-string diffs, with editor syntax
# highlighting). Read ONCE at
# import (same lifetime as the old module-level string literal); served verbatim
# by the "/" route below. HEARTH_DEV_RELOAD=1 makes that read per-request instead,
# so panel edits land on a refresh rather than a bot restart (see ui/pages.py).
# The companion switcher itself is SHARED with the facade's launch page — one
# source file (ui/switch_card.js), spliced into both at import, so the two
# surfaces offer the same fields and read live-vs-restart the same way.
# The brand layer (palette + mark + favicon) is shared the same way, so the
# panel and the facade cannot drift into two visual languages; the artwork is
# served from /ui/brand/ rather than inlined, which took 12.7 KB of base64 out
# of this page.
# The page's OWN skin and its three self-gating sections (status meters, hot
# knobs, manual pane) are four more files under ui/ — see ui/panel.py, which
# also carries the one ordering rule between them. Those are the panel's alone;
# unlike the three above, no other page may splice them.
_HTML = pages.Page(Path(__file__).parent / "control_page.html",
                   pages.chain(panel.splice, switch_card.splice, brand.splice))


# ── Route handlers ─────────────────────────────────────────────────────────────

def _make_routes(
    worker: "PipelineWorker",
    context: "LLMContext",
    mute_gate: MuteGate,
    speaking_tap: SpeakingTap,
    meter: "TokenMeter",
    engine_info: dict,
    recorder: "Recorder",
) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_req: web.Request) -> web.Response:
        return web.Response(text=_HTML(), content_type="text/html")

    @routes.post("/say")
    async def say(req: web.Request) -> web.Response:
        try:
            body = await req.json()
            text = str(body.get("text", "")).strip()
            if not text:
                return web.json_response({"ok": False, "error": "empty text"}, status=400)
            context.add_message({"role": "user", "content": text})
            barge = speaking_tap.is_speaking
            if barge:
                await worker.queue_frames([InterruptionFrame(), LLMRunFrame()])
            else:
                await worker.queue_frames([LLMRunFrame()])
            logger.info("[control] /say ({}): {!r}",
                        "text-barge-in" if barge else "text turn", text[:80])
            return web.json_response({"ok": True})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post("/mute")
    async def mute(req: web.Request) -> web.Response:
        try:
            body = await req.json()
            mute_gate.set_muted(bool(body.get("muted", False)))
            logger.info("[control] /mute → mic {}", "MUTED" if mute_gate.is_muted else "LIVE")
            return web.json_response({"ok": True, "muted": mute_gate.is_muted})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post("/ptt")
    async def ptt(req: web.Request) -> web.Response:
        try:
            body = await req.json()
            down = bool(body.get("down", False))
            if down:
                mute_gate.ptt_press()    # open mic, remembering the latched baseline
            else:
                mute_gate.ptt_release()  # restore the baseline (not a hard-mute)
            logger.info("[control] /ptt {} → mic {}",
                        "down" if down else "up", "MUTED" if mute_gate.is_muted else "LIVE")
            return web.json_response({"ok": True, "muted": mute_gate.is_muted})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post("/record/start")
    async def record_start(req: web.Request) -> web.Response:
        try:
            body = await req.json()
            res = await recorder.start(
                name=str(body.get("name") or "").strip() or None,
                mic=bool(body.get("mic", False)),
                music=bool(body.get("music", False)),
            )
            return web.json_response(res, status=200 if res.get("ok") else 409)
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post("/record/stop")
    async def record_stop(_req: web.Request) -> web.Response:
        try:
            res = await recorder.stop()
            return web.json_response(res, status=200 if res.get("ok") else 409)
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.get("/record/status")
    async def record_status(_req: web.Request) -> web.Response:
        return web.json_response(recorder.status())

    @routes.get("/usage")
    async def usage(_req: web.Request) -> web.Response:
        return web.json_response(meter.snapshot())

    @routes.get("/engine")
    async def engine(_req: web.Request) -> web.Response:
        # Served by reference: bot.py re-polls LM Studio into this same dict on a
        # slow cadence, so the facts track mid-run model swaps. The
        # page fetches on load + every 60 s.
        return web.json_response(engine_info)

    return routes


# ── App factory + lifecycle ────────────────────────────────────────────────────

def build_web_app(
    worker: "PipelineWorker",
    context: "LLMContext",
    mute_gate: MuteGate,
    speaking_tap: SpeakingTap,
    meter: "TokenMeter",
    engine_info: dict,
    recorder: "Recorder",
) -> web.Application:
    app = web.Application()
    app.add_routes(_make_routes(worker, context, mute_gate, speaking_tap, meter, engine_info, recorder))
    brand.add_routes(app)  # /ui/brand/*.png — the shared mark and favicon
    # Extension seam: feature modules (volume, config knobs, …) contribute their own
    # routes via control_routes.register — they plug in HERE with zero edits to this
    # file. Empty until a feature module is imported → byte-identical to core-only.
    panel = control_routes.PanelContext(
        worker, context, mute_gate, speaking_tap, meter, engine_info, recorder
    )
    for contribute in control_routes.contributors():
        app.add_routes(contribute(panel))
    return app


async def start_web_server(
    worker: "PipelineWorker",
    context: "LLMContext",
    mute_gate: MuteGate,
    speaking_tap: SpeakingTap,
    meter: "TokenMeter",
    engine_info: dict,
    recorder: "Recorder",
) -> web.AppRunner:
    """Start TCPSite and return the runner (caller must call runner.cleanup() on exit)."""
    app = build_web_app(worker, context, mute_gate, speaking_tap, meter, engine_info, recorder)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
    await site.start()
    print(f"[control] web box → http://{WEB_HOST}:{WEB_PORT}/", flush=True)
    return runner
