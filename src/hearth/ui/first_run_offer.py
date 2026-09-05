"""first_run_offer.py — splice the launch page's First-run offer into it.

The entry condition of the first-run path, as a component rather than inline
markup, for the reason every other launch-page asset was extracted: the page
sits at the file-size line, and a component that draws its own state belongs
beside its own bytes. See ``supervisor/firstrun`` for the two facts it draws
and ``routes/state.py`` for where they ride.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder the host page declares in its <script> block.
PLACEHOLDER = "/*FIRST_RUN_JS*/"

PATH = Path(__file__).parent / "first_run_offer.js"

#: The component as of import.
JS = pages.text(PATH)

#: Refuses a page that lost the placeholder — one that shipped it unreplaced
#: would never offer the walk, and would park Start with no way to say why.
splice = pages.splicer(PLACEHOLDER, PATH, "the first-run offer",
                       "in its <script> block")
