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

Order is contract, not preference — and the order that matters is where the
PLACEHOLDERS sit in the page, not the sequence of splices here (each splice is an
independent ``replace``; the tuple below is merely the order they are declared
in). Each module lands INSIDE the page's own ``<script>`` block, so all of them
share one scope, and two reach across it: ``panel_status``'s ``renderAgent()``
reads the ``knob`` and ``selVoice`` that ``panel_knobs`` declares, and
``panel_knobs`` calls ``renderAgent()`` back. Function declarations hoist, so the
calls are safe either way round; ``let knob`` does not, so the status placeholder
must come BEFORE the knobs one in the page — as the sections were written — or
the first paint raises on the temporal dead zone. ``test_panel_modules.py`` pins
that against the RENDERED page, which is where the truth is.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

_DIR = Path(__file__).parent

_IN_SCRIPT = "in its <script> block"

#: placeholder → (file, what it is, where the placeholder belongs). Listed in
#: the order the page splices them in; the order that BINDS is the placeholders'
#: order in the page itself (see above).
MODULES = (
    ("/*PANEL_CSS*/", "panel_style.css", "the panel's stylesheet",
     "inside its <style> block"),
    ("/*PANEL_RECORD_JS*/", "panel_record.js", "the session recorder", _IN_SCRIPT),
    ("/*PANEL_STATUS_JS*/", "panel_status.js", "the status block", _IN_SCRIPT),
    ("/*PANEL_KNOBS_JS*/", "panel_knobs.js", "the hot knobs", _IN_SCRIPT),
    ("/*PANEL_MANUAL_JS*/", "panel_manual.js", "the manual pane", _IN_SCRIPT),
)

#: name → absolute path, for the guard test and anything else that needs to read
#: a module without knowing the splice order.
PATHS = {name: _DIR / name for _, name, _, _ in MODULES}

#: The four splices, in order — each refuses a page that lost its placeholder.
SPLICES = tuple(pages.splicer(placeholder, _DIR / name, what, where)
                for placeholder, name, what, where in MODULES)

#: One transform for the whole panel: ``pages.chain(panel.splice, ...)``.
splice = pages.chain(*SPLICES)
