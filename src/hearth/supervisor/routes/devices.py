"""routes/devices.py — the paired devices: listing them, and forgetting one.

Pairing enrols a row (entry.py's claim); this file is everything that READS
that row afterwards and the one verb that takes it away.

  GET  /admin/devices          the list, for the launch page's selector
  POST /admin/devices/forget   preview-then-confirm removal, the shape the
                               models card already uses

Two helpers live here rather than in the two start doors, because both doors
need them and neither should grow a second copy:

  `unknown_device(route)` — a conversation asked to start on a device nobody
  paired is a 400 that says so, not a conversation that waits for ever for a
  device that does not exist. This is the ONLY place the registry gates
  anything: the socket's own check is the hello, which compares the id the
  conversation was started for against the id on the wire.

  `note_started(route)` — one `last_seen` stamp per conversation, after the
  child is up. A write that fails is a log line and nothing else; a
  conversation is not worth failing over a timestamp.

One part of the /admin surface; the package __init__ carries the map of the
whole, mounts the routes, and re-exports every name defined here.
"""

from __future__ import annotations

import asyncio

from aiohttp import web
from loguru import logger

from hearth.audio import devices as devices_mod
from hearth.audio import route as audio_route

#: What the second press has to say, the same word the models card asks for.
CONFIRM = 'press Confirm (or repost with {"yes": true})'


def unknown_device(route: str | None) -> str | None:
    """The sentence to refuse a start with, or None when the route is fine.

    The desk is always fine. A remote route names a device, and a device that
    was never paired has no row, no label, and nothing that could ever answer
    the socket.
    """
    if not route or not audio_route.valid(route):
        return None          # not this check's business — the grammar is checked first
    device_id = audio_route.device_of(route)
    if device_id is None or devices_mod.get(device_id) is not None:
        return None
    return (f"no paired device {device_id} — pair it first (/admin/pair/ui)")


def note_started(route: str | None) -> None:
    """Stamp `last_seen` for the device this conversation was started on."""
    if not route or not audio_route.valid(route):
        return
    device_id = audio_route.device_of(route)
    if device_id is None:
        return
    try:
        devices_mod.touch(device_id)
    except OSError as exc:
        logger.warning("[supervisor] device registry write failed ({})",
                       type(exc).__name__)


def _running_on(app: web.Application) -> tuple[str | None, str | None]:
    """(the device id a live conversation is using, its route word), or (None, None)."""
    status = app["bot_child"].status()
    if status.get("state") not in ("starting", "running"):
        return None, None
    word = (status.get("switches") or {}).get("route")
    if not isinstance(word, str) or not audio_route.valid(word):
        return None, None
    return audio_route.device_of(word), word


async def _devices_get(request: web.Request) -> web.Response:
    """Every paired device — id, label, and the two stamps. Names only."""
    return web.json_response(
        {"devices": await asyncio.to_thread(devices_mod.listed)})


async def _device_forget(request: web.Request) -> web.Response:
    """Remove one device's row. Preview first, then a confirmed press.

    Three refusals, in the order they matter: a body with no id, an id nobody
    paired, and — the one worth a sentence — the device a live conversation is
    speaking on. That last one is a 409 rather than a silent success because
    forgetting it would leave a conversation running on a device the page can
    no longer name.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is a missing id
        body = {}
    device_id = str(body.get("id") or "").strip()
    if not device_id:
        return web.json_response({"ok": False, "error": "id required"}, status=400)
    device = await asyncio.to_thread(devices_mod.get, device_id)
    if device is None:
        return web.json_response(
            {"ok": False, "error": f"no paired device {device_id}"}, status=404)
    await request.app["bot_child"].reconcile()
    live_id, live_word = _running_on(request.app)
    if live_id == device_id:
        return web.json_response(
            {"ok": False, "route": live_word,
             "error": f"{device.label} is where the conversation that is "
                      "running right now listens and speaks — stop it first"},
            status=409)
    if not bool(body.get("yes")):
        return web.json_response({
            "ok": True, "forgotten": False, "device": device.as_dict(),
            "archives": str(devices_mod.archive_name(devices_mod.devices_toml())),
            "confirm": f"this removes {device.label} from the list (the file is "
                       "copied beside itself first) and nothing else — " + CONFIRM,
        })
    try:
        archived = await asyncio.to_thread(devices_mod.forget, device_id)
    except OSError as exc:
        return web.json_response(
            {"ok": False,
             "error": f"the device list could not be written ({type(exc).__name__})"},
            status=409)
    logger.info("[supervisor] device forgotten ({})", device_id)
    return web.json_response({"ok": True, "forgotten": True,
                              "device": device.as_dict(),
                              "archived": str(archived) if archived else None})
