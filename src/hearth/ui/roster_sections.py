"""roster_sections.py — the roster page's own files.

``roster_page.html`` was 508 lines and 21.5 KiB, 61% script, with three unrelated
jobs in one scope: onboarding a new companion, editing an existing one's persona
and voices, and branching one off at a juncture. Nothing was wrong with the code;
there was no seam, so a fork-verb edit and an onboarding edit were edits to the
same file. Now the page is markup plus the wiring that starts it, and the three
jobs are three files spliced back in at render.

Not a shared layer — ``brand.css``, ``switch_card.js`` and ``admin_shell.js`` are
spliced into several pages BECAUSE they must not drift between them; these three
serve one page. ``test_page_sections.py`` asserts no other page takes one, so
sharing is a decision someone has to make out loud.

**What reaches across.** All three land inside the page's own ``<script>`` block
and share its scope. ``roster_edit`` declares ``let roster`` — the character list
the pickers read — and both other sections read it. That works because it is only
ever reached from ``refresh()``, which the page calls at the very bottom through
``wireToken``/``poll``, with every declaration already run.

So the placeholder order does not bind today, and it is pinned anyway
(``test_page_sections.py``): the freedom is accidental, and one synchronous read
of ``roster`` from a section spliced above ``roster_edit`` would end it — as a
blank page, not as anything a static check would catch.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

_IN_SCRIPT = "in its <script> block"

SECTIONS = pages.Sections("roster", Path(__file__).parent, (
    ("/*ROSTER_ONBOARD_JS*/", "roster_onboard.js", "companion onboarding",
     _IN_SCRIPT),
    ("/*ROSTER_EDIT_JS*/", "roster_edit.js", "the persona and voice editor",
     _IN_SCRIPT),
    ("/*ROSTER_FORK_JS*/", "roster_fork.js", "the branch (fork) card",
     _IN_SCRIPT),
))

splice = SECTIONS.splice
