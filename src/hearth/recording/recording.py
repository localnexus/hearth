"""
recording.py — session recording: the Record button's engine.

The shape:

  capture LOSSLESS in the live path, transcode on stop
  ─────────────────────────────────────────────────────
  Pipeline frames are already raw int16 PCM, so the live path writes stdlib-`wave`
  WAV and nothing else — no encoder ever runs inside the real-time audio loop.
  The shareable mixdown (M4A/AAC) is an ffmpeg post-pass on stop, off the
  critical path.

  stems, then mix
  ───────────────
  Each source is captured as its own isolated stem at its NATIVE rate
  (TTS 24 kHz · mic 16 kHz · system-loopback at the MONITORING DEVICE's clock
  rate, derived at mirror-engage — BT headsets commonly run 44.1 kHz); the
  mixdown resamples to 48 kHz only at mix time. Keeps both the "what I heard" file and the raw
  material to reconstruct it — same ethic as the theme-variant archive.

Exports:
    Recorder      — arm/disarm lifecycle, stem writers, on-stop ffmpeg mixdown
    TTSRecordTap  — passive tap after `tts` (captures TTSAudioRawFrame)
    MicRecordTap  — passive tap after `mute_gate` (captures InputAudioRawFrame;
                    placed after the gate ON PURPOSE so Mute is honored — muted
                    audio never touches disk)

Wiring (bot.py):
    [transport.input(), mute_gate, mic_record_tap, vad, …, tts, tts_record_tap,
     transport.output(), …]

Output layout (under the git-ignored sessions/ tree — sensitive plaintext,
local-only):
    characters/<character>/captures/<name>_<YYYY.MM.DD.HH.MM>.m4a   ← mixdown (data root)
    characters/<character>/captures/<name>_<YYYY.MM.DD.HH.MM>.stems/
        tts.wav · mic.wav · music.wav · manifest.json

Passivity guarantee (the measure-tap contract): when disarmed, both taps are
byte-identical pass-throughs. When armed, capture is a try/except-guarded
buffered file write; ANY capture error disarms the recording and logs once —
it can never take the voice loop down with it.

Timeline note: stems are wall-clock aligned. Each writer zero-pads to the
session clock when a gap > _PAD_GAP_S opens between bursts, so silences between
turns survive into the stems and the mix lines up. TTS frames arrive at
GENERATION pace (ahead of playback); the pad-to-clock at the next burst
self-corrects to within ~TTFA, which is inaudible at conversational scale.
"""

# ─── STABLE CORE ────────────────────────────────────────────────────────────────
# Recording, shipped + live-verified. Do NOT grow Recorder for unrelated features.
# New capture sources = NEW passive taps + a sibling module following the
# TTSRecordTap / MicRecordTap pattern.
# Sanctioned seams:  • the passive-tap pattern (new FrameProcessor tap + stem)
# ────────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re
import signal
import sys
import time
import wave
from pathlib import Path
from typing import Optional

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Gaps between bursts larger than this are zero-padded to the session clock;
# smaller inter-frame jitter just concatenates (within-utterance frames arrive
# faster than real time — expected position lags written position → no pad).
_PAD_GAP_S = 0.25

# Mixdown encoder ladder: Apple's AudioToolbox AAC first (best quality on this
# box), plain aac as the portable fallback. Both verified present (ffmpeg 8.1.1).
_AAC_ENCODERS = ("aac_at", "aac")

_TS_FMT = "%Y.%m.%d.%H.%M"  # <name>_<YYYY.MM.DD.HH.MM>.<format>

# The output-mirror helper (helpers/hearth_audio_route.swift, CoreAudio) — macOS +
# BlackHole only. BlackHole only hears audio ROUTED to it, so on record-start-with-
# music the Recorder engages a temporary stacked aggregate ("Hearth Record Mirror")
# that wraps the user's CURRENT output device + BlackHole, and releases it (restoring
# the previous device, destroying the mirror) on stop. Compiled lazily on first use;
# every failure is fail-soft (capture proceeds against BlackHole as-is — manual
# routing may exist). The helpers ship inside this package (helpers/ beside this
# module); the compiled binary is machine-local build output, written next to the
# source on first use. If a helper is missing or fails to build, it simply reports
# unavailable and the music/mirror features degrade — tts/mic capture is unaffected.
_TOOLS_DIR = Path(__file__).resolve().parent / "helpers"
_ROUTE_SRC = _TOOLS_DIR / "hearth_audio_route.swift"
_ROUTE_BIN = _TOOLS_DIR / "hearth-audio-route"

# Music stem — PortAudio capture helper subprocess (helpers/music_capture.py).
# NOT ffmpeg/avfoundation: ffmpeg 8.1.1's avfoundation input silently loses
# ~10% of buffers from any device on this box (short stem + splice-click
# static). The helper also owns device DISCOVERY
# (--probe) so the bot process never touches a second PortAudio instance.
_MUSIC_HELPER = _TOOLS_DIR / "music_capture.py"


def _slug(name: str) -> str:
    """Filesystem-safe capture name: keep [A-Za-z0-9._-], collapse the rest to '-'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.")
    return s or "session"


# ── Stem writer ────────────────────────────────────────────────────────────────


class StemWriter:
    """One mono int16 WAV stem at its native rate, wall-clock aligned.

    append() zero-pads up to the session clock when a real gap opened (silence
    between turns / muted stretch), then writes the burst. stdlib `wave` on a
    buffered file — microseconds per frame, safe in the frame path.
    """

    def __init__(self, path: Path, rate: int, t0: float):
        self.path = path
        self.rate = rate
        self._t0 = t0
        self._written = 0  # samples
        self._wf = wave.open(str(path), "wb")
        self._wf.setnchannels(1)
        self._wf.setsampwidth(2)  # int16
        self._wf.setframerate(rate)

    def append(self, data: bytes, now: float) -> None:
        expected = int((now - self._t0) * self.rate)
        gap = expected - self._written
        if gap > int(_PAD_GAP_S * self.rate):
            self._wf.writeframes(b"\x00\x00" * gap)
            self._written += gap
        self._wf.writeframes(data)
        self._written += len(data) // 2

    def close(self) -> dict:
        self._wf.close()
        return {
            "file": self.path.name,
            "rate": self.rate,
            "samples": self._written,
            "duration_s": round(self._written / self.rate, 2),
        }


# ── Route helper (module-level: must be callable BEFORE any pipeline exists) ──


async def run_route(*args: str) -> Optional[dict]:
    """Run the CoreAudio route helper, compiling it on first use (swiftc).
    Returns the helper's JSON, or None on ANY failure — never raises."""
    try:
        if not _ROUTE_SRC.exists():
            return None
        if (not _ROUTE_BIN.exists()
                or _ROUTE_BIN.stat().st_mtime < _ROUTE_SRC.stat().st_mtime):
            logger.info("[record] compiling audio-route helper (one-time)…")
            proc = await asyncio.create_subprocess_exec(
                "swiftc", "-O", str(_ROUTE_SRC), "-o", str(_ROUTE_BIN),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.communicate(), timeout=180)
            if proc.returncode != 0 or not _ROUTE_BIN.exists():
                logger.warning("[record] route-helper compile failed")
                return None
        proc = await asyncio.create_subprocess_exec(
            str(_ROUTE_BIN), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return json.loads(out.decode())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[record] audio-route helper failed ({})", type(exc).__name__)
        return None


async def repair_routing() -> None:
    """Heal a LEAKED output mirror (crashed stop / killed bot) — restore the
    physical device the mirror itself remembers, destroy the mirror.

    MUST run at every bot startup BEFORE the pipeline is even CONSTRUCTED:
    PortAudio snapshots the device list at initialization (transport
    construction), so destroying the mirror after that leaves PortAudio holding
    a stale default-device reference → stream-open fails with '!obj'/-9986 and
    the companion's voice is dead for the session. Module-level on purpose — needs no Recorder,
    no pipeline. Never raises.

    Origin incident: a bot killed mid-stop leaked the mirror;
    the next bot bound its output to it, the companion's voice fed BlackHole, and every
    later take's music stem carried a delayed copy → 'echo layers'."""
    res = await run_route("repair")
    if res is None:
        return
    if res.get("repaired"):
        logger.warning("[record] leaked output mirror found and repaired "
                       "(restored: {}) — a previous run died mid-recording",
                       res.get("restored") or "default unchanged")
    else:
        logger.debug("[record] routing clean — no leaked mirror")


# ── Recorder ───────────────────────────────────────────────────────────────────


class Recorder:
    """Owns the record lifecycle. Constructed once at startup; driven by the
    /record/* routes; fed by the two taps via append()."""

    def __init__(
        self,
        character: str,
        base_dir: Path,
        tts_rate: int,
        mic_rate: int,
        default_name: str = "session",
    ):
        self.character = character
        self.base_dir = Path(base_dir)
        self.tts_rate = tts_rate
        self.mic_rate = mic_rate
        self.default_name = _slug(default_name)
        self.music_device: Optional[tuple[int, str]] = None  # (PortAudio idx, name)
        # idx is informational — the helper re-resolves by NAME at each spawn
        # (its fresh PortAudio sees the device set as of record-press).

        self._armed = False
        self._error: Optional[str] = None
        self._t0 = 0.0
        self._started_iso: Optional[str] = None
        self._name: Optional[str] = None
        self._stems_dir: Optional[Path] = None
        self._mix_path: Optional[Path] = None
        self._writers: dict[str, StemWriter] = {}
        self._music_proc: Optional[asyncio.subprocess.Process] = None
        self._music_log = None  # ffmpeg stderr file handle while capturing
        self._route_prev_uid: Optional[str] = None   # set while the output mirror is engaged
        self._route_prev_name: Optional[str] = None
        self._last_result: Optional[dict] = None

    # ── passive-side API (called from the frame path — must never raise) ──────

    @property
    def armed(self) -> bool:
        return self._armed

    def append(self, stem: str, data: bytes) -> None:
        if not self._armed:
            return
        w = self._writers.get(stem)
        if w is None:
            return
        try:
            w.append(data, time.monotonic())
        except Exception as exc:  # noqa: BLE001 — NEVER let capture kill the loop
            self._armed = False
            self._error = f"capture write failed ({type(exc).__name__}) — recording disarmed"
            logger.error("[record] {}", self._error)

    # ── music loopback (P3 best-effort) ───────────────────────────────────────

    async def repair_routing(self) -> None:
        """Instance alias of module-level repair_routing() (engage-retry path)."""
        await repair_routing()

    async def detect_music_device(self) -> None:
        """One-shot loopback-device scan (BlackHole / Loopback) via the capture
        helper's --probe mode — a subprocess, so the bot process never inits a
        second PortAudio. Absent → the panel's music tickbox renders disabled.
        Never raises."""
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(_MUSIC_HELPER), "--probe",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            info = json.loads(out.decode(errors="replace") or "{}")
            if info.get("found"):
                self.music_device = (int(info.get("index", -1)), str(info["name"]))
                logger.info("[record] loopback device found: [{}] {}",
                            *self.music_device)
                return
            logger.info("[record] no loopback audio device — music tickbox disabled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[record] device scan failed ({}) — music tickbox disabled",
                           type(exc).__name__)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, name: Optional[str] = None, mic: bool = False,
                    music: bool = False) -> dict:
        if self._armed:
            return {"ok": False, "error": "already recording"}
        self._error = None
        self._name = _slug(name) if name else self.default_name
        ts = _dt.datetime.now().strftime(_TS_FMT)
        base = f"{self._name}_{ts}"
        char_dir = self.base_dir  # the companion's own captures dir (data root)
        # Back-to-back takes: the timestamp is minute-resolution, so a second
        # segment with the same name inside the same minute would land on the
        # SAME path and overwrite the first. Suffix -2, -3, … — never overwrite
        # a capture (same ethic as .archive/: recordings are irreplaceable).
        take = base
        n = 2
        while (char_dir / f"{take}.stems").exists() or (char_dir / f"{take}.m4a").exists():
            take = f"{base}-{n}"
            n += 1
        self._stems_dir = char_dir / f"{take}.stems"
        self._mix_path = char_dir / f"{take}.m4a"
        self._stems_dir.mkdir(parents=True, exist_ok=False)

        self._t0 = time.monotonic()
        self._started_iso = _dt.datetime.now().isoformat(timespec="seconds")
        self._writers = {
            "tts": StemWriter(self._stems_dir / "tts.wav", self.tts_rate, self._t0)
        }
        if mic:
            self._writers["mic"] = StemWriter(
                self._stems_dir / "mic.wav", self.mic_rate, self._t0)

        music_on = False
        if music and self.music_device is not None:
            music_on = await self._start_music()
        elif music:
            logger.warning("[record] music requested but no loopback device — skipped")

        self._armed = True
        logger.info("[record] ● armed → {} (mic={} music={})",
                    self._mix_path, mic, music_on)
        return {"ok": True, "recording": True, "mic": mic, "music": music_on,
                "path": str(self._mix_path)}

    async def _route(self, *args: str) -> Optional[dict]:
        return await run_route(*args)

    async def _start_music(self) -> bool:
        """Engage the output mirror (current device + BlackHole, rate-aligned),
        then spawn the PortAudio capture helper on BlackHole → music.wav (stereo,
        at the mirror master's clock rate — the route helper derives and reports
        it, so the stem is labeled truthfully whatever the monitoring device runs
        at). Mirror-engage failure is NOT fatal — capture proceeds against
        BlackHole as-is (manual routing), at the 48 kHz legacy assumption."""
        res = await self._route("engage")
        if not (res and res.get("ok")):
            # A leaked mirror (crashed previous stop) makes engage refuse — heal
            # and retry ONCE rather than silently capturing a mis-routed BlackHole.
            await self.repair_routing()
            res = await self._route("engage")
        music_rate = 48000
        if res and res.get("ok"):
            self._route_prev_uid = res.get("previous_uid")
            self._route_prev_name = res.get("previous_name")
            music_rate = int(res.get("rate") or 48000)
            if res.get("rate_aligned") is False:
                logger.warning("[record] BlackHole could not be rate-aligned to the "
                               "monitoring device — music stem may carry clock skew")
            logger.info("[record] output mirror engaged around '{}' @ {} Hz — "
                        "restores on stop", self._route_prev_name, music_rate)
        else:
            logger.warning("[record] mirror engage failed ({}) — capturing BlackHole "
                           "as-is (manual routing?)",
                           (res or {}).get("error", "helper unavailable"))
        _idx, dev_name = self.music_device  # type: ignore[misc]
        try:
            # Helper stderr → music-capture.log in the stems dir: delivery stats
            # (audio-vs-wall ratio) and overflow counts stay visible evidence,
            # never a silent loss.
            self._music_log = open(self._stems_dir / "music-capture.log", "wb")
            self._music_proc = await asyncio.create_subprocess_exec(
                sys.executable, str(_MUSIC_HELPER),
                "--device", dev_name, "--rate", str(music_rate),
                "--out", str(self._stems_dir / "music.wav"),
                stdout=asyncio.subprocess.DEVNULL, stderr=self._music_log,
            )
            logger.info("[record] music stem via '{}' @ {} Hz (PortAudio)",
                        dev_name, music_rate)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[record] music capture failed to start ({})",
                           type(exc).__name__)
            self._music_proc = None
            self._close_music_log()
            await self._release_mirror()  # don't leave routing switched with no capture
            return False

    def _close_music_log(self) -> None:
        if self._music_log is not None:
            try:
                self._music_log.close()
            except Exception:  # noqa: BLE001
                pass
            self._music_log = None

    async def _release_mirror(self) -> None:
        """Restore the pre-recording output device and destroy the mirror. No-op
        when no mirror was engaged. Never raises."""
        if self._route_prev_uid is None:
            return
        res = await self._route("release", self._route_prev_uid)
        if res and res.get("ok"):
            logger.info("[record] output mirror released — '{}' restored",
                        self._route_prev_name)
        else:
            logger.warning("[record] mirror release failed — restore output manually "
                           "(Control Center) if audio is off; a stale mirror is "
                           "cleaned up on the next engage")
        self._route_prev_uid = None
        self._route_prev_name = None

    async def stop(self) -> dict:
        if not self._armed and not self._writers:
            return {"ok": False, "error": "not recording"}
        self._armed = False
        stems: dict[str, dict] = {}
        for key, w in self._writers.items():
            try:
                stems[key] = w.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[record] stem '{}' close failed ({})",
                               key, type(exc).__name__)
        self._writers = {}

        mirror_was_engaged = self._route_prev_uid is not None
        had_music_proc = self._music_proc is not None
        if self._music_proc is not None:
            try:
                self._music_proc.send_signal(signal.SIGINT)  # graceful → valid WAV header
                await asyncio.wait_for(self._music_proc.wait(), timeout=5)
            except Exception:  # noqa: BLE001
                self._music_proc.kill()
            self._music_proc = None
            self._close_music_log()
        # Restore the user's output device promptly, and SHIELD the release so a
        # cancellation racing this stop (double Ctrl-C, killed shutdown) can't abort
        # it half-done. A hard SIGKILL can
        # still leak the mirror — repair_routing() at next startup is the true net.
        try:
            await asyncio.shield(self._release_mirror())
        except asyncio.CancelledError:
            raise  # the release task itself keeps running on the live loop
        except Exception:  # noqa: BLE001 — release never takes stop down
            pass
        if had_music_proc:
            music_wav = self._stems_dir / "music.wav"
            if music_wav.exists() and music_wav.stat().st_size > 44:
                try:
                    with wave.open(str(music_wav), "rb") as mf:
                        stems["music"] = {
                            "file": "music.wav", "rate": mf.getframerate(),
                            "samples": mf.getnframes(),
                            "duration_s": round(mf.getnframes() / mf.getframerate(), 2),
                        }
                except Exception:  # noqa: BLE001
                    pass

        # Mixdown: every non-empty stem, resampled to 48 kHz, mixed unnormalized.
        live = [k for k in ("tts", "mic", "music")
                if k in stems and stems[k]["samples"] > 0]
        mix_info: Optional[dict] = None
        if live:
            mix_info = await self._mixdown(live)
        else:
            logger.info("[record] nothing captured — no mixdown")

        manifest = {
            "character": self.character,
            "name": self._name,
            "started": self._started_iso,
            "stopped": _dt.datetime.now().isoformat(timespec="seconds"),
            "stems": stems,
            "mix": mix_info,
            "output_mirror": mirror_was_engaged,
            "error": self._error,
            "notes": "stems wall-clock aligned at native rates; mix = 48 kHz amix, "
                     "no normalization/processing (keep the record pristine); "
                     "music stem (if any) = BlackHole loopback of apps following the "
                     "system default, captured at the monitoring device's clock rate "
                     "(BlackHole rate-aligned to the mirror master at engage) — the "
                     "bot's own output binds to the physical device at startup, so "
                     "the companion's voice is NOT doubled into it",
        }
        try:
            (self._stems_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[record] manifest write failed ({})", type(exc).__name__)

        self._last_result = {
            "ok": True, "recording": False,
            "mix": str(self._mix_path) if mix_info else None,
            "stems": str(self._stems_dir), "captured": live,
            "duration_s": max((s["duration_s"] for s in stems.values()), default=0),
            "error": self._error,
        }
        logger.info("[record] ■ saved → {} (stems: {})",
                    self._mix_path if mix_info else "(empty)", ", ".join(live) or "none")
        return self._last_result

    async def _mixdown(self, live: list[str]) -> Optional[dict]:
        """WAV stems → one M4A at 48 kHz. aac_at first, aac fallback. Off the
        critical path (async subprocess on stop)."""
        inputs: list[str] = []
        for k in live:
            inputs += ["-i", str(self._stems_dir / f"{k}.wav")]
        if len(live) == 1:
            filt = ["-af", "aresample=48000"]
        else:
            chains = "".join(
                f"[{i}:a]aresample=48000[a{i}];" for i in range(len(live)))
            pads = "".join(f"[a{i}]" for i in range(len(live)))
            filt = ["-filter_complex",
                    f"{chains}{pads}amix=inputs={len(live)}:duration=longest:normalize=0[m]",
                    "-map", "[m]"]
        for enc in _AAC_ENCODERS:
            cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error"] + inputs
                   + filt + ["-c:a", enc, "-b:a", "192k", "-y", str(self._mix_path)])
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode == 0:
                    return {"file": self._mix_path.name, "encoder": enc,
                            "rate": 48000, "inputs": live}
                logger.warning("[record] mixdown via {} failed: {}", enc,
                               err.decode(errors="replace").strip()[:200])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[record] mixdown via {} error ({})", enc,
                               type(exc).__name__)
        self._error = "mixdown failed — stems kept (WAV)"
        return None

    # ── status (panel polling) ────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "recording": self._armed,
            "elapsed_s": round(time.monotonic() - self._t0, 1) if self._armed else 0,
            "stems": sorted(self._writers.keys())
            + (["music"] if self._music_proc is not None else []),
            "music_available": self.music_device is not None,
            "music_device": self.music_device[1] if self.music_device else None,
            "mirroring": self._route_prev_uid is not None,
            "error": self._error,
            "last": self._last_result,
        }


# ── The taps (passive; the measure-tap contract) ───────────────────────────────


class TTSRecordTap(FrameProcessor):
    """After `tts`, before transport.output(). Captures TTSAudioRawFrame when
    armed; byte-identical pass-through otherwise."""

    def __init__(self, recorder: Recorder, **kwargs):
        super().__init__(**kwargs)
        self._recorder = recorder

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self._recorder.append("tts", frame.audio)
        await self.push_frame(frame, direction)


class MicRecordTap(FrameProcessor):
    """After `mute_gate` (Mute honored — muted audio never touches disk), before
    VAD. Captures InputAudioRawFrame when armed AND the mic stem was requested."""

    def __init__(self, recorder: Recorder, **kwargs):
        super().__init__(**kwargs)
        self._recorder = recorder

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._recorder.append("mic", frame.audio)
        await self.push_frame(frame, direction)
