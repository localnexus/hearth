"""init/banner.py — the flame and the wordmark, for a terminal that has a person
at it.

Seven lines, forty columns, printed once at the top of the first-run report.
It decorates; it never gates. Three rules keep it out of the way:
  - a terminal only: when stdout is not a TTY (a pipe, a log, a test) nothing
    is printed at all — a script reading init's output never sees it;
  - colour only when the person has not said otherwise (NO_COLOR, the
    convention at no-color.org);
  - once: install.sh prints the same art as its first line and sets
    HEARTH_BANNER_SHOWN before handing over to init, so it is not shown twice.
install.sh carries a byte-identical copy of ART (there is no venv yet on its
first line); tests/test_banner.py holds the two together.
"""

from __future__ import annotations

import os
import sys

SHOWN_ENV = "HEARTH_BANNER_SHOWN"

# The flame (left) is one colour, the words (right) another. Column 23 is
# where the words start; everything left of it is flame.
ART = (
    "            (",
    "           ) )",
    "          ( ( )",
    "         _)  (_",
    "        (   __  )      H E A R T H",
    "         \\ (  ) /      a voice at home",
    "          `----'",
)
_SPLIT = 23  # first column of the words

_FLAME = "\x1b[38;5;208m"  # ember
_WORDS = "\x1b[38;5;220m"  # gold
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def text(colour: bool) -> str:
    """The banner as a string, with or without ANSI colour. Always the same
    seven lines; colour only wraps them."""
    if not colour:
        return "\n".join(ART) + "\n"
    out = []
    for line in ART:
        flame, words = line[:_SPLIT], line[_SPLIT:]
        piece = f"{_FLAME}{flame}{_RESET}"
        if words:
            piece += f"{_WORDS}{_BOLD}{words}{_RESET}"
        out.append(piece)
    return "\n".join(out) + "\n"


def wants(stream=None, env=None, quiet: bool = False) -> bool:
    """Should the banner be printed here? A TTY, not quieted, not already shown."""
    stream = sys.stdout if stream is None else stream
    env = os.environ if env is None else env
    if quiet or env.get(SHOWN_ENV):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def colour_ok(env=None) -> bool:
    env = os.environ if env is None else env
    return not env.get("NO_COLOR")


def show(stream=None, env=None, quiet: bool = False) -> bool:
    """Print the banner when it belongs on this stream. True iff printed."""
    stream = sys.stdout if stream is None else stream
    if not wants(stream, env, quiet):
        return False
    stream.write(text(colour_ok(env)))
    stream.write("\n")
    stream.flush()
    return True
