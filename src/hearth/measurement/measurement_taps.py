"""measurement_taps.py — log-only instrumentation for the speculative-prefill measurement gate.

CONTENT-FREE by design. The JSONL contains ONLY perf telemetry, booleans, counts, and
lengths — NO verbatim transcript ever touches disk. The one content-dependent metric
(the speculation-hit-rate measure — the would-have-matched rate) is computed LIVE in
memory (comparing the provisional
turn to the finalized turn) and only the boolean *result* is logged. This is data-
minimization + secret-safe: the logs are safe to keep/share.

Three passive collectors, all gated behind MEASURE_ENABLED (env, default False):
  TapA (between `stt` and `user_agg`): finalized transcripts (lengths only) + smart-turn
    verdict (is_complete/probability) + VAD start/stop.  Feeds the in-memory turn state.
  TapB (between `user_agg` and `llm`): finalization — reads the in-memory turn state and
    logs derived booleans (exact-match, strict-prefix, segment count, inject-or-spoken).
  Observer (pipeline-wide): LLM TTFB (prefill→first-token latency) + prompt_tokens (context
    size) + cache_read_input_tokens per turn — the felt-latency-vs-context curve (M5) and
    the no-prompt-cache signature (A3).

When MEASURE_ENABLED is off, taps are pure pass-throughs and the observer is not attached →
the loop behaves byte-identically. See .archive/speculative-prefill/plan/005-Measurement.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    MetricsFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# ── Flag ─────────────────────────────────────────────────────────────────────
MEASURE_ENABLED = os.environ.get("MEASURE_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

_LOG_DIR = Path(__file__).resolve().parent / "measurement-logs"


def _norm(s: str) -> str:
    """Whitespace-normalize for a fair provisional-vs-final comparison (A1)."""
    return " ".join((s or "").split())


# ── Prompt-prefix divergence probe (the cache-buster hunt) ─────────────────────
# Per LLM request we serialize the full message list and emit a HASH LADDER: the
# sha256 of the serialized prompt truncated at a set of absolute char offsets. In a
# healthy append-only session the hash at a given early offset is IDENTICAL across
# every turn (the beginning never changes). If some turn mutates an earlier context
# token, the hash at the affected offset FLIPS at that turn — the smallest flipped
# rung localizes WHERE (offset) and the turn localizes WHEN. Content-free: only
# digests + offsets + total length leave RAM; no transcript text is ever serialized.
# (sha256 of a ≥1000-char span is not reversible; hashes are a commitment, not text.)
# Offsets are over the message body (system prompt excluded — it is a static module
# constant, bot.py:110, so it only shifts every offset by a fixed amount).
_LADDER = (1000, 2000, 4000, 6000, 8000, 12000, 16000, 24000,
           32000, 48000, 64000, 80000, 96000, 120000)


def _all_messages(ctx: Any) -> list[tuple[str, str]]:
    """Best-effort (role, content) list from an LLMContext. In-memory only."""
    msgs = None
    for accessor in ("get_messages", "messages"):
        obj = getattr(ctx, accessor, None)
        if obj is None:
            continue
        msgs = obj() if callable(obj) else obj
        break
    out: list[tuple[str, str]] = []
    for m in msgs or []:
        if isinstance(m, dict):
            role, content = m.get("role", "?"), m.get("content")
        else:
            role, content = getattr(m, "role", "?"), getattr(m, "content", None)
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        out.append((str(role), content))
    return out


def _hash_ladder(serialized: str) -> tuple[int, list[list[Any]]]:
    """Return (total_len, [[offset, digest12], ...]) — digests of prefix[:offset].
    `serialized` (which contains text) stays local; only digests are returned."""
    total = len(serialized)
    rungs: list[list[Any]] = []
    for off in _LADDER:
        if off >= total:
            break
        h = hashlib.sha256(serialized[:off].encode("utf-8", "replace")).hexdigest()[:12]
        rungs.append([off, h])
    # always include the full-length digest (detects any change, even past the top rung)
    full = hashlib.sha256(serialized.encode("utf-8", "replace")).hexdigest()[:12]
    rungs.append([total, full])
    return total, rungs


class MeasurementLog:
    """Append-only JSONL sink. CONTENT-FREE: callers pass only telemetry/booleans/lengths."""

    def __init__(self) -> None:
        _LOG_DIR.mkdir(exist_ok=True)
        stamp = int(time.time())
        self.path = _LOG_DIR / f"measure-{os.getpid()}-{stamp}.jsonl"
        self._fh = open(self.path, "a", buffering=1)  # line-buffered
        self._t0 = time.monotonic()
        logger.warning(
            "[measure] MEASUREMENT MODE ON — content-free telemetry → {} "
            "(no transcript text is logged)",
            self.path,
        )

    def emit(self, tap: str, kind: str, **fields: Any) -> None:
        rec = {
            "t_mono": round(time.monotonic() - self._t0, 6),
            "t_wall": round(time.time(), 3),
            "tap": tap,
            "kind": kind,
            **fields,
        }
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001 — best-effort close
            pass


class TurnState:
    """In-memory-ONLY accumulation of the provisional user turn. Never serialized.
    Shared by TapA (writes) and TapB (reads at finalization)."""

    def __init__(self) -> None:
        self._snapshots: list[str] = []  # accumulated-provisional after each segment (RAM only)

    def add_segment(self, seg: str) -> int:
        prev = self._snapshots[-1] if self._snapshots else ""
        accum = f"{prev} {seg}".strip() if prev else (seg or "").strip()
        self._snapshots.append(accum)
        return len(self._snapshots)

    def summarize_against(self, final_text: str) -> dict[str, Any]:
        """Compute derived booleans comparing the finalized turn to the provisionals.
        Returns telemetry only — no text."""
        snaps = [_norm(s) for s in self._snapshots]
        final = _norm(final_text)
        n = len(snaps)
        if n == 0:
            # No pre-finalization transcript → an injected (/say) text turn, not a spoken
            # turn. Excluded from the spoken-turn metrics.
            return {"inject": True, "num_segments": 0}
        # The LAST snapshot is taken AT the finalizing pause (~0 ms overlap — trivially
        # equals the final). The speculation-hit win requires a speculation fired at an EARLIER
        # (INCOMPLETE) pause — one that had prefill time — to match the final text.
        non_final = snaps[:-1]  # provisionals at INCOMPLETE pauses (had overlap opportunity)
        return {
            "inject": False,
            "num_segments": n,
            "num_incomplete_pauses": n - 1,  # speculations that would fire WITH overlap
            "final_len": len(final),
            # the real speculation-hit rate: a time-advantaged provisional == final.
            "useful_match": final in non_final,
            # accumulation sanity (should be ~always True): last snapshot == final turn.
            "final_matches_last": final == snaps[-1],
            # wasted-speculation signature: the last INCOMPLETE-pause provisional is a
            # strict prefix of the final (predicted dominant case on halting turns).
            "last_incomplete_is_strict_prefix": (
                n >= 2 and final != snaps[-2] and final.startswith(snaps[-2])
            ),
        }

    def reset(self) -> int:
        n = len(self._snapshots)
        self._snapshots = []
        return n


class MeasureTapA(FrameProcessor):
    """Between `stt` and `user_agg`. Pre-finalization signal → turn state + telemetry."""

    def __init__(self, log: MeasurementLog | None, turn: TurnState | None) -> None:
        super().__init__()
        self._log = log
        self._turn = turn

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self._log is not None:
            try:
                if isinstance(frame, TranscriptionFrame):
                    txt = frame.text or ""
                    idx = self._turn.add_segment(txt) if self._turn else 0
                    self._log.emit(
                        "A", "transcription",
                        seg_index=idx, seg_len=len(txt),
                        finalized=bool(getattr(frame, "finalized", False)),
                    )
                elif isinstance(frame, MetricsFrame):
                    for d in getattr(frame, "data", []) or []:
                        if hasattr(d, "is_complete") and hasattr(d, "probability"):
                            self._log.emit(
                                "A", "turn_verdict",
                                is_complete=bool(d.is_complete),
                                probability=float(d.probability),
                                e2e_ms=float(getattr(d, "e2e_processing_time_ms", 0.0) or 0.0),
                            )
                elif isinstance(frame, VADUserStartedSpeakingFrame):
                    self._log.emit("A", "vad_start")
                elif isinstance(frame, VADUserStoppedSpeakingFrame):
                    self._log.emit("A", "vad_stop")
            except Exception as exc:  # noqa: BLE001 — logging must never break the loop
                logger.warning("[measure] TapA error ({}) — passing frame through", type(exc).__name__)
        await self.push_frame(frame, direction)


class MeasureTapB(FrameProcessor):
    """Between `user_agg` and `llm`. Finalization → derived booleans (content-free)."""

    def __init__(self, log: MeasurementLog | None, turn: TurnState | None) -> None:
        super().__init__()
        self._log = log
        self._turn = turn
        self._req_seq = 0  # monotonic per-LLM-request counter (orders the hash ladders)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self._log is not None and isinstance(frame, LLMContextFrame):
            try:
                final = _final_user_text(frame) or ""
                summary = self._turn.summarize_against(final) if self._turn else {}
                self._log.emit("B", "finalize", **summary)
                if self._turn:
                    self._turn.reset()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[measure] TapB error ({}) — passing frame through", type(exc).__name__)
            # Prompt-prefix hash ladder — the cache-buster hunt (see helpers above).
            # This is the SAME context the downstream `llm` stage is about to send, so
            # the ladder mirrors the request's cacheable prefix.
            try:
                self._req_seq += 1
                ctx = getattr(frame, "context", None)
                msgs = _all_messages(ctx) if ctx is not None else []
                # Deterministic serialization; text is hashed but NEVER emitted.
                serialized = "\n".join(f"{r}\x1f{c}" for r, c in msgs)
                total, rungs = _hash_ladder(serialized)
                self._log.emit("B", "prompt_ladder",
                               req_seq=self._req_seq, num_msgs=len(msgs),
                               total_chars=total, ladder=rungs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[measure] ladder error ({})", type(exc).__name__)
        await self.push_frame(frame, direction)


class MeasureObserver(BaseObserver):
    """Pipeline-wide. Captures LLM TTFB + prompt_tokens + cache-read per turn (M5/A3)."""

    def __init__(self, log: MeasurementLog | None) -> None:
        super().__init__()
        self._log = log
        self._seen: set = set()

    async def on_push_frame(self, data: FramePushed) -> None:
        if self._log is None:
            return
        frame = data.frame
        if not isinstance(frame, MetricsFrame):
            return
        if frame.id in self._seen:
            return
        self._seen.add(frame.id)
        try:
            for md in frame.data or []:
                if isinstance(md, TTFBMetricsData):
                    self._log.emit("O", "ttfb", processor=md.processor, value_s=round(md.value, 4))
                elif isinstance(md, LLMUsageMetricsData):
                    u = md.value
                    self._log.emit(
                        "O", "usage", processor=md.processor,
                        prompt_tokens=u.prompt_tokens,
                        completion_tokens=u.completion_tokens,
                        total_tokens=u.total_tokens,
                        cache_read=u.cache_read_input_tokens,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[measure] observer error ({})", type(exc).__name__)


def _final_user_text(frame: LLMContextFrame) -> str | None:
    """Best-effort extraction of the finalized user-turn text (used only in-memory for the
    match comparison; never logged). Defensive against LLMContext API shape."""
    ctx = getattr(frame, "context", None)
    if ctx is None:
        return None
    msgs = None
    for accessor in ("get_messages", "messages"):
        obj = getattr(ctx, accessor, None)
        if obj is None:
            continue
        msgs = obj() if callable(obj) else obj
        break
    if not msgs:
        return None
    last = msgs[-1]
    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
    return content if isinstance(content, str) else (str(content) if content is not None else None)
