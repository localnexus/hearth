"""features/audio_route.py — which audio route this sitting is on, read-only.

DROP-IN (the panel-extension seam, same shape as presence): bot.py imports this
module; registration is the import side effect, control.py takes zero edits.

One route per sitting, fixed at Start. The desk's story and the remote
device's are genuinely different — the desk can wait out a lost device
indefinitely, a phone is a countdown — so the object says which is in force
rather than reporting both with one word:

    GET /route → {kind, device, state, path, buffer_ms, shed_ms, grace_left}

    kind       "desk" | "remote"
    device     the enrolled device's id, or null on the desk
    state      desk:   "pinned" | "recovered" | "lost" | "unpinned"
               remote: "waiting" (no device yet) | "connected" | "lost" |
                       "ended" (the wait ran out; the sitting is closing)
    path       remote only: "local" | "direct" | "relayed" | "unknown" — how
               the device reached us, which is what sets the buffer depth below
    buffer_ms  the depth the device was told to hold at its last hello
    shed_ms    audio dropped on the way out because the far end fell behind
    grace_left seconds left of the wait: for a device that is away (state
               "lost"), or for one that has not arrived yet (state "waiting",
               the start wait) — null otherwise, and null always on the desk,
               which waits for its headset for as long as it takes

**Names and numbers only.** No address, no access key, no audio, nothing said.
The launch page reads it through the facade's ``/admin/state`` as ``bot.route``;
the bot is the only thing that knows any of it, because the transport that
holds it lives in this process.

Before ``attach`` — and on any route that has nothing to say — the object is
the desk's resting shape, so a reader always sees the same seven keys.
"""

from __future__ import annotations

from aiohttp import web

from hearth.control.control_routes import PanelContext, register

#: A callable returning the route dict; bot.py's build_pipeline hands it over.
_SOURCE = None

_RESTING = {"kind": "desk", "device": None, "state": "pinned", "path": None,
            "buffer_ms": None, "shed_ms": 0, "grace_left": None}


def attach(source) -> None:
    """build_pipeline hands over the live transport's own reporter (one call).
    ``None`` detaches."""
    global _SOURCE
    _SOURCE = source


def snapshot() -> dict:
    """The route object — pure, so it is testable without a server.

    A reporter that raises is answered with the resting shape rather than a
    500: a status route that can take the panel down with it is worse than one
    that occasionally says less than it knows.
    """
    if _SOURCE is None:
        return dict(_RESTING)
    try:
        state = _SOURCE()
    except Exception:  # noqa: BLE001 — a reporter must never break the panel
        return dict(_RESTING)
    if not isinstance(state, dict):
        return dict(_RESTING)
    out = dict(_RESTING)
    out.update({k: v for k, v in state.items() if k in _RESTING})
    return out


@register
def audio_route_routes(ctx: PanelContext) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/route")
    async def route(_req: web.Request) -> web.Response:
        return web.json_response(snapshot())

    return routes
