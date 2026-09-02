"""memory_prefetch.py — voice-lane per-turn targeted recall (lane (b), voice stroke).

Prefetch-behind (DESIGN-lane-b-per-turn-recall.md §D-A): after the user's turn N
is transcribed, a targeted recall runs in the BACKGROUND, off the event loop; its
extras are injected into the system instruction before turn N+1. Zero added
latency, a one-turn lag — "ask, she checks, next turn she knows". Synchronous
voice recall is rejected by the latency doctrine (it would add ~0.3 s before
first-token every turn).

Placement: right AFTER ConfigReloadProcessor, before the LLM. A live companion
switch applies upstream (that processor), so on each turn this one reads the
switcher's CURRENT seam + raw base instruction; a changed seam is a switch — the
old companion's prefetch is discarded and the base rebased. On the turn's
LLMContextFrame it (1) injects the instruction prefetched from the PRIOR turn's
cue, then (2) launches this turn's recall for the next turn.

The base passed to ``seam.augment_turn`` is the RAW system instruction (the
memory block is re-composed by augment_turn), matching the chat glue's
``base_instruction``. Every failure path serves the turn unchanged (containment):
a recall that raises, times out, or is superseded simply yields no extras.

Known scope: a live PERSONA-knob reload (config panel) is not specially tracked
here — a rare operator action whose pre-existing effect (the reloader pushes a
memory-free recomposition) is unchanged; the next prefetch re-adds a memory block
on the switcher's base. The common paths — no live persona edits, or a full
companion switch — are exact.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from pipecat.frames.frames import Frame, LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import LLMSettings

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


class MemoryPrefetch(FrameProcessor):
    """Prefetch-behind per-turn recall for the voice loop (see module docstring)."""

    def __init__(self, *, switcher, context, **kwargs) -> None:
        super().__init__(**kwargs)
        self._switcher = switcher                      # current_seam / current_base_instruction
        self._context = context                        # LLMContext; last user msg = the cue
        self._seam_seen = switcher.current_seam
        self._raw_base = switcher.current_base_instruction
        self._applied_cue: Optional[str] = None        # cue whose extras are on the LLM now
        self._pending: Optional[tuple] = None          # (cue|None, instruction) for next turn
        self._last_launched: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._gen = 0                                  # supersede token: only the latest wins

    def _rebase_if_switched(self) -> None:
        """A changed seam means a live switch applied upstream this turn: the
        switch frame already set the clean base on the LLM, so drop the old
        companion's prefetch and rebase (identity compare — seams are distinct)."""
        seam = self._switcher.current_seam
        if seam is not self._seam_seen:
            self._seam_seen = seam
            self._raw_base = self._switcher.current_base_instruction
            self._applied_cue = None
            self._pending = None
            self._last_launched = None
            self._gen += 1

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        self._rebase_if_switched()
        seam = self._switcher.current_seam

        # (1) Inject the instruction prefetched from the PRIOR turn's cue. Pushed
        #     ahead of the context frame so it lands on THIS turn (T3 ordering).
        if self._pending is not None:
            cue, instruction = self._pending
            self._pending = None
            if cue != self._applied_cue:
                try:
                    await self.push_frame(
                        LLMUpdateSettingsFrame(
                            delta=LLMSettings(system_instruction=instruction)),
                        FrameDirection.DOWNSTREAM)
                    self._applied_cue = cue
                except Exception as exc:  # noqa: BLE001 — never cost the turn
                    logger.warning("[memory] voice prefetch inject failed ({})",
                                   type(exc).__name__)

        # (2) Launch THIS turn's recall for the next turn (prefetch-behind).
        self._launch(seam)

        await self.push_frame(frame, direction)

    def _launch(self, seam) -> None:
        if (seam is None or not getattr(seam, "per_turn_enabled", False)
                or not getattr(seam, "per_turn_voice", False)):
            return
        cue = " ".join(_last_user_text(self._context.messages).split())
        if len(cue) < getattr(seam, "per_turn_min_chars", 12):
            # Below the floor (bare greetings/closes): clear any applied extras
            # next turn by targeting the clean base. augment_turn("") makes no
            # backend call — safe on the event-loop thread.
            if self._applied_cue is not None:
                self._pending = (None, seam.augment_turn(self._raw_base, ""))
            self._last_launched = None
            return
        if cue == self._last_launched:
            return  # same question as last turn — its extras already stand
        self._last_launched = cue
        self._gen += 1
        gen = self._gen
        raw_base = self._raw_base

        async def _run() -> None:
            try:
                instruction = await asyncio.wait_for(
                    asyncio.to_thread(seam.augment_turn, raw_base, cue),
                    timeout=PREFETCH_DEADLINE_S)
            except Exception as exc:  # noqa: BLE001 — an extra must never cost a turn
                logger.warning("[memory] voice prefetch recall failed ({}) — no extras",
                               type(exc).__name__)
                return
            if gen == self._gen:  # not superseded by a newer turn or a switch
                self._pending = (cue, instruction)

        self._task = asyncio.create_task(_run())
