"""switch_card.py — splice the shared companion switcher into a page.

The card itself (``switch_card.js``) has been shared since it was extracted; what
lived in two places until now was the SPLICE — control.py and supervisor/routes.py
each carried their own copy of the same three lines. With a third shared asset in
play (the admin shell), each host composes ``pages.chain(...)`` instead, and the
splices belong beside the files they inject.

Two hosts, two processes, one file: the bot's control panel (:65000) and the
facade's launch page (:65001). ``test_shared_switch_card.py`` is the guard.
"""

from __future__ import annotations

from pathlib import Path

from hearth.ui import pages

#: The placeholder both host pages declare in their <script> block.
PLACEHOLDER = "/*SWITCH_CARD_JS*/"

PATH = Path(__file__).parent / "switch_card.js"

#: The card as of import (the divergence guard compares pages against this).
JS = pages.text(PATH)


def splice(page: str) -> str:
    """Return ``page`` with the switcher in place of its placeholder.

    Raises if the placeholder is absent: a page that shipped it unreplaced would
    serve a switcher that cannot run.
    """
    if PLACEHOLDER not in page:
        raise ValueError(
            f"page does not declare {PLACEHOLDER} — it cannot receive the "
            "companion switcher (add the placeholder in its <script> block)")
    return page.replace(PLACEHOLDER, pages.text(PATH))
