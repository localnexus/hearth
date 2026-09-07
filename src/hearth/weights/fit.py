"""weights/fit.py — will these weights fit on this machine?

Three pure functions and one probe:

  kv_bytes(header, ctx)   the KV cache at a context length, from the header.
                          The correction that matters: a hybrid-attention build
                          declares `full_attention_interval`, and only every
                          Nth block carries a KV cache at all. On the
                          incumbent (41 blocks, 1 of them the MTP layer,
                          interval 4) that is 10 attention layers, not 40 —
                          5.4 GB at 262 144 rather than 21.5. Context is cheap
                          on this family; a formula that ignores the interval
                          overstates it by 4x.
  estimate(...)           weights + projector + KV + a 1 GiB margin (the same
                          margin the door's own --fit keeps per device).
  machine_budget()        what this machine has to spend. Hearth's OWN door
                          binary is allowed to be asked (`--list-devices`
                          prints the Metal working set) — it is not a product,
                          it is the thing Hearth serves through. With no door
                          binary in sight the fallback is `sysctl hw.memsize`
                          times the share Metal will hand out.
  verdict(...)            "fits" · "fits at ctx N" · "too large".

The estimate is for the DISPLAY. The door's `--fit` is what guarantees a load
never fails on memory, and `/props` after load is the truth.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass

from .header import HeaderFacts

GIB = 1024 ** 3

#: The door keeps a 1024 MiB margin per device; the estimate keeps the same.
MARGIN_BYTES = 1 * GIB

#: What the speech stages hold while a companion is up (STT + TTS, measured;
#: docs/HARDWARE-REQUIREMENTS.md). Subtracted from the working set because the
#: model server never gets the machine to itself in Hearth.
RESERVE_BYTES = int(4.6 * 1000 ** 3)

#: The share of unified memory Metal will hand to one process, by machine size
#: (Norma's G0 figures: ~75 % on 32–128 GB machines, ~93 % on the 512 GB box).
SMALL_MACHINE_SHARE = 0.75
LARGE_MACHINE_SHARE = 0.93
LARGE_MACHINE_FLOOR = 128 * GIB

#: The smallest context worth offering — the door's own `--fit-ctx` default.
MIN_CTX = 4096

#: `MTL0: Apple M3 Ultra (475136 MiB, 475135 MiB free)`
DEVICE_LINE_RE = re.compile(r"\((?P<total>\d+)\s*MiB,\s*(?P<free>\d+)\s*MiB free\)")


@dataclass(frozen=True)
class Estimate:
    weights_bytes: int
    mmproj_bytes: int
    kv_bytes: int
    margin_bytes: int
    total: int
    ctx: int


@dataclass(frozen=True)
class Budget:
    total_bytes: int
    working_set_bytes: int
    reserve_bytes: int
    available: int
    source: str


def attention_layers(header: HeaderFacts) -> int | None:
    """How many blocks actually hold a KV cache."""
    if header is None or header.block_count is None:
        return None
    blocks = header.block_count - (header.nextn_predict_layers or 0)
    if blocks <= 0:
        return None
    interval = header.full_attention_interval
    if interval:
        return math.ceil(blocks / interval)
    return blocks


def kv_bytes(header: HeaderFacts, ctx: int, cache_bytes: int = 2) -> int:
    """KV cache bytes at `ctx`. `cache_bytes` is per element (f16 = 2)."""
    layers = attention_layers(header)
    if layers is None or not header.head_count_kv:
        return 0
    key_len = header.key_length or 0
    value_len = header.value_length or 0
    head_dim = (key_len + value_len) / 2
    return int(layers * ctx * 2 * header.head_count_kv * head_dim * cache_bytes)


def estimate(candidate, ctx: int, mmproj=None, cache_bytes: int = 2) -> Estimate:
    """Footprint of loading `candidate` at `ctx`, projector included."""
    header = getattr(candidate, "header", None)
    weights = header.tensor_bytes if header and header.tensor_bytes else candidate.size_bytes
    projector = mmproj.size_bytes if mmproj is not None else 0
    kv = kv_bytes(header, ctx, cache_bytes) if header else 0
    return Estimate(weights_bytes=int(weights), mmproj_bytes=int(projector),
                    kv_bytes=int(kv), margin_bytes=MARGIN_BYTES,
                    total=int(weights) + int(projector) + int(kv) + MARGIN_BYTES,
                    ctx=int(ctx))


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "") + (done.stderr or "")


def _memsize() -> int:
    out = _run(["sysctl", "-n", "hw.memsize"]).strip()
    try:
        return int(out.splitlines()[0])
    except (IndexError, ValueError):
        return 0


def parse_devices(text: str) -> int:
    """The largest device total, in bytes, from `llama-server --list-devices`."""
    totals = [int(m.group("total")) for m in DEVICE_LINE_RE.finditer(text)]
    return max(totals) * 1024 * 1024 if totals else 0


def machine_budget(llama_server: str | None = None) -> Budget:
    """What one model may occupy here, once the speech stages are reserved."""
    total = _memsize()
    working = 0
    source = ""
    if llama_server:
        working = parse_devices(_run([llama_server, "--list-devices"]))
        if working:
            source = f"{llama_server} --list-devices"
    if not working:
        share = LARGE_MACHINE_SHARE if total >= LARGE_MACHINE_FLOOR else SMALL_MACHINE_SHARE
        working = int(total * share)
        source = f"sysctl hw.memsize x {share}"
    return Budget(total_bytes=total, working_set_bytes=working,
                  reserve_bytes=RESERVE_BYTES,
                  available=max(0, working - RESERVE_BYTES), source=source)


def verdict(est: Estimate, budget: Budget, header: HeaderFacts | None = None,
            cache_bytes: int = 2) -> str:
    """"fits" · "fits at ctx N" (the largest halving that does) · "too large"."""
    if budget.available <= 0:
        return "unknown"
    if est.total <= budget.available:
        return "fits"
    if header is None:
        return "too large"
    fixed = est.weights_bytes + est.mmproj_bytes + est.margin_bytes
    ctx = est.ctx // 2
    while ctx >= MIN_CTX:
        if fixed + kv_bytes(header, ctx, cache_bytes) <= budget.available:
            return f"fits at ctx {ctx}"
        ctx //= 2
    return "too large"
