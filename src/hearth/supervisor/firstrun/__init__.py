"""supervisor/firstrun/ — /admin/first-run: the guided first sitting.

The second half of the first-run path (D1 of the panel-audiences note: built
FOR THE STRANGER). The bootstrap (python -m hearth.init) takes a bare checkout
to a facade with a door open; this page waits behind that door and walks the
rest — is the LLM server answering, which model does it actually serve, bring
the companion up, did it hear you — and ends by handing off to /admin/launch.
From then on the launch page is the front door and this one stops being
offered (it stays reachable).

Three steps, each honest about what it can and cannot check:

  1  THE SERVER AND THE MODEL. The facade probes its own LLM URL and lists
     the ids the server advertises; picking one records it in the selected
     model.toml through the bootstrap's own surgery (comment-preserving,
     parse-verified, .prev beside it). A server that does not answer is said
     plainly, with the one command that moves the URL (hearth.init --lm-url).
     This page writes no URL: the running facade would keep using the old one
     until restarted, and a setting that looks applied but is not is worse
     than a command.
  2  THE START. The shared switch card — the same file the launch page and the
     :65000 panel carry — with Start. Parked until step 1 is done: a bot
     started against the placeholder id cannot reach a model. The page names
     the macOS microphone prompt (it is addressed to the terminal running the
     facade) because that is the moment a first start looks stuck.
  3  THE FIRST WORDS. The bot's own counters, read through the facade's proxy:
     warming → listening → "heard you and answered". No transcript crosses —
     a turn count and the engine's names are all the page shows.

The ENTRY CONDITION lives on the launch page (ui/first_run_offer.js): while
the selected model's id is still the shipped placeholder, or no companion on
this install has a session yet, the launch page offers the walk — and while
the id is the placeholder it parks its own Start. /admin/state carries the two
facts (routes/state.py → detect()).

API (mounted iff [serve.supervisor] enabled):
    GET  /admin/first-run        → the page (static contentless shell,
                                   auth-exempt like /admin/launch; data authed)
    GET  /admin/first-run/state  → the two facts, the selection, model name +
                                   id, what the server advertises, bot state
    POST /admin/first-run/model  → {id, yes?}: record the id in the selected
                                   model.toml; an id the server does not
                                   advertise (or an unreachable server) refuses
                                   unless yes

── the package layout ───────────────────────────────────────────────────────
Imports run strictly downward this list, and first_run_page.html sits beside
page.py:

    detect.py   the two facts, read at call time: needs_model and fresh
    page.py     the read side — the shell and the state GET behind it
    model.py    the one write: recording the advertised id

This __init__ is the façade: it re-exports every name the parts define, so
``from hearth.supervisor import firstrun`` reaches all of them.
"""

from __future__ import annotations

from aiohttp import web

from .detect import detect, is_fresh, model_facts, model_path, selection
from .page import _PAGE, _PROBE_TIMEOUT_S, _advertised, _page, _state
from .model import _EFFECT, _MAX_ID_LEN, _model_post, _record

__all__ = ["add_routes", "detect"]


def add_routes(app: web.Application) -> None:
    """Called by routes.build_mount — same door, same middleware."""
    app.router.add_get("/admin/first-run", _page)
    app.router.add_get("/admin/first-run/state", _state)
    app.router.add_post("/admin/first-run/model", _model_post)
