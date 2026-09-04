"""features/turn_echo.py — the panel's last-reply echo: what she just said,
shown above the box you type into.

DROP-IN (the panel-extension seam, same shape as memory_status): bot.py imports
this module; registration is the import side effect, control.py takes zero
edits. Absent the import the panel is exactly what it was — the page's own
section self-gates on a 404 from this route.

**No tap, no storage, no new plumbing.** control.py already hands every route
contributor the live ``LLMContext``: the same object ``/say`` appends your typed
turn to, and the same one the assistant aggregator appends her reply to when a
turn closes. The echo is a read of state this process is already holding, which
is why it costs one route. A live companion switch keeps the object and calls
``set_messages`` on it (pipeline/switcher.py), so the echo follows a switch with
no push plumbing either.

**Whole-turn, not streaming — and it runs slightly AHEAD of her voice.** The
assistant message lands in the context when the aggregator closes the turn, i.e.
when the LLM finishes; the TTS is usually still speaking it. That is the honest
behaviour of this seam rather than a defect to paper over: token-at-a-time echo
needs a real frame tap and a streaming renderer, which is a larger build than a
status line and does not belong here.

**System messages are never returned.** The composed persona lives in the
context as ``role="system"``; the filter here is by role, allow-list side up,
which is what makes it structurally unable to hand one back.

API:
    GET /turn → {ok, seq, role, text, waiting}
        seq     — how many echoable messages the context holds. The page uses it
                  to tell "nothing new" from "she said the same thing twice".
        role    — "assistant" when her reply is the newest message, "user" when
                  yours is.
        text    — that message's text (see _flatten).
        waiting — role == "user": she has not answered yet.
"""

from __future__ import annotations

from aiohttp import web

from hearth.control.control_routes import PanelContext, register

#: The roles the panel echoes. "system" is absent deliberately — see above.
_ECHOED = ("assistant", "user")


def _flatten(content: object) -> str:
    """The message's text, whatever content shape it arrived in.

    Content is either a plain string or a list of parts. Only ``type == "text"``
    parts are read: the other kinds a turn can carry are binary — a base64
    ``input_audio`` blob from the mic path, an image — which is neither
    displayable nor something to move through a poll every couple of seconds.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict) and part.get("type") == "text")
    return ""


@register
def turn_echo_routes(ctx: PanelContext) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/turn")
    async def last_turn(_req: web.Request) -> web.Response:
        # LLMSpecificMessage entries are objects, not dicts, and belong to one
        # provider's wire format; isinstance is the filter that skips them.
        msgs = [m for m in ctx.context.get_messages()
                if isinstance(m, dict) and m.get("role") in _ECHOED]
        if not msgs:
            return web.json_response({"ok": True, "seq": 0, "role": None,
                                      "text": "", "waiting": False})
        last = msgs[-1]
        role = last.get("role")
        return web.json_response({"ok": True, "seq": len(msgs), "role": role,
                                  "text": _flatten(last.get("content")),
                                  "waiting": role == "user"})

    return routes
