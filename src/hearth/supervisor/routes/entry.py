"""routes/entry.py — getting through the one door from a browser or a phone.

Four things that all exist for the same reason: a browser cannot attach an
Authorization header to a navigation, and nobody types a 64-hex bearer into a
phone. So —

  the three SHELLS (launch, pairing, the phone's talk page) are static chrome
  the middleware exempts, carrying no names, no state and no token; every fact
  they show arrives by authed fetch afterwards — or, for the talk page, inside
  the first frame on its own socket;

  the COOKIE carrier is minted from the bearer (never the bearer itself),
  HttpOnly so no page script can read it back, and is what makes the proxied
  panel reachable by clicking rather than only by curl;

  PAIRING trades a six-digit code for the key, once. What keeps a short secret
  on an unauthed route honest: one code at a time, minutes of life, burned on
  first use, and burned again after three wrong guesses. A correct claim also
  ENROLS the device — a row in config/devices.toml with the label the person
  typed — which is what turns "a paired device" on the launch page from a text
  field into a list.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time
from pathlib import Path

from aiohttp import web
from loguru import logger

from hearth.audio import devices as devices_mod
from hearth.ui import (
    admin_shell, brand, compact_queue, first_run_offer, hearth_restart, key_help,
    launch_actuators, launch_models, launch_sections, launch_sessions, pages,
    switch_card)

# The standing launch surface: pure static chrome (no names, no state, no
# tokens baked in — the serve middleware exempts this ONE page from auth, so
# it must stay contentless; every fact it shows arrives via authed fetch).
#
# The companion switcher is SHARED with the control panel: one source file,
# spliced into both pages at import. A static route would have needed its own
# auth exemption (a <script src> cannot carry the bearer); splicing keeps the
# door count where it is and guarantees both surfaces run the same bytes.
#
# `launch_sections` is the other kind: the page's OWN four files (the poll, the
# deferred start, the route control, the Stop card), spliced back into its one
# <script> block. See ui/launch_sections.py.
_LAUNCH_PAGE = pages.Page(
    Path(__file__).parent / "launch_page.html",
    pages.chain(switch_card.splice, compact_queue.splice, first_run_offer.splice,
                key_help.splice, hearth_restart.splice, launch_actuators.splice,
                launch_models.splice, launch_sessions.splice,
                launch_sections.splice, admin_shell.splice, brand.splice))
# The pairing page takes neither shared script: it is what a device WITHOUT the
# bearer opens, so the admin shell has nothing to carry for it.
_PAIR_PAGE = pages.Page(Path(__file__).parent / "pair_page.html", brand.splice)
# The phone's talk page, for the remote audio route. Same posture as the two
# above and for the same reason — a browser cannot attach a header to a
# navigation — with one difference worth stating: it does not fetch anything
# from this door at all. It reads the key this browser already holds from
# pairing and spends it once, in the hello on the audio socket (:65021), which
# is a separate door with its own gate (audio/remote_transport.py).
_VOICE_PAGE = pages.Page(Path(__file__).parent / "voice_page.html", brand.splice)

# Device pairing. A 64-hex bearer is not something anyone types into a phone,
# and file transfer to a hardened handset is its own adventure — so the desk
# mints a six-digit code and the device trades it for the key, once.
#
# What keeps a six-digit secret on an UNAUTHED route honest: at most one code
# exists at a time, it exists only in the minutes after the operator asked for
# it, it is burned on first use, and THREE wrong guesses burn it too. An
# attacker who is already inside the trust boundary gets three tries in a
# million during a window the operator opened deliberately.
_PAIR_TTL_S = 300.0
_PAIR_MAX_TRIES = 3


async def _cookie(request: web.Request) -> web.Response:
    """Mint the browser carrier for this facade (authed by header, like every
    other admin POST).

    Why it exists: a browser navigating to the proxied control panel at ``/``
    cannot attach an Authorization header, so without this the panel is
    reachable by curl and by nothing a person clicks. The value is derived from
    the bearer, never the bearer itself.

    HttpOnly, so a page script can never read it back out (strictly better than
    the localStorage the page keeps the bearer in). SameSite=Lax, so it rides a
    top-level navigation but not a cross-site form post. No Secure flag: the
    facade speaks plain HTTP and the overlay network, not TLS, is what encrypts
    this hop — setting Secure would simply stop the cookie working.
    """
    from hearth.serve import app as serve_app  # lazy: serve.app mounts us

    resp = web.json_response({"ok": True})
    resp.set_cookie(
        serve_app.COOKIE_NAME, serve_app.cookie_value(request.app["deps"].bearer),
        max_age=30 * 24 * 3600, httponly=True, samesite="Lax", path="/",
    )
    return resp


async def _pair_mint(request: web.Request) -> web.Response:
    """Open a pairing window (authed — this is the desk asking).

    Replaces any code already outstanding: one device at a time, deliberately.
    """
    code = f"{secrets.randbelow(1000000):06d}"
    request.app["pair"] = {"code": code, "expires": time.monotonic() + _PAIR_TTL_S,
                           "tries": 0}
    logger.info("[supervisor] pairing window open for {}s", int(_PAIR_TTL_S))
    return web.json_response({"code": code, "expires_in": int(_PAIR_TTL_S)})


async def _pair_claim(request: web.Request) -> web.Response:
    """Trade a live code for the bearer, once (UNAUTHED — that is the point).

    Every failure answers the same way and says nothing about which part was
    wrong. A correct claim burns the code before returning; so does the third
    wrong guess. The body may also carry `label` (what to call this device) and
    `device_id` (what it already is, on a re-pair) — BOTH are read only after
    the code has been found correct, so nothing about them can be learned by
    guessing, and neither can change a refusal in any way, including its shape
    and the work it does.

    The answer carries the id the device is now known by. That id is what the
    talk page sends in its hello and what a route word names, so a device that
    paired before this registry existed needs exactly one re-pair.
    """
    pair = request.app["pair"]
    refused = web.json_response({"error": "that code was refused"}, status=401)
    if not pair["code"] or time.monotonic() > pair["expires"]:
        pair["code"] = ""
        return refused
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:  # noqa: BLE001 — a malformed body is just a bad claim
        body = {}
    supplied = str(body.get("code") or "")
    if not hmac.compare_digest(supplied.encode(), pair["code"].encode()):
        pair["tries"] += 1
        if pair["tries"] >= _PAIR_MAX_TRIES:
            pair["code"] = ""
            logger.warning("[supervisor] pairing code burned after {} wrong claims",
                           _PAIR_MAX_TRIES)
        return refused
    pair["code"] = ""       # single use, burned before the answer leaves
    label = body.get("label")
    wanted = body.get("device_id")
    device = None
    try:
        device = await asyncio.to_thread(
            devices_mod.enrol,
            str(label) if isinstance(label, str) else "device",
            str(wanted) if isinstance(wanted, str) else None)
    except OSError as exc:
        # The key is what the device came for, and a file that would not write
        # is not a reason to refuse it. Say so in the log and answer anyway.
        logger.warning("[supervisor] device registry write failed ({})",
                       type(exc).__name__)
    if device is not None:
        # The id, never the label: a label is the person's own words.
        logger.info("[supervisor] device paired ({})", device.id)
    else:
        logger.info("[supervisor] device paired")
    return web.json_response({
        "token": request.app["deps"].bearer,
        "device_id": device.id if device else None,
        "label": device.label if device else None,
    })


async def _pair_ui(request: web.Request) -> web.Response:
    """The pairing shell — static chrome, like the launch page."""
    return web.Response(text=_PAIR_PAGE(), content_type="text/html")


async def _voice(request: web.Request) -> web.Response:
    """The phone's talk page — static chrome, like the other two shells."""
    return web.Response(text=_VOICE_PAGE(), content_type="text/html")


async def _launch(request: web.Request) -> web.Response:
    """The standing launch surface — reachable bot-up AND bot-down (a
    registered route always beats the catch-all proxy). Static chrome only."""
    return web.Response(text=_LAUNCH_PAGE(), content_type="text/html")
