"""firstrun/page.py — the read side: the shell, and the one GET the page walks.

The shell is auth-exempt and contentless like the other admin pages — the
sixth such door. Everything it shows arrives from /admin/first-run/state,
which is authed: the two detection facts, the selection, the model's name and
id, what the LLM server advertises right now, and the bot's process state (so
the page needs no second poll to watch a start land).

The server probe runs in a worker thread on a short timeout: this route is
polled while the page is open, and the server may well be down — saying so
plainly is one of the two things the page exists for.

One part of the /admin/first-run surface; the package __init__ carries the map
of the whole and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web

from hearth.ui import admin_shell, brand, firstrun_sections, pages, switch_card

from .detect import is_fresh, model_facts, selection

#: Short on purpose — the page polls this route every few seconds.
_PROBE_TIMEOUT_S = 2.5

# The page takes the shared switch card (Start is the card's verb, here as on
# the launch page and the panel), its own two sections, the admin shell and
# the brand layer — the same composition the launch page uses, plus sections.
_PAGE = pages.Page(Path(__file__).parent / "first_run_page.html",
                   pages.chain(switch_card.splice, firstrun_sections.splice,
                               admin_shell.splice, brand.splice))


async def _page(_req: web.Request) -> web.Response:
    """The first-run shell — static chrome (the serve middleware exempts
    exactly this path; see the page's security contract)."""
    return web.Response(text=_PAGE(), content_type="text/html")


def _advertised(url: str, token: str) -> list | None:
    """The ids the server lists, or None when nothing answers. The facade's
    own LM token rides the probe and never the response."""
    from hearth.init import probe_models  # lazy: mirrors the package gate idiom

    return probe_models(url, token, timeout=_PROBE_TIMEOUT_S)


async def _state(request: web.Request) -> web.Response:
    app = request.app
    deps = app["deps"]

    def _facts() -> dict:
        sel = selection()
        model = (model_facts(sel) if sel
                 else {"name": None, "id": None, "id_set": False})
        return {"selection": sel, "model": model, "fresh": is_fresh()}

    facts, models = await asyncio.gather(
        asyncio.to_thread(_facts),
        asyncio.to_thread(_advertised, deps.lm_base_url, deps.lm_token))
    await app["bot_child"].reconcile()  # process truth, like /admin/state
    needs_model = not facts["model"]["id_set"]
    return web.json_response({
        "first_run": needs_model or facts["fresh"],
        "needs_model": needs_model,
        **facts,
        "lm": {"url": deps.lm_base_url, "reachable": models is not None,
               "models": models},
        "bot": app["bot_child"].status(),
    })
