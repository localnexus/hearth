"""panel.py — the control panel's own CSS and JS, four files instead of one page.

``control_page.html`` was 41.6 KiB, 69% of it script, and every concern in it —
the mic and text controls, the status meters, the L2 hot knobs, the manual pane —
shared one flat scope. Nothing was wrong with the code; there was simply no seam,
so a knob edit and a meter edit were edits to the same 822-line file.

The split is by CONCERN, not by size:

  ``panel_style.css``    the panel's own skin (the shared palette still arrives
                         separately, through ``brand.css``)
  ``panel_record.js``    the M7 session recorder — arm, elapsed clock, stems
  ``panel_status.js``    the status block — engine facts, the token gauge, the
                         memory line — and the three poll timers
  ``panel_knobs.js``     the L2 hot knobs: the plain-language help table, the
                         slider/enum row builders, the profile buttons
  ``panel_manual.js``    the destinations rail and the in-page manual reader

What stays in the page is markup plus the two things every concern touches: the
``$``/``status``/``post`` transport and the mic and text-turn controls that ARE
the panel's reason to exist.

**These are not shared files.** ``brand``, ``switch_card`` and ``admin_shell``
are spliced into several pages and exist to stop drift between them; these four
serve exactly one page and exist to give it seams. Same mechanism, different
reason — worth saying, because "it is in ui/" is not by itself a claim that
another page may take it.

Each module lands INSIDE the page's own ``<script>`` block, so all of them share
one scope, and two reach across it: ``panel_status``'s ``renderAgent()`` reads
the ``knob`` and ``selVoice`` that ``panel_knobs`` declares with ``let``, and
``panel_knobs`` calls ``renderAgent()`` back.

**On order.** The order that could ever bind is where the PLACEHOLDERS sit in the
page, not the sequence of the tuple below (each splice is an independent
``replace``). Today it binds nothing: swapping status and knobs was tried under
the Node harness and the page loaded clean, because every cross-section read
happens after an ``await``, by which point the whole script body has run. The
written order is pinned anyway (``test_page_sections.py``) precisely BECAUSE that
freedom is accidental — one synchronous read of another section's ``let`` and the
order starts mattering, with the failure landing as a blank page rather than
anything a static check would see.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

_IN_SCRIPT = "in its <script> block"

#: Listed in the order the page splices them in; the order that BINDS is the
#: placeholders' order in the page itself (see above).
SECTIONS = pages.Sections("panel", Path(__file__).parent, (
    ("/*PANEL_CSS*/", "panel_style.css", "the panel's stylesheet",
     "inside its <style> block"),
    ("/*PANEL_RECORD_JS*/", "panel_record.js", "the session recorder", _IN_SCRIPT),
    ("/*PANEL_STATUS_JS*/", "panel_status.js", "the status block", _IN_SCRIPT),
    ("/*PANEL_KNOBS_JS*/", "panel_knobs.js", "the hot knobs", _IN_SCRIPT),
    ("/*PANEL_MANUAL_JS*/", "panel_manual.js", "the manual pane", _IN_SCRIPT),
))

#: One transform for the whole panel: ``pages.chain(panel.splice, ...)``.
splice = SECTIONS.splice
