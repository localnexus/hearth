"""test_recording.py — headless proof of M7 session recording (recording.py).

Runs WITHOUT mic / LM Studio / the TTS model. Proves the load-bearing invariants
on the REAL artifacts (real pipecat FrameProcessor machinery via
pipecat.tests.utils.run_test; real ffmpeg for the mixdown):

  R1  slug           — capture names are filesystem-safe; empty → "session"
  R2  stem writer    — valid mono int16 WAV at native rate; a wall-clock gap
                       between bursts is zero-padded; within-burst frames
                       concatenate unpadded (the TTS-arrives-early case)
  R3  lifecycle e2e  — start → append → stop on a temp dir yields the stems dir,
                       manifest.json, and a REAL M4A mixdown (real ffmpeg),
                       named <name>_<YYYY.MM.DD.HH.MM>.m4a under <character>/
  R4  tap passivity  — disarmed taps pass every frame through untouched (the
                       measure-tap contract); an armed TTS tap captures the
                       exact frame bytes into the stem
  R5  guards         — double-start and stop-when-idle refuse cleanly; append
                       to an unrequested stem is a no-op; a capture error
                       DISARMS the recording instead of raising into the frame
                       path (the never-kill-the-loop guarantee)

  (Music/loopback capture needs a BlackHole device + routed output — hardware,
   not part of this headless harness; see M7 P3 [needs test].)

Run:  .venv/bin/python test_recording.py
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import wave
from pathlib import Path

from pipecat.frames.frames import InputAudioRawFrame, TTSAudioRawFrame
from pipecat.tests.utils import run_test

from hearth.recording import recording
from hearth.recording.recording import MicRecordTap, Recorder, StemWriter, TTSRecordTap

_PASS = 0
_FAIL = 0


def check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}")


# ── R1 — slug ─────────────────────────────────────────────────────────────────


def r1_slug():
    print("\nR1 — capture-name slug")
    check(recording._slug("my test!? take 2") == "my-test-take-2", "specials collapse to '-'")
    check(recording._slug("session-note_01.a") == "session-note_01.a", "safe chars survive")
    check(recording._slug("   ") == "session", "empty → 'session'")
    check(recording._slug("..--") == "session", "punctuation-only → 'session'")


# ── R2 — stem writer ──────────────────────────────────────────────────────────


def r2_stem_writer():
    print("\nR2 — stem writer (WAV validity + wall-clock padding)")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "tts.wav"
        w = StemWriter(p, rate=24000, t0=100.0)
        burst = b"\x01\x00" * 2400  # 0.1 s of samples
        # within-burst: arrives "early" (expected < written) → concatenate, no pad
        w.append(burst, now=100.05)
        w.append(burst, now=100.11)
        # after a real 1 s silence → zero-pad to the session clock
        w.append(burst, now=101.2)
        info = w.close()
        with wave.open(str(p), "rb") as rf:
            check(rf.getnchannels() == 1 and rf.getsampwidth() == 2
                  and rf.getframerate() == 24000, "mono int16 @ native 24 kHz")
            n = rf.getnframes()
            data = rf.readframes(n)
        # written = 2 bursts (4800) + pad to 1.2 s (28800-4800=24000) + burst (2400)
        check(n == 31200, f"padding lands on the session clock (frames={n}, expect 31200)")
        pad_region = data[4800 * 2:28800 * 2]
        check(pad_region == b"\x00" * len(pad_region), "gap region is pure silence")
        check(info["duration_s"] == 1.3, f"duration reported ({info['duration_s']}s)")


# ── R3 — lifecycle end-to-end (real ffmpeg mixdown) ───────────────────────────


async def _r3_async(base: Path) -> dict:
    rec = Recorder("testchar", base, tts_rate=24000, mic_rate=16000)
    out = {}
    out["start"] = await rec.start(name="my test!?", mic=True)
    rec.append("tts", b"\x10\x00" * 24000)  # 1 s
    rec.append("mic", b"\x08\x00" * 16000)  # 1 s
    out["stop"] = await rec.stop()
    out["status"] = rec.status()
    return out


def r3_lifecycle():
    print("\nR3 — lifecycle end-to-end (stems + manifest + real M4A mixdown)")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "captures"
        out = asyncio.run(_r3_async(base))
        check(out["start"]["ok"] and out["stop"]["ok"], "start/stop both ok")
        mix = out["stop"]["mix"]
        check(mix is not None and Path(mix).exists() and Path(mix).stat().st_size > 0,
              "M4A mixdown rendered by real ffmpeg")
        name_ok = bool(mix) and re.fullmatch(
            r"my-test_\d{4}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.m4a", Path(mix).name)
        check(bool(name_ok), f"filename = <name>_<YYYY.MM.DD.HH.MM>.m4a ({Path(mix).name if mix else '—'})")
        check(bool(mix) and Path(mix).parent.name == "testchar", "lands under <character>/")
        stems = Path(out["stop"]["stems"])
        check((stems / "tts.wav").exists() and (stems / "mic.wav").exists(),
              "both stems on disk")
        man = stems / "manifest.json"
        check(man.exists() and '"tts"' in man.read_text() and '"mic"' in man.read_text(),
              "manifest.json describes both stems")
        check(sorted(out["stop"]["captured"]) == ["mic", "tts"], "captured list correct")
        check(out["status"]["recording"] is False, "status disarmed after stop")


# ── R4 — tap passivity (real pipecat machinery) ───────────────────────────────


def _tts_frame(payload: bytes) -> TTSAudioRawFrame:
    return TTSAudioRawFrame(audio=payload, sample_rate=24000, num_channels=1)


async def _r4_async(base: Path) -> dict:
    out = {}
    payload = b"\x22\x00" * 1200

    # Disarmed: byte-identical pass-through (recorder never even consulted for state).
    rec = Recorder("testchar", base, tts_rate=24000, mic_rate=16000)
    tap = TTSRecordTap(rec)
    frame = _tts_frame(payload)
    down, _up = await run_test(tap, frames_to_send=[frame])
    out["disarmed_passthrough"] = any(f is frame for f in down)

    mic_tap = MicRecordTap(rec)
    mic_frame = InputAudioRawFrame(audio=payload, sample_rate=16000, num_channels=1)
    down, _up = await run_test(mic_tap, frames_to_send=[mic_frame])
    out["disarmed_mic_passthrough"] = any(f is mic_frame for f in down)

    # Armed: the tap captures the exact bytes AND still passes the frame through.
    await rec.start(name="tapcheck")
    frame2 = _tts_frame(payload)
    down, _up = await run_test(TTSRecordTap(rec), frames_to_send=[frame2])
    out["armed_passthrough"] = any(f is frame2 for f in down)
    stop = await rec.stop()
    with wave.open(str(Path(stop["stems"]) / "tts.wav"), "rb") as rf:
        out["captured_bytes"] = rf.readframes(rf.getnframes())
    out["payload"] = payload
    return out


def r4_taps():
    print("\nR4 — tap passivity (real pipecat run_test)")
    with tempfile.TemporaryDirectory() as td:
        out = asyncio.run(_r4_async(Path(td) / "captures"))
        check(out["disarmed_passthrough"], "disarmed TTS tap: frame passes through untouched")
        check(out["disarmed_mic_passthrough"], "disarmed mic tap: frame passes through untouched")
        check(out["armed_passthrough"], "armed tap: frame STILL passes through (record never blocks audio)")
        check(out["captured_bytes"] == out["payload"], "armed tap: stem holds the exact frame bytes")


# ── R5 — guards (fail-soft contract) ──────────────────────────────────────────


async def _r5_async(base: Path) -> dict:
    out = {}
    rec = Recorder("testchar", base, tts_rate=24000, mic_rate=16000)
    out["idle_stop"] = await rec.stop()
    await rec.start(name="g")
    out["double_start"] = await rec.start(name="g2")
    rec.append("mic", b"\x01\x00" * 16)  # mic never requested → must be a silent no-op
    out["still_armed_after_unrequested"] = rec.armed
    # Sabotage the writer → append must disarm, not raise into the frame path.
    rec._writers["tts"]._wf.close()
    rec.append("tts", b"\x01\x00" * 16)
    out["disarmed_on_error"] = (not rec.armed) and rec.status()["error"] is not None
    out["final_stop"] = await rec.stop()  # finalize still succeeds (stems best-effort)
    return out


def r5_guards():
    print("\nR5 — guards (fail-soft: recording can never take the loop down)")
    with tempfile.TemporaryDirectory() as td:
        out = asyncio.run(_r5_async(Path(td) / "captures"))
        check(out["idle_stop"]["ok"] is False, "stop while idle refuses cleanly")
        check(out["double_start"]["ok"] is False, "second start refuses cleanly")
        check(out["still_armed_after_unrequested"], "append to unrequested stem: silent no-op")
        check(out["disarmed_on_error"], "capture error DISARMS (no raise into the frame path)")
        check(out["final_stop"]["ok"] is True, "stop after error still finalizes")


# ── R6 — back-to-back takes (same name, same minute → never overwrite) ───────


async def _r6_async(base: Path) -> list[str]:
    rec = Recorder("testchar", base, tts_rate=24000, mic_rate=16000)
    mixes = []
    for _ in range(3):  # three segments inside the same minute, same name
        await rec.start(name="take")
        rec.append("tts", b"\x10\x00" * 4800)
        stop = await rec.stop()
        mixes.append(stop["mix"])
    return mixes


def r6_back_to_back():
    print("\nR6 — back-to-back takes (press Record again — nothing else)")
    with tempfile.TemporaryDirectory() as td:
        mixes = asyncio.run(_r6_async(Path(td) / "captures"))
        names = [Path(m).name for m in mixes]
        check(len(set(names)) == 3, f"three same-minute takes → three distinct files ({names})")
        check(all(Path(m).exists() and Path(m).stat().st_size > 0 for m in mixes),
              "all three mixdowns intact (no overwrite)")
        check(names[1].endswith("-2.m4a") and names[2].endswith("-3.m4a"),
              "collision suffix -2/-3 applied in order")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("test_recording.py — M7 session recording, headless")
    r1_slug()
    r2_stem_writer()
    r3_lifecycle()
    r4_taps()
    r5_guards()
    r6_back_to_back()
    print(f"\n{_PASS} passed · {_FAIL} failed")
    raise SystemExit(1 if _FAIL else 0)
