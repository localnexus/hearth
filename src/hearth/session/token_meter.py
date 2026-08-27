"""TokenMeter — per-session LLM token-usage observer for the voice loop.

A pipecat BaseObserver that watches the MetricsFrame -> LLMUsageMetricsData that
OpenAILLMService already emits (the model server's OWN usage block — ground truth,
not a tiktoken estimate). It records per-turn prompt/completion/total counts, keeps
a session cumulative running total, and prints a shutdown summary.

Thinking-off guard: Qwen3.6 is a hybrid thinking model with reasoning forced OFF.
If reasoning_tokens > 0 ever appears, chain-of-thought is leaking; we surface a
loud stderr warning (always, regardless of verbose) and flag it in the summary.

Requires PipelineParams(enable_usage_metrics=True) — without it no MetricsFrame is
pushed and this observer is a silent no-op.
"""

from __future__ import annotations

import sys

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed


class TokenMeter(BaseObserver):
    """Accumulate LLM token usage per turn + cumulatively; print on shutdown.

    Args:
        verbose: When True, print a per-turn stderr line as each usage arrives.
            When False, only the shutdown summary (and any reasoning-leak
            warning) print. The leak warning is NEVER gated by this flag.
    """

    def __init__(self, verbose: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.verbose = verbose
        self.turns = 0
        self.prompt = 0
        self.completion = 0
        self.total = 0
        self.reasoning_seen = 0      # cumulative reasoning tokens (should stay 0)
        # Runway gauge (Phase 1 status block): the LATEST per-turn prompt_tokens
        # (this-turn input = held-in-ctx), NOT the cumulative sum, plus the prior
        # turn's value so we can report the turn-over-turn growth (net turn growth).
        self.last_prompt = 0
        self.prev_prompt = 0
        self._seen_ids: set[int] = set()  # dedupe MetricsFrame.id (frames re-pushed)

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if not isinstance(frame, MetricsFrame):
            return
        if frame.id in self._seen_ids:
            return
        self._seen_ids.add(frame.id)
        for md in frame.data:
            if not isinstance(md, LLMUsageMetricsData):
                continue
            u = md.value
            self.turns += 1
            self.prompt += u.prompt_tokens
            self.completion += u.completion_tokens
            self.total += u.total_tokens
            # Runway gauge: shift the per-turn prompt_tokens history forward.
            # net turn growth = last_prompt − prev_prompt (0 on the first turn).
            self.prev_prompt = self.last_prompt
            self.last_prompt = u.prompt_tokens
            rt = u.reasoning_tokens or 0
            if rt > 0:
                # ALWAYS warn (not gated by verbose) — thinking-off is load-bearing.
                self.reasoning_seen += rt
                print(
                    f"[TokenMeter] WARNING  reasoning_tokens={rt} on turn "
                    f"{self.turns} — thinking is LEAKING (should be OFF)",
                    file=sys.stderr, flush=True,
                )
            if self.verbose:
                print(
                    f"[TokenMeter] turn {self.turns}: "
                    f"prompt {u.prompt_tokens} · completion {u.completion_tokens} "
                    f"· total {u.total_tokens}",
                    file=sys.stderr, flush=True,
                )

    def summary(self) -> str:
        line = (
            f"turns: {self.turns} · prompt {self.prompt:,} · "
            f"completion {self.completion:,} · total {self.total:,}"
        )
        if self.reasoning_seen:
            line += (
                f"  WARNING reasoning_tokens seen: {self.reasoning_seen:,} "
                f"(thinking leaked)"
            )
        return line

    def snapshot(self) -> dict:
        """Read-only view of live counts for the web layer (no side effects)."""
        # net turn growth = Δ of consecutive per-turn prompt_tokens; 0 on the
        # first turn (no prior turn to diff against).
        net_turn_growth = self.last_prompt - self.prev_prompt if self.turns > 1 else 0
        return {
            "turns": self.turns,
            "prompt": self.prompt,
            "completion": self.completion,
            "total": self.total,
            "reasoning_seen": self.reasoning_seen,
            "leak": self.reasoning_seen > 0,
            # Phase 1 status block (runway gauge):
            "held_in_ctx": self.last_prompt,      # latest per-turn prompt_tokens
            "net_turn_growth": net_turn_growth,   # Δ per-turn prompt_tokens
        }

    def print_summary(self):
        print(f"[TokenMeter] {self.summary()}", file=sys.stderr, flush=True)
