"""launch_sessions.py — splice the "Sessions" card into the launch page.

The launch page's fifth component file, and the same judgement as the four
before it: a card with per-row verbs, a preview-then-confirm flow, a file
upload and a list is a page's worth of behaviour, and the launch page is long
past the size where another inline block would be the right answer.

What the card is: the web half of the session-file verbs — reveal, download,
deposit, archive/unarchive, rename, destroy — that `routes/sessions.py` grew
one stroke at a time. It sits beside the Conversation picker because it is the
same shelf seen from the other side: the picker chooses which conversation to
resume, the card decides which conversations there are.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder the launch page declares in its own <script> tag.
PLACEHOLDER = "/*LAUNCH_SESSIONS_JS*/"

PATH = Path(__file__).parent / "launch_sessions.js"

#: The component as of import.
JS = pages.text(PATH)

splice = pages.splicer(PLACEHOLDER, PATH, "the Sessions card",
                       "in its own <script> tag")
