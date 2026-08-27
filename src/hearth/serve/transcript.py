"""serve/transcript.py — the facade's conversation tap.

Every chat turn that crosses the facade lands as markdown, one file per local
day + channel: <transcript_dir>/<YYYY-MM-DD>-<character>-<channel>.md. The
directory is gitignored (verbatim conversation plaintext) and belongs to the
backup set for at-rest cover.

Transcript knobs: fresh files open with YAML frontmatter, body stays
human-first markdown (the machine lane for continuity is sessions/*.json, never
this file); per-day files on every channel; channel truth comes from the caller
— X-Hearth-Channel, whitelisted, a streaming client stamps "voice", chat
clients default to "chat"; a model-side save-conversation *tool* stays
deferred to the continuity arc (tool = agency, tap = completeness). This
module remains the ONLY place format and home live.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from loguru import logger

CHANNELS = ("chat", "voice")  # whitelist — a header value lands in a filename


class TranscriptTap:
    """Append-only exchange writer. Failures are logged, never raised — a tap
    problem must not break the conversation it is taping."""

    def __init__(self, home: Path, character: str, channel: str = "chat", model: str = ""):
        self._home = home
        self._character = character
        self._channel = channel
        self._model = model

    def record(self, user_text: str, reply_text: str, channel: str | None = None) -> None:
        ch = channel if channel in CHANNELS else self._channel
        try:
            now = _dt.datetime.now()
            path = self._home / f"{now:%Y-%m-%d}-{self._character}-{ch}.md"
            self._home.mkdir(parents=True, exist_ok=True)
            fresh = not path.exists()
            with open(path, "a", encoding="utf-8") as f:
                if fresh:
                    f.write(
                        "---\n"
                        "type: transcript\n"
                        f"character: {self._character}\n"
                        f"channel: {ch}\n"
                        f"date: {now:%Y-%m-%d}\n"
                        f"model: {self._model}\n"
                        "---\n"
                        f"\n# {self._character} — {ch} — {now:%Y-%m-%d}\n"
                    )
                f.write(f"\n**user — {now:%H:%M:%S}**\n\n{user_text.strip()}\n")
                f.write(f"\n**{self._character} — {now:%H:%M:%S}**\n\n{reply_text.strip()}\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve] transcript tap failed ({})", type(exc).__name__)
