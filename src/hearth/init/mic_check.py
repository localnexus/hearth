"""mic_check.py — the closing microphone check: say something, see this.

    .venv/bin/python -m hearth.init.mic_check

`install.sh` runs this as its last step, after asking. It records about two
seconds from the default input, measures the loudest sample, and says in plain
words whether anything arrived. It plays nothing back: a sound coming out of
the speakers proves the speaker, not the microphone, and a fresh install has
no reason to startle anybody.

Why it exists: the number-one first-day failure is a microphone macOS never
granted. Hearth then hears silence and does not say so
(docs/quick/first-talk.md, docs/quick/when-it-goes-wrong.md). The check closes
that loop while the person is still at the terminal window that needs the
grant — macOS attaches the microphone permission to the terminal app, so the
dialog this triggers is the same one the real conversation depends on.

The result never changes an exit code. A silent microphone is a note (`!`),
not a failed install: the person may simply not have spoken.

The PortAudio binding is `sounddevice`, already in the environment — uv.lock
pulls it for `mlx-audio`, which the `mac` extra installs. Nothing new is
declared for this module, and it is imported inside the function, so a tree
without it degrades to a note instead of a traceback.
"""

from __future__ import annotations

import array
import sys

SECONDS = 2.0
RATE = 16_000
_FULL_SCALE = 32767
# Below this share of full scale, nothing worth calling speech arrived. Room tone
# on a built-in Mac microphone sits near 0.005; a spoken sentence peaks far above.
HEARD = 0.02
WHEN_IT_GOES_WRONG = "docs/quick/when-it-goes-wrong.md"

MARK_HEARD = "+"
MARK_SILENT = "!"
MARK_SKIPPED = "-"


def peak(chunks) -> int:
    """The loudest sample across raw little-endian int16 frames, 0 if there are none."""
    loudest = 0
    for chunk in chunks:
        if not chunk:
            continue
        samples = array.array("h")
        samples.frombytes(bytes(chunk)[: len(chunk) // 2 * 2])
        for s in samples:
            # -32768 has no positive twin in int16; clamp rather than overflow the scale.
            level = _FULL_SCALE if s == -32768 else abs(s)
            if level > loudest:
                loudest = level
    return loudest


def sentence(loudest: int) -> tuple[str, str]:
    """Peak level → the mark and the plain words that go with it."""
    share = loudest / _FULL_SCALE
    percent = round(share * 100)
    if share >= HEARD:
        return MARK_HEARD, f"heard you — loudest moment {percent}% of full scale"
    return MARK_SILENT, (f"heard nothing — loudest moment {percent}% of full scale. "
                         f"The microphone grant is the usual reason: see {WHEN_IT_GOES_WRONG}")


def record(sd, seconds: float = SECONDS, rate: int = RATE) -> int:
    """Read `seconds` of mono int16 from the default input and return the peak.

    `sd` is the sounddevice module, or any stand-in with the same shape — that
    is how the tests drive this without a microphone.
    """
    chunks: list[bytes] = []
    blocksize = 1024
    with sd.RawInputStream(channels=1, samplerate=rate, dtype="int16",
                           blocksize=blocksize) as stream:
        remaining = int(seconds * rate)
        while remaining > 0:
            want = min(blocksize, remaining)
            data, _overflowed = stream.read(want)
            chunks.append(bytes(data))
            remaining -= want
    return peak(chunks)


def check(sd=None, seconds: float = SECONDS, out=None) -> int:
    """Print one line about the microphone. Always returns 0 — this never fails a run."""
    out = sys.stdout if out is None else out

    def line(mark: str, text: str) -> None:
        print(f"  {mark} {text}", file=out)

    if sd is None:
        try:
            # Imported here, not at module top: a tree without PortAudio still loads.
            import sounddevice

            sd = sounddevice
        except Exception as exc:  # pragma: no cover — an environment without PortAudio
            line(MARK_SKIPPED, f"microphone not checked ({exc.__class__.__name__}). "
                               f"If Hearth hears silence later: see {WHEN_IT_GOES_WRONG}")
            return 0
    print("  say something — listening for two seconds", file=out)
    try:
        loudest = record(sd, seconds=seconds)
    except Exception as exc:
        line(MARK_SKIPPED, f"the microphone could not be opened: {exc}. "
                           f"See {WHEN_IT_GOES_WRONG}")
        return 0
    line(*sentence(loudest))
    return 0


def main() -> int:
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
