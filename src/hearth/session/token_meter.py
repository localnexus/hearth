"""TokenMeter — per-session LLM token-usage observer for the voice loop.

A pipecat BaseObserver that watches the MetricsFrame -> LLMUsageMetricsData that
OpenAILLMService already emits (the model server's OWN usage block — ground truth,
not a tiktoken estimate). It records per-turn prompt/completion/total counts, keeps
a session cumulative running total, and prints a shutdown summary. An open-time
chars/4 estimate (prime_estimate) fills the gauge's dead zone — after a resume or
live switch the pre-fill is real context the server won't report until turn 1.

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
        self.last_completion = 0
        # Estimate primed at open / live-switch; any real server report clears it.
        self.est_pending: int | None = None
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
            self.last_completion = u.completion_tokens
            self.est_pending = None
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

    def prime_estimate(self, system_instruction: str, messages) -> None:
        """Seed the runway gauge before the server's first usage report.

        Pre-fill (system prompt + memory block + any resumed transcript) is real
        context, but the server reports it only with turn 1 — without this seed
        the panel claims 0 held right after a resume or live switch. chars/4
        heuristic; snapshot() flags it estimated until ground truth replaces it.
        Duck-typed on purpose: the pipecat-free switcher calls it without
        importing this module.
        """
        chars = len(system_instruction or "")
        for m in messages or []:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, list):  # multimodal parts
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chars += len(part["text"])
        self.est_pending = chars // 4

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
        if self.est_pending is not None:
            held, estimated = self.est_pending, True
        else:
            # Last request's prompt + its completion — both sit in context now;
            # prompt alone perpetually trails the gauge by one reply.
            held, estimated = self.last_prompt + self.last_completion, False
        return {
            "turns": self.turns,
            "prompt": self.prompt,
            "completion": self.completion,
            "total": self.total,
            "reasoning_seen": self.reasoning_seen,
            "leak": self.reasoning_seen > 0,
            # Phase 1 status block (runway gauge):
            "held_in_ctx": held,
            "estimated": estimated,               # True until the first server report
            "net_turn_growth": net_turn_growth,   # Δ per-turn prompt_tokens
        }

    def print_summary(self):
        print(f"[TokenMeter] {self.summary()}", file=sys.stderr, flush=True)
