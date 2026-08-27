"""engine_probe_llamaserver.py — sibling engine-probe adapter for llama.cpp llama-server.

A SECOND backend for the panel's engine probe, provider-dispatched.
`control.py`'s `fetch_engine_info` (STABLE CORE)
probes LM Studio's proprietary `/api/v0/models`; this module probes llama-server's
native `/props`, returning the SAME dict contract so the panel and bot.py's slow
re-poll stay backend-agnostic.

Sanctioned seam: a new sibling module beside the stable core, provider dispatch
chosen by config, **zero** stable-core edits beyond
the one dispatch call site already in bot startup. control.py takes no edits.

Return contract (identical to control.fetch_engine_info):
    async fetch_engine_info(base_url, token, target_model=None) -> dict
    → {provider, model_id, allotted, model_max}; the three value keys are None on
      ANY failure (server down, 401, malformed body, timeout) so the panel degrades
      to '—'. Never raises; never blocks startup; never logs the token.

llama-server `/props` shape (verified against llama.cpp
`tools/server/README.md`, GET /props documented example):
    top-level:  default_generation_settings, total_slots, model_path, chat_template,
                chat_template_caps, modalities, media_marker, build_info, is_sleeping
    default_generation_settings.n_ctx  = the ALLOCATED (loaded) context  → 'allotted'
    model_path                         = the GGUF file path; its basename → 'model_id'
                                         (llama-server's `--alias` is exposed on
                                         /v1/models, NOT on /props, so the filename
                                         is the honest identifier /props gives us)
    ⚠ n_ctx_train is NOT exposed by /props today → 'model_max' stays None (panel
      shows '—'). Looked up defensively anyway (top-level + inside
      default_generation_settings) so a future build that adds it lights the field
      up with zero code change here.

Provider dispatch: `fetch_engine_info_for(provider, ...)` selects the backend by a
config-supplied provider string. Default ("lmstudio" / anything unrecognized) routes
to the existing LM Studio probe, so every v2-shaped install sees ZERO change;
provider == "llama-server" routes here.
"""

from __future__ import annotations

from pathlib import Path

import aiohttp
from loguru import logger

# ── Provider identifiers (config-facing) ─────────────────────────────────────────
PROVIDER_LMSTUDIO = "lmstudio"
PROVIDER_LLAMASERVER = "llama-server"
DEFAULT_PROVIDER = PROVIDER_LMSTUDIO
_LLAMASERVER_ALIASES = frozenset({"llama-server", "llamaserver", "llama.cpp", "llamacpp"})


# ── The llama-server /props probe (M1) ───────────────────────────────────────────

async def fetch_engine_info(base_url: str, token: str, target_model: str | None = None) -> dict:
    """One authenticated GET {host}/props → current engine facts, llama-server flavor.

    Same return contract as control.fetch_engine_info (provider="llama-server"). On
    ANY failure the value keys are None so the panel degrades to '—'. Never raises;
    never blocks startup; never logs the token.

    `base_url` is the OpenAI-compat base (…/v1); we strip a trailing /v1 to reach the
    native server root, then hit /props. `target_model` is accepted for contract
    parity but not gated on: llama-server serves ONE model per process (parity
    inventory S5), so there is no multi-model disambiguation to do — /props always
    describes the single loaded model.
    """
    info = {
        "provider": "llama-server",
        "model_id": None,
        "allotted": None,
        "model_max": None,
    }
    host = base_url.rstrip("/")
    if host.endswith("/v1"):
        host = host[: -len("/v1")]
    url = f"{host}/props"
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning("[probe] llama-server /props HTTP {} — panel shows —", resp.status)
                    return info
                body = await resp.json()
        if not isinstance(body, dict):
            logger.warning("[probe] llama-server /props: non-object body — panel shows —")
            return info
        gen = body.get("default_generation_settings")
        gen = gen if isinstance(gen, dict) else {}

        # model_id ← basename of the served GGUF path
        model_path = body.get("model_path")
        if isinstance(model_path, str) and model_path.strip():
            info["model_id"] = Path(model_path).name

        # allotted ← allocated/loaded context (default_generation_settings.n_ctx;
        # some builds also surface it top-level — accept either).
        n_ctx = gen.get("n_ctx")
        if n_ctx is None:
            n_ctx = body.get("n_ctx")
        if isinstance(n_ctx, int) and not isinstance(n_ctx, bool):
            info["allotted"] = n_ctx

        # model_max ← training context IF this build exposes it (README: not on /props
        # today). Defensive lookup so a future build lights it up for free; else None.
        n_train = body.get("n_ctx_train")
        if n_train is None:
            n_train = gen.get("n_ctx_train")
        if isinstance(n_train, int) and not isinstance(n_train, bool):
            info["model_max"] = n_train

        logger.info("[probe] llama-server engine info: model_id set · allotted={} · model_max={}",
                    info["allotted"], info["model_max"])
    except Exception as exc:  # noqa: BLE001 — must never crash startup
        logger.warning("[probe] llama-server /props failed ({}) — panel shows —", type(exc).__name__)
    return info


# ── Provider dispatch (the config-chosen selector) ───────────────────────────────

async def fetch_engine_info_for(
    provider: str | None, base_url: str, token: str, target_model: str | None = None
) -> dict:
    """Route the engine probe to the configured backend.

    `provider == "llama-server"` (or an obvious alias) → the /props probe above.
    Anything else, including the default "lmstudio", None, or an unrecognized value
    → the STABLE-CORE LM Studio probe (control.fetch_engine_info), preserving current
    behavior for every v2-shaped install. The LM Studio probe is imported lazily so
    this module stays cheap to import in isolation (unit tests, tooling).
    """
    key = (provider or DEFAULT_PROVIDER).strip().lower()
    if key in _LLAMASERVER_ALIASES:
        return await fetch_engine_info(base_url, token, target_model)
    from hearth.control.control import fetch_engine_info as _lmstudio_probe
    return await _lmstudio_probe(base_url, token, target_model)


# ── M4: reasoning_effort graceful-tolerance swap-time check (verify-and-document) ─

async def probe_reasoning_effort_tolerance(
    base_url: str, token: str, model: str, effort: str = "none"
) -> dict:
    """Parity-inventory line-item **M4**, packaged as the runnable "5-min check":
    does this server IGNORE an unknown `reasoning_effort` field GRACEFULLY — i.e.
    accept the request and return a normal completion — rather than reject it?

    Returns {tolerated, status, note}: `tolerated` True ⇔ the server accepted a chat
    completion carrying `reasoning_effort` (HTTP 200); False ⇔ it rejected the request;
    None ⇔ the probe was inconclusive (server unreachable/timeout). Never raises.

    This is a DIAGNOSTIC only — it is NOT on the hot path. The parity inventory's M4
    reading is verify-and-document ("verify reasoning_effort is *ignored gracefully*
    like mlx_lm.server does (expected; 5-min check at swap time)"), so bot.py keeps
    sending `reasoning_effort` unconditionally (the inventory's expected-graceful
    stance); this check exists to CONFIRM that expectation against a real server at
    swap time. If it ever reports tolerated=False, adding a send-guard becomes a
    separate signed follow-up — not a silent change here.
    """
    result: dict = {"tolerated": None, "status": None, "note": ""}
    host = base_url.rstrip("/")
    if not host.endswith("/v1"):
        host = host + "/v1"
    url = f"{host}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
        "reasoning_effort": effort,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                result["status"] = resp.status
                result["tolerated"] = resp.status == 200
                result["note"] = (
                    "reasoning_effort accepted — ignored gracefully (HTTP 200)"
                    if resp.status == 200
                    else f"server rejected request carrying reasoning_effort (HTTP {resp.status})"
                )
    except Exception as exc:  # noqa: BLE001 — diagnostic, never crashes a caller
        result["note"] = f"probe inconclusive ({type(exc).__name__}) — server unreachable?"
    return result
