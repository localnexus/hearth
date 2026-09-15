"""device_pin.py — recovery re-opens only onto the same device identity; if it
is not back, stay silent and say so. PortAudio has no UIDs and its indices
renumber as devices come and go, so a device's NAME is the only key PortAudio
and CoreAudio share — it is the bridge from a CoreAudio pin back to a live
PortAudio index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger


@dataclass(frozen=True)
class DevicePin:
    uid: str
    name: str
    direction: str  # "in" | "out"
    rate: Optional[int]


async def pin_default(direction: str) -> Optional[DevicePin]:
    from hearth.recording.recording import run_route

    res = await run_route("devices")
    if res is None or not res.get("ok"):
        logger.info(f"[audio] {direction} device not pinned — recovery unavailable this session")
        return None

    long_direction = "input" if direction == "in" else "output"
    uid = res.get(f"default_{long_direction}_uid")
    if not uid:
        logger.info(f"[audio] {direction} device not pinned — recovery unavailable this session")
        return None

    entry = next((d for d in res.get("devices", []) if d.get("uid") == uid), None)
    if entry is None:
        logger.info(f"[audio] {direction} device not pinned — recovery unavailable this session")
        return None

    pin = DevicePin(uid=uid, name=entry["name"], direction=direction, rate=entry.get("rate"))
    logger.info(f"[audio] pinned {direction} device: {pin.name}")
    return pin


def resolve_index(pa, pin: DevicePin) -> Optional[int]:
    candidates = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["name"] != pin.name:
            continue
        has_channels = (
            info["maxInputChannels"] > 0
            if pin.direction == "in"
            else info["maxOutputChannels"] > 0
        )
        if has_channels:
            candidates.append(info["index"])

    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        logger.warning(f"[audio] ambiguous name match for {pin.name!r}: {len(candidates)} candidates")
    return None


async def uid_present(uid: str) -> Optional[bool]:
    from hearth.recording.recording import run_route

    res = await run_route("devices")
    if res is None or not res.get("ok"):
        return None
    return any(d.get("uid") == uid for d in res.get("devices", []))
