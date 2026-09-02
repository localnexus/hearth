"""mlx_tts_service.py — Chatterbox-Turbo TTS as a pipecat TTSService.

Wraps mlx-audio's ChatterboxTurboTTS in the pipecat TTSService interface so the
rest of the pipeline (VAD, STT, LLM, transport) is unchanged — only the TTS layer
runs synthesis in-process via MLX.

Key design decisions documented inline:
- CONDITIONALS: precompute the default voice's conditionals ONCE at init via
  prepare_conditionals(); generate() calls without ref_audio= reuse self._conds.
  Source-verified: chatterbox_turbo.py stream_generate() lines 1050-1060 check
  `self._conds is not None` and use it when ref_audio is omitted.

- THREADING: MLX's Metal GPU streams are thread-local — a stream created (or
  first used) on thread A does NOT exist on thread B.  Calling model.generate()
  from any thread other than the one that initialised the model crashes with
  "There is no Stream(gpu, N) in current thread".  The fix: use a single-thread
  ThreadPoolExecutor (max_workers=1) for BOTH model loading AND every synthesis
  call.  Since the executor always dispatches to the same worker thread, MLX's
  per-thread stream context is consistent across all calls.  Audio chunks are
  forwarded from the worker to the event loop via asyncio.Queue so they stream
  out as produced — first frame at ~1.4 s, not after the full utterance.  The
  asyncio event loop itself is never blocked.

- CHUNKING: each GenerationResult from mlx-audio is ~streaming_interval seconds
  of audio; we yield one TTSAudioRawFrame per GenerationResult rather than
  sub-chunking to self.chunk_size.  At streaming_interval=2.0 s each frame is
  ~96 kB — within pipecat's transport buffer; no click artefacts observed in
  Step 0.  Can be sub-chunked if transport jitter appears in Step 2.

- whole_response OFF: we do NOT install WholeResponseTextAggregator.  Default
  pipecat sentence aggregation fires run_tts on the LLM's first sentence, giving
  ~1.4 s TTFA instead of waiting for the whole reply.
"""

# ─── STABLE CORE ────────────────────────────────────────────────────────────────
# One cohesive TTS service. Do NOT grow it for new engines — a different TTS engine
# is a NEW sibling module / TTSService subclass, selected via config.
# Sanctioned seams:  • live knobs already flow in via config_reload
#   (tts.set_synth_params / tts.set_ref_wav) — extend those hooks, not this class.
# ────────────────────────────────────────────────────────────────────────────────

import asyncio
import concurrent.futures
import json
import logging
import re
import time
import wave
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mlx.core as mx
import numpy as np

from pipecat.frames.frames import Frame, TTSAudioRawFrame
from pipecat.services.tts_service import TTSService

from hearth.config import config_loader
from hearth.tts import paralinguistics
from hearth.tts import tag_profiles
from hearth.tts.params import SAMPLE_RATE  # re-export: engine rate, backend-neutral owner

logger = logging.getLogger(__name__)

# ── Paralinguistic strip log ──────────────────────────────────────────────────
# `DATA/logs/` is the runtime-data home — the code tree never receives runtime writes
# (same anchor as serve/tts_prep.py's copy of this log). Append-only JSONL, reviewed
# after a conversation rather than watched during one (you're talking to the companion,
# not reading a scrollback).
_STRIP_LOG_DIR: Path = config_loader.DATA_DIR / "logs"
_STRIP_LOG: Path = _STRIP_LOG_DIR / "paralinguistic-strips.jsonl"


def _log_strips(strips: list[dict], context_id) -> None:
    """Record bracketed non-cue tokens the transformer removed.

    UNKNOWN strips are the signal — a tag the model reached for that we don't yet
    support; known families are already-made decisions, kept out of the logger to
    stay quiet. Best-effort by design: a logging fault must never break the voice
    loop, so every failure is swallowed to debug.
    """
    for s in strips:
        if not s["known"]:
            logger.info(
                "MLXAudioTTSService: stripped UNKNOWN paralinguistic tag %r "
                "(context_id=%s) — novel tag, consider a mapping",
                s["token"], context_id,
            )
    try:
        _STRIP_LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        with _STRIP_LOG.open("a") as fh:
            for s in strips:
                fh.write(json.dumps({
                    "ts": stamp,
                    "token": s["token"],
                    "known": s["known"],
                    "context_id": str(context_id),
                }) + "\n")
    except Exception as exc:               # never let a log write break TTS
        logger.debug("MLXAudioTTSService: strip-log write failed: %r", exc)


# ── Module-level constants ────────────────────────────────────────────────────

MODEL_REPO: str = "mlx-community/chatterbox-turbo-fp16"
"""HuggingFace repo for the pre-converted MLX Chatterbox-Turbo weights.
Do NOT use the raw ResembleAI repo — it has no config.json and mlx-audio
cannot load it.  The mlx-community repo includes model.safetensors + config.json.
Weights are already cached on this machine from the Step 0 spike; load is ~0.9 s.
"""

DEFAULT_REF_WAV: str = str(
    config_loader.resolve_data_path("characters/example/voices/default/sample.wav")
)
"""Reference WAV for the built-in default voice — the shipped example bundle's
`default` clip (public domain; mono, 24 kHz, Int16).  Chatterbox clones zero-shot
from this clip at init (prepare_conditionals); no training/embeddings.

Resolved through the config loader (data root, then the engine tree), never a
literal absolute path, so the tree stays relocatable.
This is only the FALLBACK: the live voice comes from config/active.toml via
config_loader → bot.py passes `ref_wav=_CFG.ref_wav` explicitly.  To clone a
different voice, add a bundle under characters/ and select it there — don't edit
this (mono, ~24 kHz, > 5 s).
"""

# SAMPLE_RATE (24 kHz mono, Chatterbox-Turbo's fixed output rate) moved to
# hearth.tts.params (backend-neutral owner); re-exported via the import above.

STREAMING_INTERVAL: float = 2.0
"""Seconds of audio per mlx-audio GenerationResult chunk.
At 2.0 s each frame is ~96 KB of int16 PCM.  Smaller values yield lower
TTFA at the cost of more Metal kernel launches per utterance.
"""

# ── Sentinel objects for queue end-of-stream signalling ───────────────────────
_STREAM_DONE = object()
_STREAM_ERROR_TAG = object()


class MLXAudioTTSService(TTSService):
    """Chatterbox-Turbo TTS (mlx-audio) as a pipecat TTSService.

    One model instance is loaded at construction time and shared for all
    utterances.  The default voice's conditionals are also pre-computed once — the
    per-call ref_audio= re-encoding overhead (~0.2 s on warm runs) is eliminated.

    Threading model
    ---------------
    MLX Metal GPU streams are thread-local.  A stream initialised on thread A
    does not exist on thread B and any MLX operation on thread B crashes with
    "There is no Stream(gpu, N) in current thread".  To work around this, a
    single-worker ThreadPoolExecutor is created at __init__ time.  The model is
    loaded on that executor's worker thread, and all synthesis calls are also
    dispatched to the same thread.  asyncio.Queue bridges results back to the
    event loop so they stream out as produced.

    Use in bot.py
    -------------
    Do NOT pass whole_response=True — default sentence aggregation is correct here.
    """

    def __init__(
        self,
        model_repo: str = MODEL_REPO,
        ref_wav: str = DEFAULT_REF_WAV,
        dump_dir: Optional[str] = None,
        synth_params: Optional[dict] = None,
        **kwargs,
    ) -> None:
        """Load model and pre-compute the default voice's conditionals.

        Both operations run on the single-worker executor so they happen on the
        same thread as all future synthesis calls (MLX GPU stream thread-safety).

        Args:
            model_repo: HuggingFace repo id for the mlx-community Chatterbox
                        Turbo weights.  Defaults to fp16 (best quality).
            ref_wav:    Path to the reference WAV for voice cloning.  Defaults
                        to the shipped default clip.
            dump_dir:   If set, every synthesised utterance is written to this
                        directory as a WAV (named with its text) plus a line in
                        manifest.tsv — for capturing prosody artifacts in the wild.
                        None (default) = no dump,
                        zero overhead.  Barge-in-truncated utterances are captured
                        too, tagged _TRUNC.
            **kwargs:   Forwarded to TTSService (e.g. push_text_frames=False).
        """
        # sample_rate MUST be set before super().__init__ so that chunk_size
        # (which uses self.sample_rate) is available immediately.
        super().__init__(sample_rate=SAMPLE_RATE, **kwargs)

        # Single-worker executor: every MLX call (load + all synthesis) happens
        # on the same OS thread, keeping MLX's per-thread GPU stream consistent.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mlx-tts"
        )

        def _load() -> None:
            """Load model and precompute conditionals on the executor thread."""
            from mlx_audio.tts.utils import load_model  # import here to keep it on-thread
            self._model = load_model(model_repo)
            # prepare_conditionals() sets self._model._conds (a Conditionals
            # dataclass with t3 and gen fields).  Source evidence for safe reuse:
            # chatterbox_turbo.py stream_generate() lines 1050-1060 assert
            # `self._conds is not None` and use it when ref_audio= is omitted.
            self._model.prepare_conditionals(ref_wav)
            logger.info("MLXAudioTTSService: model loaded and conditionals ready")

        # Block until the model is loaded — callers must not call run_tts before
        # __init__ returns, which is the normal Python constructor guarantee.
        future = self._executor.submit(_load)
        future.result()  # re-raises any exception from the executor thread

        # ── Live-config synthesis state (config_reload.py drives these) ───────
        # self._synth holds the synth knobs splatted into generate(). EMPTY ⇒ the
        # call is byte-identical to before this feature (generate() uses its own
        # signature defaults). set_synth_params() swaps this dict atomically; run_tts
        # reads it ONCE per utterance (read-once + GIL = no lock needed).
        self._synth: dict = dict(synth_params) if synth_params else {}
        # Current reference clip (for voice diff/reporting; swapped by set_ref_wav).
        self._ref_wav: str = ref_wav

        # ── Optional utterance capture (in-the-wild prosody debugging) ────────
        self._dump_path: Optional[Path] = None
        self._dump_idx: int = 0
        if dump_dir:
            self._dump_path = Path(dump_dir)
            self._dump_path.mkdir(parents=True, exist_ok=True)
            manifest = self._dump_path / "manifest.tsv"
            if not manifest.exists():
                manifest.write_text("idx\ttime\tcompleted\tdur_s\ttext\n")
            logger.info("MLXAudioTTSService: TTS dump ENABLED → %s", self._dump_path)

        logger.info("MLXAudioTTSService: ready.")

    # ── Utterance capture ─────────────────────────────────────────────────────

    def _write_dump(
        self, text: str, pcm_chunks: list[bytes], completed: bool, idx: int
    ) -> None:
        """Write one captured utterance (WAV + manifest line).

        Called from run_tts's finally block so barge-in-truncated utterances are
        also saved (tagged _TRUNC).  Best-effort: capture must never break the
        live loop, so failures are logged and swallowed.
        """
        if self._dump_path is None or not pcm_chunks:
            return
        try:
            pcm = b"".join(pcm_chunks)
            dur = len(pcm) / (SAMPLE_RATE * 2)
            snippet = re.sub(r"[^A-Za-z0-9 ]+", "", text)[:40].strip().replace(" ", "_")
            tag = "" if completed else "_TRUNC"
            name = f"utt_{idx:04d}_{snippet or 'empty'}{tag}.wav"
            with wave.open(str(self._dump_path / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm)
            with open(self._dump_path / "manifest.tsv", "a") as m:
                m.write(
                    f"{idx:04d}\t{time.strftime('%Y-%m-%d %H:%M:%S')}\t"
                    f"{'yes' if completed else 'TRUNC'}\t{dur:.2f}\t{text}\n"
                )
        except Exception as exc:  # never let capture break the live loop
            logger.warning("MLXAudioTTSService: dump write failed: %s", exc)

    # ── Live-config setters (driven by config_reload.py at turn boundaries) ────

    def set_synth_params(self, params: dict) -> None:
        """Swap the synth-knob dict splatted into generate() (FREE tier).

        A single atomic attribute rebind (GIL) of a fresh dict — no lock. run_tts
        reads self._synth once at entry, so a swap mid-utterance can't tear the
        in-flight synthesis; it takes effect on the next utterance. Always assign a
        NEW dict (never mutate in place) so read-once stays coherent.
        """
        self._synth = dict(params)

    def set_ref_wav(self, path: str) -> "concurrent.futures.Future":
        """Re-clone the voice by recomputing conditionals from a new clip (HIDEABLE).

        prepare_conditionals() is an MLX call and MUST run on the single-worker
        executor (GPU-stream thread affinity), so it is SUBMITTED, not called inline
        on the pipeline thread. Returns the Future so the caller can await it
        (asyncio.wrap_future) — masked under LLM think-time (~0.2 s). Because the
        executor is FIFO single-worker, a re-prepare submitted at a turn boundary
        completes before that turn's first run_tts synthesis is submitted.
        """
        def _reprepare() -> None:
            self._model.prepare_conditionals(path)
            logger.info("MLXAudioTTSService: conditionals re-prepared from %s", path)

        fut = self._executor.submit(_reprepare)
        self._ref_wav = path
        return fut

    # ── Core synthesis ──────────────────────────────────────────────────────

    async def run_tts(
        self,
        text: str,
        context_id: str,
    ) -> AsyncGenerator[Frame | None, None]:
        """Synthesise `text` and yield TTSAudioRawFrame chunks as they arrive.

        The synchronous mlx-audio generator runs in the single-worker executor
        thread (same thread the model was loaded on — required for MLX GPU stream
        affinity).  Audio chunks are forwarded to the event loop via asyncio.Queue
        so they stream out as produced — first frame typically ~1.4 s after call
        entry.

        Args:
            text:       Sentence (or short paragraph) to synthesise.  Pipecat's
                        default TTSService aggregator sends one sentence at a time.
            context_id: Opaque barge-in tracking token; threaded through to each
                        TTSAudioRawFrame unchanged.

        Yields:
            TTSAudioRawFrame — int16 PCM, 24 kHz, mono.  One frame per mlx-audio
            GenerationResult (~2 s of audio at default streaming_interval=2.0).
        """
        # ── Prosody-artifact fixes (source-verified against live captures) ────
        # Two distinct mechanisms produce the "between-phrase" / trailing glitches:
        #
        #  1. ORPHAN fragments — an ASCII "..." run gets split by the sentence
        #     aggregator into a word + standalone "." fragments (capture: "One.."
        #     then a lone "."). Handing the TTS a WORD-LESS fragment makes it
        #     improvise ~1 s of non-word filler (a chuckle/breath — verified loud:
        #     utt_0008 RMS ~1730). The unicode ellipsis "…" never orphans (it's a
        #     single char that stays attached to its word).
        #
        #  2. TRAILING improvisation — even a word-anchored trailing run ("So……")
        #     occasionally blurts a loud tail (verified: a phrase ending "……" said
        #     4x, one instance's trailing RMS ~5-8x its twins). The longer the
        #     symbol run, the wider the window the model improvises into.
        #
        # Fix: collapse any run of 2+ dots/ellipses to a single "…" (stops the
        # orphan split AND shrinks the trailing window), then skip anything with
        # no alphanumeric character (the deterministic net for orphans that still
        # arrive pre-split). A single "…" is the confirmed-clean baseline.
        #
        #  3. NEWLINES — the LLM peppers replies with line breaks (paragraph gaps,
        #     the occasional stray list) that mean NOTHING to the ear but make
        #     Chatterbox stumble: a leading "\n\n" voices as an odd breath/pause,
        #     an embedded "\n" fractures the prosody mid-clause. Pipecat's sentence
        #     segmentation is punctuation-only (utils/string.py match_endofsentence
        #     — "\n" is not a boundary char), so newlines never carried structure;
        #     folding every whitespace run (newlines, tabs, doubled spaces) down to
        #     one space just gives the voice a single clean flowing line. Do this
        #     FIRST so the ellipsis/orphan rules below see already-flattened text.
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"[.…]{2,}", "…", text)
        # Repair malformed paralinguistic cue tags: an enclosed BARE cue root in
        # *…* / (…) / […] / {…} → the canonical [tag] (case/padding/morphology).
        # Then STRIP every remaining […] that isn't one of the nine — post-repair,
        # a bracketed non-cue can only be a stage direction Chatterbox would read
        # ALOUD. Leaves multi-word prose and non-square enclosures untouched. Runs
        # on already-flattened text. See paralinguistics.py.
        text, _strips = paralinguistics.normalize_with_report(text)
        if _strips:
            _log_strips(_strips, context_id)
        if not any(ch.isalnum() for ch in text):
            logger.debug(
                "MLXAudioTTSService: skipping word-less fragment %r (context_id=%s)",
                text, context_id,
            )
            return

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        # Read the live synth knobs ONCE per utterance (a single attribute read;
        # atomic under the GIL). A concurrent set_synth_params() swap can't tear this
        # utterance — worst case it takes effect on the next one (turn-boundary
        # semantics). EMPTY dict ⇒ generate() gets no synth kwargs ⇒ byte-identical
        # to before live-config.
        synth = self._synth
        # Paralinguistic tag envelope: a style tag in THIS
        # utterance overlays its calibrated knob deltas for this generate call
        # only — the envelope IS the call; the next utterance rides self._synth
        # untouched. No profiled tag ⇒ deltas {} ⇒ synth dict unchanged.
        try:
            _deltas = tag_profiles.deltas_for(text, tag_profiles.load_profiles("chatterbox-turbo"))
            if _deltas:
                synth = {**synth, **_deltas}
                logger.info("MLXAudioTTSService: tag envelope -> %s (this utterance only)", _deltas)
        except Exception as exc:  # profiles are an overlay, never a failure mode
            logger.warning("MLXAudioTTSService: tag-profile overlay failed (%s) — live knobs only", type(exc).__name__)

        def _run_sync() -> None:
            """Run the synchronous generator on the executor thread.

            Each GenerationResult's audio is converted from mx.array float32
            [-1,1] to int16 PCM bytes and pushed onto the asyncio queue.
            A sentinel is pushed at the end; exceptions are wrapped and pushed
            so they propagate back to the event loop.
            """
            try:
                for res in self._model.generate(
                    text=text,
                    stream=True,
                    streaming_interval=STREAMING_INTERVAL,
                    # ref_audio intentionally omitted — conditionals were
                    # precomputed in __init__ via prepare_conditionals().
                    **synth,  # live synth knobs (empty ⇒ engine defaults)
                ):
                    # Convert GenerationResult.audio (mx.array float32 [-1,1])
                    # → int16 PCM bytes.
                    audio_mx: mx.array = res.audio
                    mx.eval(audio_mx)
                    arr = np.array(audio_mx, dtype=np.float32).reshape(-1)
                    pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2")
                    raw_bytes = pcm.tobytes()
                    loop.call_soon_threadsafe(queue.put_nowait, raw_bytes)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, (_STREAM_ERROR_TAG, exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _STREAM_DONE)

        # Submit to the single-worker executor (same thread as model load).
        synth_future = self._executor.submit(_run_sync)

        # Utterance capture: assign an index up-front so the finally block can
        # write even if the consumer exits early (barge-in).
        dump_on = self._dump_path is not None
        dump_idx = self._dump_idx if dump_on else -1
        if dump_on:
            self._dump_idx += 1
        captured: list[bytes] = []
        completed = False

        first_frame = True
        try:
            while True:
                item = await queue.get()

                if item is _STREAM_DONE:
                    completed = True
                    # Collect the future to surface any exception that bypassed
                    # the queue (e.g. if the sentinel was sent before the error).
                    try:
                        synth_future.result(timeout=0)
                    except concurrent.futures.TimeoutError:
                        pass
                    break

                if isinstance(item, tuple) and len(item) == 2 and item[0] is _STREAM_ERROR_TAG:
                    raise item[1]

                raw_bytes: bytes = item  # type: ignore[assignment]
                if first_frame:
                    logger.debug(
                        "MLXAudioTTSService: first audio frame for context_id=%s", context_id
                    )
                    first_frame = False

                if dump_on:
                    captured.append(raw_bytes)

                yield TTSAudioRawFrame(
                    audio=raw_bytes,
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    context_id=context_id,
                )
        finally:
            # If the consumer exits early (barge-in cancellation), cancel the
            # background future if possible so the Metal GPU work is abandoned.
            synth_future.cancel()
            # Capture the utterance (audio + text) if dumping is enabled — this
            # runs on both clean completion and early barge-in exit, so truncated
            # utterances are saved too (tagged _TRUNC via completed=False).
            if dump_on:
                self._write_dump(text, captured, completed, dump_idx)
