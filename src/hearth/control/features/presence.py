"""features/presence.py — the conversation's presence state, read-only, for
anything that sits beside Hearth and wants to react to the talk: a desk figure,
a status light, the panel itself, a future room participant.

DROP-IN (the panel-extension seam, same shape as memory_status): bot.py imports
this module; registration is the import side effect, control.py takes zero
edits. Absent the import the panel is exactly what it was.

Three booleans the pipeline already knows, composed into one object:

    bot_speaking   — the companion's voice is playing (SpeakingTap, after the
                     speaker output: BotStarted/StoppedSpeakingFrame).
    user_speaking  — the listener hears you (PresenceTap, wired IMMEDIATELY
                     AFTER the VAD so it sees VADUserStarted/StoppedSpeakingFrame
                     at their source, before turn-taking gets a say).
    muted          — the mic switch (MuteGate).

**No audio, no text, no persistence.** Nothing spoken passes through here in
either direction; the object is state only, and only the state a person in the
room could see anyway. A reader polls it (10 Hz is plenty; it is a dict read)
over the panel's loopback bind, so no new door opens.

Why a separate tap rather than folding into SpeakingTap: that one sits after
transport.output(), which is downstream of the VAD frames' useful position and
would report them late. Why the mute guard in the ROUTE and not by trust in the
gate alone: the gate drops audio, so a mute pressed mid-sentence starves the VAD
of the silence it needs to emit Stopped — the flag would stick true. The route
answers ``user_speaking = tap and not muted`` so the reader never sees a muted
mic reported as a speaking one.

Wiring: build_pipeline constructs ``PresenceTap()``, places it after ``vad``,
and calls ``attach(tap)``; until then the route reports user_speaking false.

API:
    GET /presence → {bot_speaking, user_speaking, muted, ts}
        Sent with ``Access-Control-Allow-Origin: *`` — the reader is another
        app's webview, not this panel's page (see the route for why that is
        safe on this route and would not be on the POSTs).
        ts — this process's wall clock (time.time()) when the answer was
             composed; a reader that sees it stop advancing knows the bot is gone.
"""

from __future__ import annotations

import time

from aiohttp import web
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from hearth.control.control_routes import PanelContext, register


class PresenceTap(FrameProcessor):
    """Flips ``user_speaking`` on the VAD's own frames; passes every frame
    through untouched (the measure-tap contract). Wire immediately after
    VADProcessor. Session boundaries (Start/End/Cancel) reset the flag so a
    stale true never outlives the sitting that set it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._user_speaking: bool = False

    @property
    def user_speaking(self) -> bool:
        return self._user_speaking

    def observe(self, frame: Frame) -> None:
        """The whole of the tap's logic, pure, so a test can drive it by hand."""
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
        elif isinstance(frame, (VADUserStoppedSpeakingFrame, StartFrame, EndFrame, CancelFrame)):
            self._user_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.observe(frame)
        await self.push_frame(frame, direction)


_TAP: PresenceTap | None = None


def attach(tap: PresenceTap) -> None:
    """bot.py's build_pipeline hands the wired tap over (one call)."""
    global _TAP
    _TAP = tap


def snapshot(mute_gate, speaking_tap) -> dict:
    """The presence object — pure, so it is testable without a server."""
    muted = bool(mute_gate.is_muted)
    heard = bool(_TAP.user_speaking) if _TAP is not None else False
    return {
        "bot_speaking": bool(speaking_tap.is_speaking),
        "user_speaking": heard and not muted,
        "muted": muted,
        "ts": time.time(),
    }


@register
def presence_routes(ctx: PanelContext) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/presence")
    async def presence(_req: web.Request) -> web.Response:
        # The one route on the box that is meant for a reader OUTSIDE the
        # panel's own page. A desk figure or overlay lives in its own app
        # origin (an app webview is not http://127.0.0.1), so without this
        # header the browser refuses it the body. Wildcard is safe here and
        # only here: GET, no credentials, no content — three booleans a person
        # in the room could see — on a loopback bind by default.
        return web.json_response(snapshot(ctx.mute_gate, ctx.speaking_tap),
                                 headers={"Access-Control-Allow-Origin": "*"})

    return routes
