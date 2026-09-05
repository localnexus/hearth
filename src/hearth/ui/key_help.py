"""key_help.py — splice the access-key explainer into the two front doors.

The launch page and the first-run page each open on a card asking for the
access key, and until 2026-09-05 neither said what it was. The explainer is
one shared file for the reason the switch card is: two token cards that drift
would teach two different stories about the same key. The operator's ask,
after walking the stranger's path in person: *an explainer at a 9th-grade
level, and an example so they know what it looks like.*

The example is a fixed, visibly patterned 64-hex string — the pages are served
unauthed (see their security contracts), so no real key may ever be baked in.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder the host page declares in its <script> block.
PLACEHOLDER = "/*KEY_HELP_JS*/"

PATH = Path(__file__).parent / "key_help.js"

#: The component as of import.
JS = pages.text(PATH)

#: The example key the page shows — pinned so a test can prove it is the ONLY
#: 64-hex run in the served page.
EXAMPLE = "0123456789abcdef" * 4

splice = pages.splicer(PLACEHOLDER, PATH, "the access-key explainer",
                       "in its <script> block")
