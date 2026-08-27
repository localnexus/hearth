"""
control_taps.py — control-seam FrameProcessors (MuteGate + SpeakingTap).

Extracted from control.py so the web control box (control.py)
holds only the aiohttp layer and this holds only the two in-pipeline processors it
depends on. Both are wired into the pipeline in bot.py; the web routes call methods
on the instances (mute_gate.set_muted, speaking_tap.is_speaking, …).

Exports:
    MuteGate    — drops InputAudioRawFrame when muted (wire before VADProcessor)
    SpeakingTap — tracks bot-speaking state (wire after TTS output)
"""

from __future__ import annotations

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


# ── MuteGate ───────────────────────────────────────────────────────────────────

class MuteGate(FrameProcessor):
    """
    Drops InputAudioRawFrame when muted; passes everything else unconditionally.

    Wire immediately after transport.input() and before VADProcessor:
        [transport.input(), mute_gate, vad, stt, ...]
    Muted → VAD sees no audio → no VAD frames → no segmentation, no barge-in.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._muted: bool = False
        self._ptt_prev: bool | None = None  # latched baseline saved during a PTT hold

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    @property
    def is_muted(self) -> bool:
        return self._muted

    def ptt_press(self) -> None:
        """Momentary open: remember the latched baseline, then unmute."""
        self._ptt_prev = self._muted
        self._muted = False

    def ptt_release(self) -> None:
        """Restore the latched baseline (NOT a hard-mute — avoids stranding a
        'listening' baseline as muted after a single PTT press)."""
        self._muted = self._ptt_prev if self._ptt_prev is not None else self._muted
        self._ptt_prev = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and self._muted:
            return  # drop — do NOT push
        await self.push_frame(frame, direction)


# ── SpeakingTap ────────────────────────────────────────────────────────────────

class SpeakingTap(FrameProcessor):
    """
    Observes BotStartedSpeakingFrame / BotStoppedSpeakingFrame and flips a flag.
    Place late in the pipeline (after TTS, before transport.output() or after).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._speaking: bool = False

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speaking = False
        await self.push_frame(frame, direction)
