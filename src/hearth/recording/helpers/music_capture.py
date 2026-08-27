"""
music_capture.py — M7 loopback capture helper (spawned by recording.py).

Captures a CoreAudio loopback input (BlackHole) to WAV via PortAudio
(sounddevice) — NOT ffmpeg/avfoundation. ffmpeg 8.1.1's avfoundation input
silently drops ~10% of buffers from ANY device on this box (uniform loss,
scales with duration, empty stderr at -loglevel warning; every dropped buffer
is a splice-click in the stem). PortAudio benched 0.9982 wall ratio, zero
overflows, on the identical test. Full evidence: build-log
2026.08.23.0510_m7-rate-agnostic-music-capture.md.

Runs as a SUBPROCESS so the live pipeline never shares a process (or a
PortAudio instance) with the capture. SIGINT/SIGTERM → drain queue → close →
valid WAV header. Delivery stats go to stderr (recording.py files it as
music-capture.log in the stems dir) so any loss stays visible evidence.

Modes:
    --probe                      print JSON {found, index, name} for the first
                                 loopback-looking input device, then exit —
                                 device discovery for the panel tickbox,
                                 keeping ALL PortAudio touching out of bot.py
    --device N --rate R --out P  capture until SIGINT/SIGTERM
"""

from __future__ import annotations

import argparse
import json
import queue
import signal
import sys
import threading
import time
import wave

_LOOPBACK_RE = ("blackhole", "loopback")
_CHANNELS = 2
_SAMPWIDTH = 2  # int16


def _input_devices(sd):
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] >= _CHANNELS:
            yield i, d["name"]


def probe() -> int:
    import sounddevice as sd

    for i, name in _input_devices(sd):
        if any(pat in name.lower() for pat in _LOOPBACK_RE):
            print(json.dumps({"found": True, "index": i, "name": name}))
            return 0
    print(json.dumps({"found": False}))
    return 0


def capture(device_name: str, rate: int, out_path: str) -> int:
    import sounddevice as sd

    dev_idx = next((i for i, n in _input_devices(sd) if n == device_name), None)
    if dev_idx is None:  # device set may have shifted since the probe — retry lax
        dev_idx = next((i for i, n in _input_devices(sd)
                        if device_name.lower() in n.lower()), None)
    if dev_idx is None:
        print(f"capture device not found: {device_name}", file=sys.stderr)
        return 2

    q: queue.SimpleQueue[bytes] = queue.SimpleQueue()
    stop = threading.Event()
    overflows = 0

    def cb(indata, n_frames, t, status):  # PortAudio thread — copy out, count, leave
        nonlocal overflows
        if status.input_overflow:
            overflows += 1
        q.put(bytes(indata))

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.set())

    frames = 0
    t0 = time.monotonic()
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(_SAMPWIDTH)
        wf.setframerate(rate)
        # RawInputStream: callback hands raw int16 bytes — no numpy in the loop.
        with sd.RawInputStream(device=dev_idx, channels=_CHANNELS,
                               samplerate=rate, dtype="int16", callback=cb):
            while not stop.is_set():
                try:
                    buf = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                wf.writeframes(buf)
                frames += len(buf) // (_CHANNELS * _SAMPWIDTH)
        wall = time.monotonic() - t0
        while True:  # stream closed — drain the tail
            try:
                buf = q.get_nowait()
            except queue.Empty:
                break
            wf.writeframes(buf)
            frames += len(buf) // (_CHANNELS * _SAMPWIDTH)

    audio_s = frames / rate
    ratio = audio_s / wall if wall > 0 else 0.0
    print(f"captured {audio_s:.2f} s audio in {wall:.2f} s wall "
          f"(ratio {ratio:.4f}) @ {rate} Hz from '{device_name}'", file=sys.stderr)
    if overflows:
        print(f"WARNING: {overflows} input-overflow callbacks — "
              f"the stem carries dropped audio", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--device")
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--out")
    a = p.parse_args()
    if a.probe:
        return probe()
    if not a.device or not a.out:
        p.error("--device and --out are required unless --probe")
    return capture(a.device, a.rate, a.out)


if __name__ == "__main__":
    sys.exit(main())
