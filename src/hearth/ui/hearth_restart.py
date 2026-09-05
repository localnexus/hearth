"""hearth_restart.py — splice the "Restart Hearth" card into the launch page.

The daemon-restart route existed from the supervisor's first stroke; the
button did not, because under a plain terminal run the same request ends
Hearth with nothing to bring it back — a stranger on the one-command path
would press it and watch the pages go dark. The card therefore draws only
while /admin/state reports a keeper (supervisor/keeper.py), and the route
refuses without one. Built 2026-09-05 on the operator's ask.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder the launch page declares in its <script> block.
PLACEHOLDER = "/*HEARTH_RESTART_JS*/"

PATH = Path(__file__).parent / "hearth_restart.js"

#: The component as of import.
JS = pages.text(PATH)

splice = pages.splicer(PLACEHOLDER, PATH, "the Restart Hearth card",
                       "in its <script> block")
