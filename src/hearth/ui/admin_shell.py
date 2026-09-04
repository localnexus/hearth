"""admin_shell.py — splice the shared admin-page JS into a page.

Same mechanism and the same reason as ``brand`` and ``switch_card``: one file
on disk, spliced into the pages that need it, with a divergence-guard test.
This one carries what every authed admin page was re-stating — the ``$``/``el``
helpers, the bearer in localStorage, the authed ``api()``, ``show``/``report``,
the token-entry wiring and the poll loop.

The splice lands INSIDE the page's own ``<script>`` block, not as a block of its
own, so the shell's declarations are the page's own top-level bindings and a
page that redeclares one fails loudly instead of shadowing it.

pair_page.html is deliberately excluded: it is what a device WITHOUT the bearer
opens, and a shell whose job is carrying the bearer has nothing for it.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder each participating page declares in its <script> block.
PLACEHOLDER = "/*ADMIN_SHELL_JS*/"

PATH = Path(__file__).parent / "admin_shell.js"

#: The shell as of import (the divergence guards compare pages against this).
JS = pages.text(PATH)


#: Refuses a page that lost the placeholder — it would then serve calls to
#: helpers nobody defined: a blank screen and a console error, discovered by a
#: person instead of by startup.
splice = pages.splicer(PLACEHOLDER, PATH, "the admin shell",
                       "at the top of its <script> block")
