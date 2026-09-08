"""The Sessions card is spliced into the launch page, and it only ever talks to
routes that exist.

The card is the web half of the session-file verbs — reveal, download, deposit,
archive/unarchive, rename, destroy. Python cannot press its buttons, so these
are the three things Python CAN prove and that a browser would only tell us
about after a restart and a refresh:

  1. the placeholder was replaced and the card's own markers are in the served
     page (a page shipping "/*LAUNCH_SESSIONS_JS*/" verbatim would serve a
     Sessions card with nothing behind it);
  2. every /admin/sessions… path the card names is actually mounted — a typo
     there is a 404 no test of the routes would ever notice;
  3. the refusals it can show carry the ROUTES' wording. The card shows what
     the route sent; the strings kept in the card are the fallback, and this is
     what stops the fallback drifting into a second, softer story about a
     read-only shelf or an unoffered destroy.

Plus the storage rule the launch page's security contract rests on: the bearer
lives in localStorage under the ONE helper the admin shell owns, and no card
may keep anything of its own there. Reveal being unavailable is a fact about
where this browser is sitting, not a preference.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from hearth.session import verbs as verbs_mod
from hearth.supervisor import routes as routes_mod
from hearth.ui import launch_sessions

JS = launch_sessions.PATH.read_text(encoding="utf-8")
PAGE = routes_mod._LAUNCH_PAGE()


class TheCardIsSpliced(unittest.TestCase):

    def test_the_placeholder_is_gone_and_the_card_is_there(self):
        self.assertNotIn(launch_sessions.PLACEHOLDER, PAGE,
                         "the launch page ships the placeholder — the card is dead")
        self.assertIn("window.LaunchSessions = (function", PAGE)
        self.assertEqual(PAGE.count("window.LaunchSessions = (function"), 1,
                         "two copies would be two cards fighting over one id")

    def test_the_splice_refuses_a_page_without_the_placeholder(self):
        with self.assertRaises(ValueError):
            launch_sessions.splice("<script>nothing here</script>")

    def test_the_markup_the_card_writes_into_is_in_the_page(self):
        for marker in ("sessionscard", "sessionsnote", "sessionshead",
                       "sessions\"", "sessionsdeposit"):
            with self.subTest(marker=marker):
                self.assertIn(marker, PAGE)

    def test_the_page_hands_the_card_its_seams(self):
        """The card cannot reach the bearer or the picker on its own: the host
        passes an authed fetch (a download link cannot carry a header) and the
        reload that keeps the Conversation picker in step."""
        self.assertIn("function authFetch(", PAGE)
        self.assertIn("LaunchSessions.refresh(api, report, {", PAGE)
        self.assertIn("onChanged: loadSessions", PAGE)


class OnlyRoutesThatExist(unittest.TestCase):

    def test_every_admin_sessions_path_the_card_names_is_mounted(self):
        # The mount table is written out in build_mount(); reading it from the
        # source is what lets this run without standing an app up, and a path
        # that is added there is the same string the router will answer on.
        source = Path(routes_mod.__file__).read_text(encoding="utf-8")
        mounted = set(re.findall(r'add_(?:get|post)\("(/admin/[^"]+)"', source))
        named = set(re.findall(r'"(/admin/sessions[a-z/]*)', JS))
        self.assertTrue(named, "the card names no session route at all")
        for path in sorted(named):
            with self.subTest(path=path):
                self.assertIn(path, mounted,
                              f"the card calls {path}, which nothing mounts")

    def test_it_names_every_verb_the_arc_built(self):
        for path in ("/admin/sessions", "/admin/sessions/reveal",
                     "/admin/sessions/file", "/admin/sessions/deposit",
                     "/admin/sessions/archive", "/admin/sessions/unarchive",
                     "/admin/sessions/destroy", "/admin/sessions/rename"):
            with self.subTest(path=path):
                self.assertIn(path, JS)


class TheRefusalsAreTheRoutes(unittest.TestCase):

    def test_the_live_session_guard_is_worded_as_the_guard_words_it(self):
        tail = verbs_mod.live_guard("x", "running", "x").split("x", 1)[1]
        flat = " ".join(JS.replace('" +', "").replace('"', "").split())
        self.assertIn(" ".join(tail.split()), flat,
                      "the card tells a different story about a read-only shelf")

    def test_the_403_is_the_route_s_own_sentence(self):
        flat = " ".join(JS.replace('" +', "").replace('"', "").split())
        self.assertIn("destroy is not offered here — enable [serve.sessions] "
                      "destroy_for_all, or use the panel on the machine Hearth "
                      "runs on", flat)

    def test_the_reference_refusal_is_the_route_s_own_sentence(self):
        self.assertIn("this session is referenced — rename its title instead", JS)

    def test_the_reference_list_is_shown_verbatim(self):
        """A 409 on a file rename IS the answer — what knows this id."""
        self.assertIn("d.references", JS)

    def test_destroy_asks_for_the_word_the_shelf_shows(self):
        """title → name → id, exactly destroy's own precedence."""
        self.assertIn("(s.title || \"\").trim() || (s.name || \"\").trim() || s.session_id",
                      JS)
        self.assertIn("confirm_with", JS)

    def test_the_cannot_reach_list_is_rendered_and_never_invented(self):
        self.assertIn("cannot_reach", JS)
        for line in verbs_mod.CANNOT_REACH:
            with self.subTest(line=line):
                self.assertNotIn(line, JS)   # it is the route's list, not ours


class WhatTheCardMayNotDo(unittest.TestCase):

    def test_it_keeps_nothing_in_browser_storage(self):
        """The bearer's one home is the admin shell's helper; a card that
        remembered a probe result across visits would be remembering where the
        browser was sitting last time."""
        for bad in ("localStorage", "sessionStorage", "indexedDB"):
            with self.subTest(api=bad):
                self.assertNotIn(bad, JS)

    def test_it_never_renders_a_path_or_a_message(self):
        """The routes send neither, and the card must not invent either — the
        only "message" it names is the COUNT of prompt messages a deposit
        dropped."""
        for bad in ('"path"', ".path", ".messages", ".content"):
            with self.subTest(field=bad):
                self.assertNotIn(bad, JS)

    def test_a_recall_only_row_is_offered_destroy_and_nothing_else(self):
        """The privacy tier: transcript-ephemeral sittings do not appear in
        load or rename, so destroy is the only verb on the row."""
        self.assertIn('s.memory_mode === "recall-only"', JS)
        self.assertIn("if (!ephemeral)", JS)

    def test_the_download_is_an_authed_fetch_and_not_a_link(self):
        """A navigation carries no Authorization header, and the bearer is
        never a query parameter — so a plain <a href> would 401."""
        self.assertIn("authFetch", JS)
        self.assertIn("createObjectURL", JS)
        self.assertIn("revokeObjectURL", JS)
        self.assertNotIn("Bearer", JS)      # the token never passes through here


if __name__ == "__main__":
    unittest.main()
