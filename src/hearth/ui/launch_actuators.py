"""launch_actuators.py — splice the "Externals" (actuators) card into the launch page.

Lifted out of the launch page on 2026-09-05 when the card learned the
companion guard: an actuator declared with guard = "companion" is refused
while a companion is running, and the card turns that refusal into a
question the operator answers in words before the press goes through with
?force=1. The page was at the 15 KiB line, so the card became its own file,
the way the Restart Hearth card and the compaction queue already were.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder the launch page declares in its own <script> tag.
PLACEHOLDER = "/*LAUNCH_ACTUATORS_JS*/"

PATH = Path(__file__).parent / "launch_actuators.js"

#: The component as of import.
JS = pages.text(PATH)

splice = pages.splicer(PLACEHOLDER, PATH, "the Externals (actuators) card",
                       "in its own <script> tag")
