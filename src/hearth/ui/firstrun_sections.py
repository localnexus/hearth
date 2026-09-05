"""firstrun_sections.py — the first-run page's own files.

``first_run_page.html`` was born split, on the pattern the roster and settings
pages arrived at after growing past the size line: the page keeps its markup
and the wiring that starts it, and its two jobs are two files spliced back in
at render — step 1 (is the server answering, which model) and steps 2–3 (the
start, and the first words).

Not a shared layer — ``brand.css``, ``switch_card.js`` and ``admin_shell.js``
are spliced into several pages BECAUSE they must not drift between them; these
two serve one page, and ``test_page_sections.py`` asserts no other page takes
one.

**What reaches across.** ``firstrun_check`` declares ``let fr`` (the last
state payload) and the page's ``refresh()``; ``firstrun_listen`` declares the
card and ``renderListen()``. They reach each other only through function
declarations, which hoist — ``refresh`` calls ``renderListen``, the card's
``onApplied`` calls ``refresh`` — and every such call happens after an
``await``, with the whole script body already run. So the placeholder order
does not bind today, and is pinned anyway in ``test_page_sections.py``.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

_IN_SCRIPT = "in its <script> block"

SECTIONS = pages.Sections("firstrun", Path(__file__).parent, (
    ("/*FIRSTRUN_CHECK_JS*/", "firstrun_check.js",
     "step 1 — the server and the model id", _IN_SCRIPT),
    ("/*FIRSTRUN_LISTEN_JS*/", "firstrun_listen.js",
     "steps 2 and 3 — the start and the first words", _IN_SCRIPT),
))

splice = SECTIONS.splice
