"""compact_queue.py — splice the compaction-queue readout into a page.

One host today (the facade's launch page, which is where the Compact button
lives), written as a spliced asset rather than inline markup for the reason
every other asset here was extracted: the launch page is at the file-size line,
and a component that draws its own state belongs beside its own bytes.

See ``supervisor/compact_watch.queue_status`` for what it renders and why the
failed state is the one that had to become visible.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder the host page declares in its <script> block.
PLACEHOLDER = "/*COMPACT_QUEUE_JS*/"

PATH = Path(__file__).parent / "compact_queue.js"

#: The component as of import.
JS = pages.text(PATH)

#: Refuses a page that lost the placeholder — one that shipped it unreplaced
#: would show an empty queue forever and look healthy doing it.
splice = pages.splicer(PLACEHOLDER, PATH, "the compaction queue",
                       "in its <script> block")
