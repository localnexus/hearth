"""
control_routes.py — route registry for the web control panel (the panel-extension seam).

control.py is STABLE CORE: its built-in routes (/, /say, /mute,
/ptt, /record/*, /usage, /engine) don't change per feature. NEW panel features —
volume, hot knobs, voice swap, … — register their own
routes HERE so control.py takes ZERO edits per feature.

A feature module defines a contributor `(PanelContext) -> web.RouteTableDef` and
decorates it with @register; importing that module (from bot.py's feature list)
runs the registration. With no feature module imported, contributors() is empty
and the panel is byte-identical to the core-only app.

Pattern — M2 volume (illustrative):

    # features/volume.py   ← ALL the new code lives here
    from aiohttp import web
    from control_routes import register, PanelContext

    @register
    def volume_routes(ctx: PanelContext) -> web.RouteTableDef:
        routes = web.RouteTableDef()
        @routes.post("/volume")
        async def volume(req: web.Request) -> web.Response:
            body = await req.json()
            ...  # use ctx.worker / ctx.recorder / etc.
            return web.json_response({"ok": True})
        return routes

    # bot.py — activate by importing the module (import side effect = registration):
    #     import features.volume  # noqa: F401
    # control.py needs NO change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List

if TYPE_CHECKING:
    from aiohttp import web
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from hearth.control.control_taps import MuteGate, SpeakingTap
    from hearth.recording.recording import Recorder
    from hearth.session.token_meter import TokenMeter


@dataclass(frozen=True)
class PanelContext:
    """The shared dependencies handed to every route contributor.

    Bundles exactly what control.py's core routes already close over, so a feature
    route reaches the same live objects (worker, conversation context, mute gate,
    speaking tap, token meter, static engine facts, recorder) without control.py
    having to thread new arguments through for each feature.
    """

    worker: "PipelineWorker"
    context: "LLMContext"
    mute_gate: "MuteGate"
    speaking_tap: "SpeakingTap"
    meter: "TokenMeter"
    engine_info: dict
    recorder: "Recorder"


Contributor = Callable[["PanelContext"], "web.RouteTableDef"]
_CONTRIBUTORS: List[Contributor] = []


def register(fn: Contributor) -> Contributor:
    """Decorator: register a feature's route contributor (idempotent per function)."""
    if fn not in _CONTRIBUTORS:
        _CONTRIBUTORS.append(fn)
    return fn


def contributors() -> list[Contributor]:
    """The registered contributors, in registration order (copy — callers can't mutate)."""
    return list(_CONTRIBUTORS)
