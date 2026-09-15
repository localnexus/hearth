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

S2 (mouth, 2026-09-08): ``level`` + ``level_ts`` join the object —
the played voice's per-frame RMS from a LevelTap wired AFTER transport.output()
(playback time, not TTS time; see LevelTap's docstring for why). 0–1 float; 0.0
on its own once the audio stops, never a held value; before attach_level the
field is 0.0 so a reader always sees the shape.

API:
    GET /presence → {bot_speaking, user_speaking, muted, level, level_ts, lead_s, audio, ts}
        Sent with ``Access-Control-Allow-Origin: *`` — the reader is another
        app's webview, not this panel's page (see the route for why that is
        safe on this route and would not be on the POSTs).
        ts — this process's wall clock (time.time()) when the answer was
             composed; a reader that sees it stop advancing knows the bot is gone.

``lead_s`` — seconds queued in the output device but not yet played, read from
the stream on each poll; null until the stream is open or when it cannot be
read; a consumer delays its mouth by exactly this.

``audio`` — per direction (``input``, ``output``), whether the device the
session started on is ``ok``, ``lost`` (the companion stays silent; nothing is
re-routed to another device) or ``recovered``, together with that device's
NAME and the wall-clock time of the last state change. ``unpinned`` when no
device identity was available to pin at session start.
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


class LeadProbe:
    """Seconds of voice handed to the output device but not yet played, read
    from the open stream's free space on demand: capacity = the largest free
    space ever seen (an empty ring shows all of it), queued = capacity minus
    free now. Seconds, not frames; no clock or latency figure folded in — the
    path past the device is the consumer's per-route constant. None when
    nothing honest can be said: no stream yet, a stream that raises, a sample
    rate of 0. Never negative."""

    def __init__(self, output_transport) -> None:
        self._out = output_transport
        self._capacity = 0

    def lead_s(self):
        stream = getattr(self._out, "_out_stream", None)
        rate = int(getattr(self._out, "_sample_rate", 0) or 0)
        if stream is None or rate <= 0:
            return None
        try:
            free = int(stream.get_write_available())
        except Exception:
            return None
        if free < 0:
            return None
        if free > self._capacity:
            self._capacity = free
        return (self._capacity - free) / rate


_TAP: PresenceTap | None = None
_LEVEL = None   # LevelTap (control_taps) after transport.output(); None until attach_level
_LEAD = None   # LeadProbe bound to the output transport; None until attach_lead
_AUDIO = None   # the recovering transport; None until attach_audio

_UNPINNED = {
    "input": {"state": "unpinned", "device": None, "since": None, "uid": None},
    "output": {"state": "unpinned", "device": None, "since": None, "uid": None},
}


def attach(tap: PresenceTap) -> None:
    """bot.py's build_pipeline hands the wired tap over (one call)."""
    global _TAP
    _TAP = tap


def attach_level(tap) -> None:
    """bot.py hands over the played-audio LevelTap (S2). None detaches."""
    global _LEVEL
    _LEVEL = tap


def attach_lead(probe) -> None:
    """bot.py hands over the probe. None detaches."""
    global _LEAD
    _LEAD = probe


def attach_audio(transport) -> None:
    """bot.py hands over the transport whose halves know their device. None detaches."""
    global _AUDIO
    _AUDIO = transport


def snapshot(mute_gate, speaking_tap) -> dict:
    """The presence object — pure, so it is testable without a server."""
    muted = bool(mute_gate.is_muted)
    heard = bool(_TAP.user_speaking) if _TAP is not None else False
    level = float(_LEVEL.level()) if _LEVEL is not None else 0.0
    level_ts = float(_LEVEL.level_ts) if _LEVEL is not None else 0.0
    lead_s = _LEAD.lead_s() if _LEAD is not None else None
    audio = _AUDIO.audio_state() if _AUDIO is not None else {k: dict(v) for k, v in _UNPINNED.items()}
    return {
        "bot_speaking": bool(speaking_tap.is_speaking),
        "user_speaking": heard and not muted,
        "muted": muted,
        "level": level,
        "level_ts": level_ts,
        "lead_s": lead_s,
        "audio": audio,
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
