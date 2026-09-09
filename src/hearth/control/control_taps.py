"""
control_taps.py — control-seam FrameProcessors (MuteGate + SpeakingTap + LevelTap).

Extracted from control.py so the web control box (control.py)
holds only the aiohttp layer and this holds only the two in-pipeline processors it
depends on. Both are wired into the pipeline in bot.py; the web routes call methods
on the instances (mute_gate.set_muted, speaking_tap.is_speaking, …).

Exports:
    MuteGate    — drops InputAudioRawFrame when muted (wire before VADProcessor)
    SpeakingTap — tracks bot-speaking state (wire after TTS output)
    LevelTap    — per-frame RMS level of played audio (wire after transport.output(); desk-figure S2 + panel waveform)
"""

from __future__ import annotations

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
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


# ── LevelTap ───────────────────────────────────────────────────────────────────

class LevelTap(FrameProcessor):
    """
    Per-frame RMS envelope of the audio passing through — levels only, no
    audio kept, no text seen. The one shared envelope source of the desk-figure seam
    (spec 04-hearth-signal-seam §S2) and the panel-waveform idea: whichever
    build lands first creates the class, the other reuses it.

    Placement decides what the level MEANS:
      • after transport.output()  → played audio, at playback time. The output
        transport writes each chunk to the device and only then pushes it
        downstream (pipecat base_output._audio_task_handler), so a tap here
        follows the speaker at the transport's chunk rate (10 ms × chunks,
        ~25 Hz by default). This is the mouth's placement (S2): TTS runs ahead
        of playback by whole sentences, so a level taken after ``tts`` would
        move the mouth before the sound and close it while sound still plays.
      • after mute_gate           → the mic (the panel-waveform's need).

    Contract for readers (fixed with Sophie 2026-09-08 23:36):
      level      float 0–1 = min(1, rms/32768 × GAIN) on the int16 PCM frame;
                 full-scale speech peaks ≈ 1, ordinary speech ≈ 0.2–0.6.
      silence    0.0 on its own after HOLD_S with no frame (one chunk gap),
                 never a held value — the mouth closes when the audio stops,
                 independent of bot_speaking. Bot-stopped and session
                 boundaries zero it at once.
      level_ts   monotonic seconds of the last non-zero frame (0.0 if none),
                 so a stalled tap is distinguishable from true silence.
    """

    GAIN = 4.0
    HOLD_S = 0.1           # > one 40 ms chunk, < a syllable

    def __init__(self, frame_type=None, **kwargs):
        super().__init__(**kwargs)
        # Which audio frames count: OutputAudioRawFrame (played voice) by
        # default; the mic placement passes InputAudioRawFrame.
        from pipecat.frames.frames import OutputAudioRawFrame
        self._frame_type = frame_type or OutputAudioRawFrame
        self._level: float = 0.0
        self._last_ts: float = 0.0     # monotonic, last non-zero frame
        self._seen_ts: float = 0.0     # monotonic, last frame of any level

    @staticmethod
    def rms_level(pcm: bytes, gain: float = GAIN) -> float:
        """int16 little-endian PCM → 0–1 level. Pure; the whole of the math."""
        if len(pcm) < 2:
            return 0.0
        from array import array
        a = array("h"); a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
        n = len(a)
        if n == 0:
            return 0.0
        acc = 0
        for s in a:
            acc += s * s
        rms = (acc / n) ** 0.5
        return min(1.0, rms / 32768.0 * gain)

    def observe(self, frame: Frame, now: float | None = None) -> None:
        """The tap's logic, pure, so a test drives it by hand with its own clock."""
        import time as _t
        t = _t.monotonic() if now is None else now
        if isinstance(frame, self._frame_type):
            self._level = self.rms_level(frame.audio)
            self._seen_ts = t
            if self._level > 0.0:
                self._last_ts = t
        elif isinstance(frame, (BotStoppedSpeakingFrame, StartFrame, EndFrame, CancelFrame)):
            self._level = 0.0

    def level(self, now: float | None = None) -> float:
        """Current level; 0.0 on its own once HOLD_S has passed with no frame."""
        import time as _t
        t = _t.monotonic() if now is None else now
        if t - self._seen_ts > self.HOLD_S:
            return 0.0
        return self._level

    @property
    def level_ts(self) -> float:
        return self._last_ts

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.observe(frame)
        await self.push_frame(frame, direction)
