"""TurnTimer — per-turn in-ear latency observer for the voice loop.

A pipecat BaseObserver that times one spoken turn end-to-end: the gap the
felt latency actually lives in is the chain around the model (end-pointing,
STT, the first TTS chunk), not the model itself, so this marks each stage as
it happens rather than trusting a single end-to-end guess.

Marks (seconds since the turn opened, each the first occurrence only):
  stt_s         — UserStoppedSpeakingFrame -> TranscriptionFrame (STT latency)
  first_token_s — UserStoppedSpeakingFrame -> first LLMTextFrame that follows
                  an LLMFullResponseStartFrame (model time-to-first-token)
  first_audio_s — UserStoppedSpeakingFrame -> BotStartedSpeakingFrame. This is
                  the felt number: the silence the person in the room hears.
  first_tts_s   — UserStoppedSpeakingFrame -> first TTSAudioRawFrame (first
                  synthesized chunk, ahead of playback actually starting)

A turn opens on UserStoppedSpeakingFrame and closes on BotStoppedSpeakingFrame
(speaking_s = seconds from first_audio_s to the stop). A still-open unanswered
turn is closed as-is when a new UserStoppedSpeakingFrame arrives. A response
with no open turn (nothing preceded it) is not timed and only bumps
``untimed``. Numbers and None only — never a frame's text or audio payload.
"""

from __future__ import annotations

import statistics
import sys
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

MAX_TURNS = 500


class TurnTimer(BaseObserver):
    """Time each spoken turn's stt/first-token/first-audio/speaking marks.

    Args:
        verbose: When True, print one stderr line per closed turn (numbers
            only) as it closes. When False, only the shutdown summary prints.
    """

    def __init__(self, verbose: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.verbose = verbose
        self.turns: list[dict] = []
        self.untimed = 0
        self._total_turns = 0
        self._open: dict | None = None
        self._response_started = False
        self._untimed_active = False
        self._seen_ids: set[int] = set()

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if frame.id in self._seen_ids:
            return
        self._seen_ids.add(frame.id)

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._close_open(unanswered=True)
            self._open = {"_opened": time.monotonic(), "stt_s": None,
                          "first_token_s": None, "first_tts_s": None,
                          "first_audio_s": None, "speaking_s": None}
            self._response_started = False
            return

        if self._open is None:
            # A response with no open turn (a typed turn): count it once per
            # response cycle, not once per frame in that cycle.
            if isinstance(frame, BotStoppedSpeakingFrame):
                self._untimed_active = False
            elif isinstance(frame, (TranscriptionFrame, LLMFullResponseStartFrame,
                                     LLMTextFrame, TTSAudioRawFrame,
                                     BotStartedSpeakingFrame)):
                if not self._untimed_active:
                    self.untimed += 1
                    self._untimed_active = True
            return

        if isinstance(frame, TranscriptionFrame):
            self._mark("stt_s")
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._response_started = True
        elif isinstance(frame, LLMTextFrame):
            if self._response_started:
                self._mark("first_token_s")
        elif isinstance(frame, TTSAudioRawFrame):
            self._mark("first_tts_s")
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._mark("first_audio_s")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._close_open(unanswered=False)

    def _mark(self, key: str) -> None:
        if self._open[key] is None:
            self._open[key] = round(time.monotonic() - self._open["_opened"], 3)

    def _close_open(self, *, unanswered: bool) -> None:
        turn = self._open
        self._open = None
        if turn is None:
            return
        opened = turn.pop("_opened")
        if turn["first_audio_s"] is not None and not unanswered:
            turn["speaking_s"] = round(time.monotonic() - opened - turn["first_audio_s"], 3)
        self.turns.append(turn)
        self._total_turns += 1
        if len(self.turns) > MAX_TURNS:
            self.turns.pop(0)
        if self.verbose:
            print(
                f"[TurnTimer] turn {self._total_turns}: "
                f"stt {turn['stt_s']} · first token {turn['first_token_s']} · "
                f"first sound {turn['first_audio_s']} · speaking {turn['speaking_s']}",
                file=sys.stderr, flush=True,
            )

    @staticmethod
    def _stats(values: list[float]) -> dict:
        if not values:
            return {"median": None, "p95": None, "max": None}
        median = round(statistics.median(values), 3)
        mx = round(max(values), 3)
        p95 = mx if len(values) < 2 else round(statistics.quantiles(values, n=20)[-1], 3)
        return {"median": median, "p95": p95, "max": mx}

    def snapshot(self) -> dict:
        """Numbers and None only — never a frame's text or audio payload."""
        def col(key: str) -> list[float]:
            return [t[key] for t in self.turns if t[key] is not None]

        return {
            "turns": self._total_turns,
            "untimed": self.untimed,
            "stt_s": self._stats(col("stt_s")),
            "first_token_s": self._stats(col("first_token_s")),
            "first_audio_s": self._stats(col("first_audio_s")),
            "per_turn": [
                [t["stt_s"], t["first_token_s"], t["first_audio_s"], t["speaking_s"]]
                for t in self.turns
            ],
        }

    def summary(self) -> str:
        snap = self.snapshot()
        stt, tok, aud = snap["stt_s"], snap["first_token_s"], snap["first_audio_s"]
        return (
            f"turns: {snap['turns']} · transcript {stt['median']} s · "
            f"first token {tok['median']} s · first sound {aud['median']} s "
            f"(p95 {aud['p95']})"
        )

    def print_summary(self):
        print(f"[TurnTimer] {self.summary()}", file=sys.stderr, flush=True)
