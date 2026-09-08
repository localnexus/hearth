"""launch_models.py — splice the "Models" card into the launch page.

The launch page's fourth component file, for the same reason as the three
before it: a card with its own verbs, its own preview-then-confirm flow and its
own scan list is a page's worth of behaviour, and the page it belongs to is
already at the size where a new inline block would be the wrong answer.

What the card is: enrollment, rendering, applying and loading the door, in the
order the design names them — the web half of `python -m hearth.weights`. It
talks to /admin/models (supervisor/models/), and its Load / Unload buttons press
the two built-in `door-load` / `door-unload` actuators through the ordinary
actuator runner rather than starting anything themselves.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder the launch page declares in its own <script> tag.
PLACEHOLDER = "/*LAUNCH_MODELS_JS*/"

PATH = Path(__file__).parent / "launch_models.js"

#: The component as of import.
JS = pages.text(PATH)

splice = pages.splicer(PLACEHOLDER, PATH, "the Models card",
                       "in its own <script> tag")
