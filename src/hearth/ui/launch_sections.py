"""launch_sections.py — the launch page's own four files.

``launch_page.html`` was 848 lines and 38 864 B, 81% script, against the 16 KiB
line the other split pages hold — and it had grown there honestly, one card at a
time: the audio route control arrived with the paired-device work, the Stop card
learned to count a grace window, the deferred start learned to wait out somebody
else's maintenance lock. Nothing was wrong with the code; there was no seam, so
an edit to the route radios and an edit to the state poll were edits to the same
file. Now the page is markup, the four `let`s every section reads, the switch
card, the session shelf and the wiring that starts it — and the four jobs are
four files spliced back in at render.

Not a shared layer — ``brand.css``, ``switch_card.js`` and ``admin_shell.js`` are
spliced into several pages BECAUSE they must not drift between them; these four
serve one page. ``test_page_sections.py`` asserts no other page takes one, so
sharing is a decision someone would have to make out loud.

Distinct, too, from the page's COMPONENTS — ``launch_actuators.js``,
``launch_models.js``, ``launch_sessions.js``, and the shared ``switch_card.js``,
``compact_queue.js``, ``first_run_offer.js``, ``key_help.js`` and
``hearth_restart.js``: those are closures in their own ``<script>`` tags with a
mount contract and no reach into the page's scope, and they keep their own
one-file splicers.
These four are the page's own scope, cut into readable pieces — the panel and
roster idiom, spliced inside the page's own ``<script>`` block.

**What reaches across.** All four land in that one block and share its scope.
Four ``let``s stay in the page because more than one section reads them —
``choices`` (written by the card, read by the poll), ``botUp`` and ``busy``
(written by the poll and by the two write paths, read by both), and ``devices``
(written by the poll, read by the route control and by the Stop card's audio
line). Beyond those, one binding reaches section to section: ``launch_deferred``
declares ``let deferred``, and the poll reads it to know whether a finished
Start line has gone stale (``test_page_sections.py`` pins that one by name).
Everything else crosses through function declarations, which hoist.

**Order.** Each placeholder sits exactly where its code sat, which is why the
split moved nothing: the one load-time call into a section
(``drawRouteControl(devices, "")``) still happens below ``launch_devices``, and
``launch_stop``'s keep/name wiring still runs in the page's wiring section,
where it always ran. The written order is pinned by ``test_page_sections.py``
so it stays deliberate rather than accidental.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

_IN_SCRIPT = "in its <script> block"

SECTIONS = pages.Sections("launch", Path(__file__).parent, (
    ("/*LAUNCH_POLL_JS*/", "launch_poll.js", "the state poll", _IN_SCRIPT),
    ("/*LAUNCH_DEFERRED_JS*/", "launch_deferred.js", "deferred start",
     _IN_SCRIPT),
    ("/*LAUNCH_DEVICES_JS*/", "launch_devices.js", "the audio route control",
     _IN_SCRIPT),
    ("/*LAUNCH_STOP_JS*/", "launch_stop.js", "the Stop card", _IN_SCRIPT),
))

splice = SECTIONS.splice
