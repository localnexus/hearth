"""settings_sections.py — the settings page's own files.

``settings_page.html`` was 502 lines and 19.9 KiB and **73% script** — the most
script-heavy page in the repo. It is a form generator: it walks pydantic's JSON
Schema, renders a control per field, and gates every write behind a preview. Four
concerns, one scope. Now four files.

What stays in the page is the markup and the state every section reads — the four
``let``s (``schemas``, ``overview``, ``current``, ``pending``) and ``needToken()``
— plus the ``wireToken``/``poll`` wiring at the bottom. That is the same division
the control panel got: the page keeps what everything touches.

Not a shared layer; see ``roster_sections`` for the distinction, which
``test_page_sections.py`` enforces for all three pages.

**What reaches across.** This page is the clean case: the four ``let``s live in
the page ABOVE every placeholder, so the sections reference each other only
through function declarations, which hoist. ``settings_form`` reaches for
``unwrap`` (schema), ``renderKinds`` (files) and ``askSet`` (confirm);
``settings_confirm`` reaches back for ``refresh`` and ``openFile``. Order is
genuinely free here — pinned in ``test_page_sections.py`` only so the sections
keep reading in the order they were written. Add a section that declares a
``let`` another one reads and that stops being true; say so here if it happens.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

_IN_SCRIPT = "in its <script> block"

SECTIONS = pages.Sections("settings", Path(__file__).parent, (
    ("/*SETTINGS_SCHEMA_JS*/", "settings_schema.js", "the JSON Schema walkers",
     _IN_SCRIPT),
    ("/*SETTINGS_FILES_JS*/", "settings_files.js", "the config-file list",
     _IN_SCRIPT),
    ("/*SETTINGS_FORM_JS*/", "settings_form.js", "the generated form",
     _IN_SCRIPT),
    ("/*SETTINGS_CONFIRM_JS*/", "settings_confirm.js", "preview-then-confirm",
     _IN_SCRIPT),
))

splice = SECTIONS.splice
