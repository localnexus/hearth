"""memory_prefetch.py — voice-lane per-turn targeted recall (lane (b), voice stroke).

Prefetch-behind: after the user's turn N is transcribed, a targeted recall runs
in the BACKGROUND, off the event loop; the block it finds rides turn N+1. Zero
added latency, a one-turn lag — "ask, they check, next turn they know".
Synchronous voice recall is rejected by the latency doctrine (it would add
~0.3 s before first-token every turn).

Where the block lands (changed 2026-09-05): NOT the system instruction. A
per-turn system rewrite made the model server re-evaluate the whole transcript
every turn (measured 2026-09-05: 5.9K–13.8K tokens, 3–8 s, growing with the
sitting). Instead this processor pushes a per-request COPY of the context whose
newest user message carries the framed block (memory.with_turn_block). The live
context is never touched: the block is ephemeral — not in the history the
aggregator keeps, not in the session snapshot — and the prompt's cached prefix
(system + history) stays intact; only the tail is re-evaluated.

Placement: right AFTER ConfigReloadProcessor, before the LLM. A live companion
switch applies upstream, so on each turn this one reads the switcher's CURRENT
seam; a changed seam is a switch — the old companion's prefetch is discarded.
Every failure path serves the turn unchanged (containment): a recall that
raises, times out, or is superseded simply yields no block.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from hearth.memory.fold import with_turn_block

# A hung recall never leaks a task or delays a swap; matches the chat glue's bound.
PREFETCH_DEADLINE_S = 5.0


def _last_user_text(messages) -> str:
    """The most recent user turn's text — the cue. Multimodal parts join by text."""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p["text"] for p in content
                    if isinstance(p, dict) and isinstance(p.get("text"), str)
                )
            return ""
    return ""


def _request_copy(ctx, block: str):
    """A context for THIS request only: same tools, the block folded into the
    newest user message. The live context (and its message dicts) stay as
    they are."""
    kwargs = {}
    tools = getattr(ctx, "tools", None)
    if tools is not None:
        kwargs["tools"] = tools
    choice = getattr(ctx, "tool_choice", None)
    if choice is not None:
        kwargs["tool_choice"] = choice
    return LLMContext(messages=with_turn_block(list(ctx.messages), block), **kwargs)


class MemoryPrefetch(FrameProcessor):
    """Prefetch-behind per-turn recall for the voice loop (see module docstring)."""

    def __init__(self, *, switcher, context, **kwargs) -> None:
        super().__init__(**kwargs)
        self._switcher = switcher                      # current_seam
        self._context = context                        # LLMContext; last user msg = the cue
        self._seam_seen = switcher.current_seam
        self._pending: Optional[tuple] = None          # (cue, block) for the next turn
        self._last_launched: Optional[str] = None
        self._last_block: str = ""                     # the block the last cue produced
        self._task: Optional[asyncio.Task] = None
        self._gen = 0                                  # supersede token: only the latest wins

    def _rebase_if_switched(self) -> None:
        """A changed seam means a live switch applied upstream this turn: drop
        the old companion's prefetch (identity compare — seams are distinct)."""
        seam = self._switcher.current_seam
        if seam is not self._seam_seen:
            self._seam_seen = seam
            self._pending = None
            self._last_launched = None
            self._last_block = ""
            self._gen += 1

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        self._rebase_if_switched()
        seam = self._switcher.current_seam

        # (1) Ride the block prefetched from the PRIOR turn's cue on THIS turn,
        #     as a request copy — the live context is never mutated.
        out = frame
        if self._pending is not None:
            _cue, block = self._pending
            self._pending = None
            if block:
                try:
                    out = LLMContextFrame(context=_request_copy(frame.context, block))
                except Exception as exc:  # noqa: BLE001 — never cost the turn
                    logger.warning("[memory] voice prefetch fold failed ({})",
                                   type(exc).__name__)
                    out = frame

        # (2) Launch THIS turn's recall for the next turn (prefetch-behind).
        self._launch(seam)

        await self.push_frame(out, direction)

    def _launch(self, seam) -> None:
        if (seam is None or not getattr(seam, "per_turn_enabled", False)
                or not getattr(seam, "per_turn_voice", False)):
            # Gates off — including a mid-sitting runtime poke (the panel's
            # per-turn-voice pause). Nothing rides: the block is ephemeral, so
            # stopping the launches IS stopping the cost.
            self._gen += 1  # supersede any in-flight recall
            self._pending = None
            self._last_launched = None
            return
        cue = " ".join(_last_user_text(self._context.messages).split())
        if len(cue) < getattr(seam, "per_turn_min_chars", 12):
            self._last_launched = None   # below the floor (bare greetings/closes)
            return
        if cue == self._last_launched:
            # Same question as last turn: its block rides again, no new recall.
            self._pending = (cue, self._last_block)
            return
        self._last_launched = cue
        self._gen += 1
        gen = self._gen

        async def _run() -> None:
            try:
                block = await asyncio.wait_for(
                    asyncio.to_thread(seam.turn_block, cue),
                    timeout=PREFETCH_DEADLINE_S)
            except Exception as exc:  # noqa: BLE001 — an extra must never cost a turn
                logger.warning("[memory] voice prefetch recall failed ({}) — no extras",
                               type(exc).__name__)
                return
            if gen == self._gen:  # not superseded by a newer turn or a switch
                self._last_block = str(block or "")
                self._pending = (cue, self._last_block)

        self._task = asyncio.create_task(_run())
