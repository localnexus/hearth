"""test_engine_probe.py — headless proof of the llama-server /props probe adapter (M1)
plus the reasoning_effort graceful-tolerance check (M4).

Runs WITHOUT LM Studio or llama-server: it stands up a local aiohttp test server on
127.0.0.1:0 and points the probe at it, so the real HTTP path is exercised end to end
against controlled /props, /api/v0/models, and /v1/chat/completions responses.

Proves the load-bearing invariants of hearth/control/engine_probe_llamaserver.py:
  1. happy path  — llama-server /props shape → correct {provider, model_id, allotted, model_max}
  2. server down — connection refused → all value keys None, provider label intact, no raise
  3. malformed   — non-JSON / non-object body → Nones, no raise
  4. 401         — auth failure → Nones
  5. /v1 strip   — a base_url ending in /v1 is trimmed to reach the native root /props
  6. n_ctx_train — exposed-when-present lights up model_max (else None, per current builds)
  7. dispatch    — fetch_engine_info_for routes llama-server → /props, default/unknown → LM Studio
  8. M4          — probe_reasoning_effort_tolerance: 200 ⇒ tolerated, 4xx ⇒ rejected, both graceful

Run:  PYTHONPATH=engine .venv/bin/python engine/tests/test_engine_probe.py
"""

from __future__ import annotations

import asyncio
import sys

from aiohttp import web

from hearth.control import engine_probe_llamaserver as probe

_PASS = 0
_FAIL = 0


def check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}")


# ── local aiohttp test server ────────────────────────────────────────────────
# Behavior is driven by a mutable `state` dict so each test tunes the responses
# without spinning up a new server class.

class MockEngine:
    def __init__(self):
        self.state = {
            "props_status": 200,
            "props_body": {},          # dict → JSON; str → raw text (malformed test)
            "models_status": 200,
            "models_body": {"data": []},
            "chat_status": 200,
            "require_auth": False,
        }
        self.runner = None
        self.base = None  # http://127.0.0.1:<port>

    def _authed(self, req):
        if not self.state["require_auth"]:
            return True
        return req.headers.get("Authorization", "").startswith("Bearer ")

    async def _props(self, req):
        if not self._authed(req):
            return web.Response(status=401)
        st = self.state["props_status"]
        if st != 200:
            return web.Response(status=st)
        body = self.state["props_body"]
        if isinstance(body, str):
            return web.Response(text=body, content_type="application/json")
        return web.json_response(body)

    async def _models(self, req):
        if not self._authed(req):
            return web.Response(status=401)
        st = self.state["models_status"]
        if st != 200:
            return web.Response(status=st)
        return web.json_response(self.state["models_body"])

    async def _chat(self, req):
        if not self._authed(req):
            return web.Response(status=401)
        st = self.state["chat_status"]
        if st != 200:
            return web.Response(status=st)
        return web.json_response({
            "choices": [{"message": {"role": "assistant", "content": "pong"}}],
        })

    async def start(self):
        app = web.Application()
        app.router.add_get("/props", self._props)
        app.router.add_get("/api/v0/models", self._models)
        app.router.add_post("/v1/chat/completions", self._chat)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = list(self.runner.addresses)[0][1]
        self.base = f"http://127.0.0.1:{port}"
        return self.base

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()


# A realistic llama-server /props body (fields per llama.cpp tools/server README).
def _props_body(n_ctx=8192, model_path="/models/candidate-01/IQ3_XXS.gguf", n_ctx_train=None):
    body = {
        "default_generation_settings": {
            "id": 0,
            "n_ctx": n_ctx,
            "params": {"temperature": 0.4},
        },
        "total_slots": 1,
        "model_path": model_path,
        "chat_template": "{{ ... }}",
        "build_info": "b1234",
        "is_sleeping": False,
    }
    if n_ctx_train is not None:
        body["default_generation_settings"]["n_ctx_train"] = n_ctx_train
    return body


async def test_happy_path():
    print("\n[1] llama-server /props happy path → correct engine facts")
    eng = MockEngine()
    base = await eng.start()
    try:
        eng.state["props_body"] = _props_body(n_ctx=8192, model_path="/models/cand/IQ3_XXS.gguf")
        info = await probe.fetch_engine_info(base, token="")
        check(info["provider"] == "llama-server", f"provider is llama-server (got {info['provider']!r})")
        check(info["model_id"] == "IQ3_XXS.gguf", f"model_id = GGUF basename (got {info['model_id']!r})")
        check(info["allotted"] == 8192, f"allotted = default_generation_settings.n_ctx (got {info['allotted']!r})")
        check(info["model_max"] is None, "model_max None when /props omits n_ctx_train (current builds)")
    finally:
        await eng.stop()


async def test_server_down():
    print("\n[2] server down (connection refused) → all Nones, no raise")
    # Port 1 is not listening → immediate refusal.
    info = await probe.fetch_engine_info("http://127.0.0.1:1", token="")
    check(info["provider"] == "llama-server", "provider label intact on failure")
    check(info["model_id"] is None and info["allotted"] is None and info["model_max"] is None,
          "all value keys None when the server is down")


async def test_malformed():
    print("\n[3] malformed body (non-JSON text) → Nones, no raise")
    eng = MockEngine()
    base = await eng.start()
    try:
        eng.state["props_body"] = "this is not json {"
        info = await probe.fetch_engine_info(base, token="")
        check(info["model_id"] is None and info["allotted"] is None and info["model_max"] is None,
              "malformed /props body yields Nones")
        # Also: a JSON body that is a list, not an object.
        eng.state["props_body"] = "[]"
        info2 = await probe.fetch_engine_info(base, token="")
        check(info2["allotted"] is None, "non-object JSON body yields Nones")
    finally:
        await eng.stop()


async def test_401():
    print("\n[4] 401 auth failure → Nones")
    eng = MockEngine()
    base = await eng.start()
    try:
        eng.state["require_auth"] = True
        eng.state["props_body"] = _props_body()
        # No token → server returns 401 → Nones.
        info = await probe.fetch_engine_info(base, token="")
        check(info["allotted"] is None, "no-token request rejected (401) → Nones")
        # With a token the auth header is sent and it succeeds.
        info_ok = await probe.fetch_engine_info(base, token="secret-xyz")
        check(info_ok["allotted"] == 8192, "token present → auth header sent → 200 → facts populate")
    finally:
        await eng.stop()


async def test_v1_strip():
    print("\n[5] trailing /v1 is stripped to reach the native /props root")
    eng = MockEngine()
    base = await eng.start()
    try:
        eng.state["props_body"] = _props_body(n_ctx=4096)
        # Pass the OpenAI-compat base (…/v1); the probe must strip it and still hit /props.
        info = await probe.fetch_engine_info(base + "/v1", token="")
        check(info["allotted"] == 4096, "base_url ending in /v1 still resolves /props")
    finally:
        await eng.stop()


async def test_n_ctx_train_present():
    print("\n[6] n_ctx_train exposed → model_max lights up")
    eng = MockEngine()
    base = await eng.start()
    try:
        eng.state["props_body"] = _props_body(n_ctx=8192, n_ctx_train=32768)
        info = await probe.fetch_engine_info(base, token="")
        check(info["model_max"] == 32768, f"model_max = n_ctx_train when present (got {info['model_max']!r})")
        check(info["allotted"] == 8192, "allotted still the loaded n_ctx, distinct from model_max")
    finally:
        await eng.stop()


async def test_dispatch():
    print("\n[7] fetch_engine_info_for routing (llama-server vs default LM Studio)")
    eng = MockEngine()
    base = await eng.start()
    try:
        eng.state["props_body"] = _props_body(n_ctx=8192)
        eng.state["models_body"] = {"data": [
            {"id": "test-model", "state": "loaded",
             "loaded_context_length": 6000, "max_context_length": 40960},
        ]}
        # provider = llama-server → /props path (provider label proves the route).
        ls = await probe.fetch_engine_info_for("llama-server", base, token="")
        check(ls["provider"] == "llama-server" and ls["allotted"] == 8192, "'llama-server' → /props probe")
        # default "lmstudio" → the stable-core LM Studio /api/v0/models probe.
        lm = await probe.fetch_engine_info_for("lmstudio", base, token="", target_model="test-model")
        check(lm["provider"] == "LM Studio" and lm["allotted"] == 6000, "'lmstudio' → LM Studio probe")
        # None / unknown → default (LM Studio), never an error.
        dflt = await probe.fetch_engine_info_for(None, base, token="", target_model="test-model")
        check(dflt["provider"] == "LM Studio", "None provider defaults to LM Studio")
        unk = await probe.fetch_engine_info_for("wat", base, token="", target_model="test-model")
        check(unk["provider"] == "LM Studio", "unknown provider falls back to LM Studio (no raise)")
    finally:
        await eng.stop()


async def test_reasoning_effort_tolerance():
    print("\n[8] M4: reasoning_effort graceful-tolerance swap-time check")
    eng = MockEngine()
    base = await eng.start()
    try:
        # Server that accepts (ignores) reasoning_effort → 200 → tolerated.
        eng.state["chat_status"] = 200
        r_ok = await probe.probe_reasoning_effort_tolerance(base + "/v1", token="", model="m")
        check(r_ok["tolerated"] is True and r_ok["status"] == 200, "200 ⇒ tolerated True (ignored gracefully)")
        # Server that rejects the unknown field → 400 → not tolerated, but graceful (no raise).
        eng.state["chat_status"] = 400
        r_bad = await probe.probe_reasoning_effort_tolerance(base + "/v1", token="", model="m")
        check(r_bad["tolerated"] is False and r_bad["status"] == 400, "400 ⇒ tolerated False, reported not raised")
        # Server unreachable → inconclusive (None), never raises.
        r_none = await probe.probe_reasoning_effort_tolerance("http://127.0.0.1:1/v1", token="", model="m")
        check(r_none["tolerated"] is None, "unreachable ⇒ tolerated None (inconclusive)")
    finally:
        await eng.stop()


async def _main():
    await test_happy_path()
    await test_server_down()
    await test_malformed()
    await test_401()
    await test_v1_strip()
    await test_n_ctx_train_present()
    await test_dispatch()
    await test_reasoning_effort_tolerance()
    print(f"\n{'='*52}\n  RESULT: {_PASS} passed, {_FAIL} failed\n{'='*52}")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
