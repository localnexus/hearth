"""audio/remote_path.py — who is on the socket, how far away, and how deep the
buffer has to be for them.

Everything here is decided ONCE, at the hello, and never again for the life of
a connection. It is split out of ``remote_transport`` because none of it needs
pipecat, a socket, or a running loop: given a peer address and a
``tailscale status --json`` document, the answers are pure functions, and a
test can drive every one of them without a network.

Three questions, in the order the hello asks them:

1. **Is this the device the sitting was started for, with the key?** The first
   frame is a text frame ``{"hello": {"device": <id>, "token": <key>}}``. The
   key is compared in constant time against the one in
   ``HEARTH_DATA/config/serve-token``; the id against the sitting's own. Any
   failure is one word — ``refused`` — and never says which half was wrong.

2. **Which way did they come?** The socket arrives through ``tailscale serve``,
   so the peer's real address is in ``X-Forwarded-For``. A peer whose overlay
   address matches and that has a ``CurAddr`` is **direct**; one without is
   **relayed**; a peer we cannot find, or a status document we cannot read, is
   **unknown** — three words, never a guess dressed as a fourth.

3. **How much jitter must the far end absorb?** Measured 2026-09-15: a direct
   hop needed up to 35 ms across the room and up to 107 ms on a hotspot; a
   relayed one needed 141–188 ms. So **direct 60 ms, relayed or unknown
   200 ms**, both overridable by environment. The buffer DROPS when it
   overflows, which is why 60 is a reasonable bet rather than a promise.
"""

from __future__ import annotations

import hmac
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

DIRECT = "direct"
RELAYED = "relayed"
UNKNOWN = "unknown"

#: Leans from the 2026-09-15 measurements; environment wins where it speaks.
DEFAULT_DIRECT_MS = 60
DEFAULT_RELAYED_MS = 200

#: Where ``tailscale`` lives on this machine, in the order we look.
TAILSCALE_PATHS = ("/usr/local/bin/tailscale",
                   "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
TAILSCALE_TIMEOUT_S = 3.0

#: The close code and the single word a refused client is told.
REFUSED_CODE = 4401
REFUSED_REASON = "refused"


# ── the key ───────────────────────────────────────────────────────────────────

def serve_token_path() -> Path:
    """``HEARTH_DATA/config/serve-token`` — the path, never the value."""
    from hearth.config import config_loader  # lazy: reads the data root

    return config_loader.CONFIG_DIR / "serve-token"


def read_serve_token() -> str | None:
    """The access key, read in process. ``None`` when the file is absent or
    empty — the caller refuses to start rather than running an ungated socket.

    The value is returned, never logged, never put in a reply, and never
    carried anywhere but the constant-time compare below.
    """
    try:
        token = serve_token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


# ── the hello ─────────────────────────────────────────────────────────────────

def parse_hello(message) -> dict | None:
    """The first frame → the hello object, or ``None`` if it is not one.

    A binary first frame, malformed JSON, a JSON document that is not an
    object, or an object without a ``hello`` object inside it all answer
    ``None``: audio before the hello is not audio, it is a stranger.
    """
    if isinstance(message, (bytes, bytearray)):
        return None
    try:
        doc = json.loads(message)
    except (TypeError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    hello = doc.get("hello")
    return hello if isinstance(hello, dict) else None


def hello_accepted(hello: dict | None, *, device_id: str, token: str) -> bool:
    """True iff this hello is the sitting's own device carrying the key.

    Both halves compare in constant time, and both are compared every time —
    no early return on the id, so the answer's timing says nothing about which
    half failed either.
    """
    if not hello:
        return False
    supplied_device = hello.get("device")
    supplied_token = hello.get("token")
    if not isinstance(supplied_device, str) or not isinstance(supplied_token, str):
        return False
    device_ok = hmac.compare_digest(supplied_device.encode(), str(device_id).encode())
    token_ok = hmac.compare_digest(supplied_token.encode(), str(token).encode())
    return device_ok and token_ok


# ── the path ──────────────────────────────────────────────────────────────────

def peer_address(headers, remote_address) -> str | None:
    """The far end's real address.

    ``tailscale serve`` proxies the socket, so ``remote_address`` is the
    loopback side of the proxy and the true peer is the first hop in
    ``X-Forwarded-For``. Fall back to ``remote_address`` for a direct bind
    (a test, or a run without the serve line).
    """
    forwarded = ""
    try:
        forwarded = str((headers or {}).get("X-Forwarded-For") or "")
    except (AttributeError, TypeError):
        forwarded = ""
    if forwarded.strip():
        return forwarded.split(",")[0].strip() or None
    if isinstance(remote_address, (tuple, list)) and remote_address:
        return str(remote_address[0])
    if isinstance(remote_address, str) and remote_address:
        return remote_address
    return None


def tailscale_binary() -> str | None:
    """The first ``tailscale`` that exists, or ``None``."""
    for candidate in TAILSCALE_PATHS:
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("tailscale")


def tailscale_status() -> dict | None:
    """``tailscale status --json`` as a dict, or ``None``.

    Bounded, never raises, and never reads anything else: a missing binary, a
    timeout, a non-zero exit and unreadable output all answer ``None``, which
    the classifier turns into ``unknown`` and a deeper buffer.
    """
    binary = tailscale_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, "status", "--json"],
                              capture_output=True, timeout=TAILSCALE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        doc = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def classify(address: str | None, status: dict | None) -> str:
    """``(peer address, status document)`` → ``direct`` | ``relayed`` | ``unknown``.

    The rule is the one the survey measured: find the peer whose
    ``TailscaleIPs`` carry this address; a populated ``CurAddr`` means the
    packets go straight there, an empty one means they go through a relay.
    A peer we cannot find is ``unknown`` — not ``direct`` — because the
    expensive mistake is a shallow buffer on a long path.
    """
    if not address or not isinstance(status, dict):
        return UNKNOWN
    peers = status.get("Peer")
    candidates = list(peers.values()) if isinstance(peers, dict) else []
    myself = status.get("Self")
    if isinstance(myself, dict):
        candidates.append(myself)
    for peer in candidates:
        if not isinstance(peer, dict):
            continue
        ips = peer.get("TailscaleIPs")
        if not isinstance(ips, (list, tuple)):
            continue
        if address not in [str(ip) for ip in ips]:
            continue
        return DIRECT if str(peer.get("CurAddr") or "").strip() else RELAYED
    return UNKNOWN


def self_host_names(status: dict | None = None) -> list:
    """This machine's own names, best first.

    The page the socket must accept is served by this same machine, so the
    origins to allow are ITS names — looked up rather than written down, so
    nothing here has to be edited when a machine or a network is renamed.
    The overlay network knows the full name; the operating system knows the
    short one; both are offered.
    """
    names = []
    doc = status if status is not None else tailscale_status()
    myself = (doc or {}).get("Self")
    if isinstance(myself, dict):
        dns = str(myself.get("DNSName") or "").strip().rstrip(".")
        if dns:
            names.append(dns)
    try:
        host = socket.gethostname().strip().rstrip(".")
    except OSError:
        host = ""
    for candidate in (host, host.split(".")[0] if host else ""):
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def facade_port() -> int:
    """The port the page itself is served on (``HEARTH_FACADE_PORT``, else
    65001) — the origins to allow are that page's, on that port."""
    raw = os.environ.get("HEARTH_FACADE_PORT", "").strip()
    try:
        port = int(raw)
    except ValueError:
        return 65001
    return port if 1 <= port <= 65535 else 65001


def facade_origins(status: dict | None = None) -> list:
    """Every address this machine's own page can be opened at, both schemes.

    A browser sends ``Origin`` on a WebSocket handshake and the transport
    refuses one that is missing or not on this list. Both schemes are offered
    because the plain-HTTP address is the fallback while certificates are off,
    and an origin that cannot open the socket looks like a broken page rather
    than a refusal.
    """
    port = facade_port()
    out = []
    for name in self_host_names(status):
        for scheme in ("https", "http"):
            out.append(f"{scheme}://{name}:{port}")
    return out


def buffer_ms(path: str) -> int:
    """The far end's ring-buffer depth for this path, in milliseconds.

    ``unknown`` is charged the relayed depth: not knowing is a reason to be
    generous, not a reason to guess cheap.
    """
    direct = _env_ms("HEARTH_AUDIO_BUFFER_DIRECT_MS", DEFAULT_DIRECT_MS)
    relayed = _env_ms("HEARTH_AUDIO_BUFFER_RELAYED_MS", DEFAULT_RELAYED_MS)
    return direct if path == DIRECT else relayed


def _env_ms(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        return fallback
    return value if value > 0 else fallback


def describe(address: str | None, status: dict | None) -> tuple[str, int]:
    """``(path, buffer_ms)`` — the pair the hello answer and the log line want."""
    path = classify(address, status)
    return path, buffer_ms(path)
