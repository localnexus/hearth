"""OpenClaw dispatch bridge — the voice LLM's narrow "hands".

The voice model gets exactly two tools:

    dispatch_task(task, expect)  — send one sentence of work to the OpenClaw
                                   `hands` agent via the gateway's OpenAI ingress.
                                   expect="quick": wait up to quick_wait_s and
                                   answer inline; on timeout auto-convert to a
                                   long dispatch and return the ack instead.
                                   expect="long": ack immediately, results land
                                   in the OpenClaw channel (posted by `hands`).
    check_tasks()                — bridge-local status; no gateway round-trip.

Activation = config presence: config/openclaw.toml with [openclaw] enabled=true
(read via config_loader.load_openclaw_config — same reader that fills the
{{openclaw_tools}} prompt slot, so tools and prompt block appear/disappear
together). Absent or disabled ⇒ maybe_attach() returns None and bot.py behavior
is byte-identical.

Secrets: the gateway bearer token is read once at construction —
env OPENCLAW_GATEWAY_TOKEN wins, else gateway.auth.token from the token_source
JSON — and lives only on this object. It is never logged, never in tool results,
never in the LLM context. Log lines carry task ids and durations only, never
task text.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, Optional

import aiohttp
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from hearth.config import config_loader

# The completion contract (DESIGN-bridge §completion-contract): the HTTP response
# text = the spoken summary; the channel post carries the artifacts. The `hands`
# workspace charter (AGENTS.md) states the same rules — this wrapper is the
# per-dispatch reminder, kept terse because it rides every request.
_DISPATCH_WRAPPER = (
    "[dispatch from Hearth voice] {task}\n"
    "Do the work now. Reply with a spoken-size summary only — at most 3 "
    "sentences, no paths, no code, no JSON (it will be read aloud) — and post "
    "the full result to your configured channel per your charter."
)

_DISPATCH_SCHEMA = FunctionSchema(
    name="dispatch_task",
    description=(
        "Hand a real-world task to your background hands (web search, reading "
        "files, longer research). Use expect='quick' only for lookups that "
        "should answer within seconds; use expect='long' for real work — you "
        "get an acknowledgment now and the result later."
    ),
    properties={
        "task": {
            "type": "string",
            "description": "The task, as one clear self-contained sentence.",
        },
        "expect": {
            "type": "string",
            "enum": ["quick", "long"],
            "description": "quick = wait a few seconds for an inline answer; long = dispatch and acknowledge.",
        },
    },
    required=["task"],
)

_CHECK_SCHEMA = FunctionSchema(
    name="check_tasks",
    description=(
        "Check on background tasks you dispatched earlier: what is still "
        "running and what finished since you last looked."
    ),
    properties={},
    required=[],
)


class OpenClawBridge:
    """Holds gateway credentials + task state; registered as tool handlers."""

    def __init__(self, cfg: dict):
        self._url: str = str(cfg["gateway_url"]).rstrip("/")
        self._agent: str = str(cfg["agent"])
        self._quick_wait_s: float = float(cfg["quick_wait_s"])
        self._timeout_s: float = float(cfg["timeout_s"])
        self._max_in_flight: int = int(cfg["max_in_flight"])
        self._token: str = self._read_token(cfg)  # never logged
        # D4: one stable OpenClaw session per Hearth session (= per process run).
        # The gateway derives a stable session key from this `user` string, so
        # consecutive dispatches share prefix-KV + referential continuity.
        self._session_user: str = f"hearth:{uuid.uuid4().hex[:12]}"
        self._tasks: dict[int, dict[str, Any]] = {}
        self._next_id: int = 1

    @staticmethod
    def _read_token(cfg: dict) -> str:
        tok = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
        if tok:
            return tok
        src = cfg.get("token_source", "")
        try:
            with open(src) as f:
                tok = str(json.load(f)["gateway"]["auth"]["token"]).strip()
        except (OSError, KeyError, ValueError) as exc:
            raise config_loader.ConfigError(
                f"openclaw.toml token_source unusable ({type(exc).__name__}): {src}"
            )
        if not tok:
            raise config_loader.ConfigError(f"empty gateway token in {src}")
        return tok

    # ── tool handlers ────────────────────────────────────────────────────────

    async def _on_dispatch(self, params) -> None:
        task = str(params.arguments.get("task", "")).strip()
        expect = params.arguments.get("expect", "long")
        if not task:
            await params.result_callback({"status": "error", "reason": "empty task"})
            return
        running = [r for r in self._tasks.values() if r["status"] == "running"]
        if len(running) >= self._max_in_flight:
            await params.result_callback(
                {"status": "busy", "reason": f"{len(running)} tasks already in flight; wait for one to finish"}
            )
            return

        tid = self._next_id
        self._next_id += 1
        rec: dict[str, Any] = {
            "id": tid, "status": "running", "started": time.monotonic(),
            "result": None, "announced": False,
        }
        self._tasks[tid] = rec
        logger.info("[openclaw] task {} dispatched (expect={})", tid, expect)
        runner = asyncio.create_task(self._run(rec, task))

        if expect == "quick":
            done, _ = await asyncio.wait({runner}, timeout=self._quick_wait_s)
            if done:
                rec["announced"] = True  # inline result = already surfaced
                if rec["status"] == "done":
                    await params.result_callback(
                        {"status": "done", "task_id": tid, "result": rec["result"]}
                    )
                else:
                    await params.result_callback(
                        {"status": rec["status"], "task_id": tid, "reason": rec["result"]}
                    )
                return
            # quick timed out → auto-convert to a long dispatch (no error, no dead air)

        await params.result_callback(
            {
                "status": "dispatched",
                "task_id": tid,
                "note": "acknowledge naturally; the result will be posted to the channel when done",
            }
        )

    async def _on_check(self, params) -> None:
        now = time.monotonic()
        running = [
            {"task_id": r["id"], "running_for_s": int(now - r["started"])}
            for r in self._tasks.values() if r["status"] == "running"
        ]
        done = [
            {"task_id": r["id"], "status": r["status"], "result": r["result"]}
            for r in self._tasks.values()
            if r["status"] != "running" and not r["announced"]
        ]
        for r in self._tasks.values():
            if r["status"] != "running":
                r["announced"] = True
        await params.result_callback({"running": running, "done_since_last_check": done})

    # ── gateway plumbing ─────────────────────────────────────────────────────

    async def _run(self, rec: dict, task: str) -> None:
        t0 = time.monotonic()
        try:
            rec["result"] = await self._call_gateway(_DISPATCH_WRAPPER.format(task=task))
            rec["status"] = "done"
        except asyncio.TimeoutError:
            rec["status"], rec["result"] = "timeout", f"task {rec['id']} exceeded {int(self._timeout_s)}s"
        except asyncio.CancelledError:
            rec["status"], rec["result"] = "error", "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced to the character, must not crash the loop
            rec["status"], rec["result"] = "error", str(exc)[:300]
        logger.info(
            "[openclaw] task {} {} in {:.1f}s", rec["id"], rec["status"], time.monotonic() - t0
        )

    async def _call_gateway(self, content: str) -> str:
        body = {
            "model": f"openclaw/{self._agent}",  # pinned routing — the LLM never picks the target
            "user": self._session_user,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {"Authorization": f"Bearer {self._token}"}
        timeout = aiohttp.ClientTimeout(total=self._timeout_s)
        for attempt in (1, 2):  # single retry on connection failure, then honest error
            try:
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.post(
                        f"{self._url}/v1/chat/completions", json=body, headers=headers
                    ) as resp:
                        if resp.status != 200:
                            text = (await resp.text())[:300]
                            raise RuntimeError(f"gateway HTTP {resp.status}: {text}")
                        data = await resp.json()
                        return (data["choices"][0]["message"]["content"] or "").strip()
            except aiohttp.ClientConnectionError:
                if attempt == 2:
                    raise RuntimeError("hands unreachable (gateway connection failed)")
                await asyncio.sleep(0.5)
        raise RuntimeError("hands unreachable")  # unreachable; satisfies type-checkers

    def _schedule_prewarm(self) -> None:
        """Move the ~11 s first-call harness prefill off the first real ask.

        One trivial same-session exchange at startup (measured: repeats then ride
        LM Studio's prefix-KV cache at ~1 s — PLAN-phases §Phase-0 results). Sent
        WITHOUT the dispatch wrapper so `hands` does not post a channel message.
        Fire-and-forget; failure is logged at debug and never blocks startup.
        """
        async def _warm() -> None:
            try:
                t0 = time.monotonic()
                await self._call_gateway(
                    "Session warm-up ping from the Hearth bridge. Reply with the "
                    "single word: ready. Do not use tools; do not post messages."
                )
                logger.info("[openclaw] session pre-warmed in {:.1f}s", time.monotonic() - t0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[openclaw] pre-warm failed ({})", type(exc).__name__)

        asyncio.get_running_loop().create_task(_warm())


def maybe_attach(llm, context) -> Optional[OpenClawBridge]:
    """Attach the bridge iff config/openclaw.toml enables it. The single bot.py seam.

    Disabled/absent ⇒ returns None having touched nothing: no tools registered,
    context.set_tools never called, prompt unchanged (the {{openclaw_tools}} slot
    renders empty via the same load_openclaw_config gate).
    """
    cfg = config_loader.load_openclaw_config()
    if not cfg:
        return None
    bridge = OpenClawBridge(cfg)
    llm.register_function("dispatch_task", bridge._on_dispatch)
    llm.register_function("check_tasks", bridge._on_check)
    context.set_tools(ToolsSchema(standard_tools=[_DISPATCH_SCHEMA, _CHECK_SCHEMA]))
    bridge._schedule_prewarm()
    logger.info("[openclaw] bridge attached (agent={}, gateway={})", bridge._agent, bridge._url)
    return bridge
