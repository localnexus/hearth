"""Every launch-page action speaks beside its own button.

Before this, the whole page shared ONE status line (`#msg`), parked as the
LAST element on the page — below every card. Start, Stop, Compact and four
other cards all wrote there, in plain text or red, and nothing cleared it
when the bot's state moved: "stopped" stayed on screen after the next
session had already started. The fix: each action's message appears beside
its OWN button, in one of three tones ("warn" working/waiting, "err" failed,
"ok" done), and a finished message goes stale the moment the bot's state
moves. The shared line moves to the top of the page, under the state line,
and keeps serving the cards that have no button of their own.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from hearth.supervisor import routes as routes_mod
from hearth.ui import admin_shell
from hearth.ui import switch_card

NODE = shutil.which("node")

CARD_JS = switch_card.JS


def _launch_script() -> str:
    """The launch page's own <script> text, joined in document order."""
    page = routes_mod._LAUNCH_PAGE()
    return "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", page, re.S))


class StatusLineStatic(unittest.TestCase):
    """Assertions that need no interpreter — always run."""

    @classmethod
    def setUpClass(cls):
        cls.page = routes_mod._LAUNCH_PAGE()

    def test_msg_appears_once_before_tokencard(self):
        self.assertEqual(self.page.count('id="msg"'), 1)
        self.assertLess(self.page.index('id="msg"'), self.page.index('id="tokencard"'))

    def test_action_spans_appear_once_each(self):
        self.assertEqual(self.page.count('id="stopstate"'), 1)
        self.assertEqual(self.page.count('id="compactstate"'), 1)

    def test_warn_helper_is_gone(self):
        self.assertNotIn("function warn(", self.page)

    def test_new_helpers_are_present(self):
        self.assertIn("clearOnEdge(", self.page)
        self.assertIn("sayAt(", self.page)

    def test_card_wears_a_tone_and_exposes_say(self):
        self.assertIn("function say(msg, tone)", CARD_JS)
        self.assertIn("say: say", CARD_JS)

    def test_two_min_only_on_the_compact_paths(self):
        # Exactly three sites: the compact button's title, the compact branch
        # of deferredMessage, and doStop's running line.
        self.assertEqual(self.page.count("~2 min"), 3)
        # The non-compact (plain maintenance) branch never claims a duration.
        for line in self.page.splitlines():
            if "maintenance on " in line:
                self.assertNotIn("~2 min", line)

    def test_deferred_message_uses_character_not_session(self):
        match = re.search(r"function deferredMessage\(\) \{.*?\n\}", self.page, re.S)
        self.assertIsNotNone(match, "deferredMessage not found in the launch page")
        body = match.group(0)
        self.assertIn("holder.character", body)
        self.assertNotIn("holder.session", body)

    def test_status_bits_use_character_not_session(self):
        self.assertIn('"compacting " + m.character', self.page)
        for line in self.page.splitlines():
            if '"compacting "' in line:
                self.assertNotIn("m.session", line)

    def test_no_storage_access_in_the_pages_own_script(self):
        # The shared shell (ui/admin_shell.js) is the only place allowed to
        # touch localStorage/sessionStorage. Prove the launch page's own
        # script contributes none of the "Storage" occurrences: the count in
        # the page's script text equals the count in the shell alone.
        own_script = _launch_script()
        self.assertEqual(own_script.count("Storage"), admin_shell.JS.count("Storage"))
        self.assertEqual(own_script.replace(admin_shell.JS, "").count("Storage"), 0)


# ── Node-backed behavioural tests ────────────────────────────────────────────
# A fake DOM in the same spirit as test_pages_load.HARNESS: everything a page
# or card reaches for exists; document.createElement additionally RECORDS
# every element it makes, so assertions can inspect what a card actually
# wrote rather than what it merely tried to write.

CARD_HARNESS = r"""
const fs = require("fs");
const created = [];
function fakeEl(id) {
  const e = { id, textContent: "", value: "", className: "", style: {}, options: [],
    appendChild(c){ return c; }, addEventListener(){}, };
  created.push(e);
  return e;
}
global.window = { location: { href: "" }, addEventListener(){} };
global.document = {
  createElement: (t) => fakeEl(t),
  createTextNode: () => fakeEl("text"),
};
global.localStorage = { getItem: () => "", setItem(){}, removeItem(){} };
global.fetch = async () => ({ status: 200, json: async () => ({}) });
global.setInterval = () => 0;
global.setTimeout = () => 0;

new Function(fs.readFileSync(process.argv[2], "utf8"))();

function only(text) {
  const hits = created.filter((e) => e.textContent === text);
  if (hits.length !== 1) {
    console.error("expected exactly one element with textContent " +
                   JSON.stringify(text) + ", found " + hits.length);
    process.exit(1);
  }
  return hits[0];
}

function expect(cond, msg) {
  if (!cond) { console.error(msg); process.exit(1); }
}

const card = window.HearthSwitchCard.mount(fakeEl("m"), {
  cls: { state: "state" },
  state: async () => null,
  live: async () => null,
  submit: async () => ({ status: 200, data: { ok: true } }),
});

card.say("hello", "warn");
let el = only("hello");
expect(el.className === "state warn", "warn: got className " + el.className);

card.say("done", "ok");
el = only("done");
expect(el.className === "state ok", "ok: got className " + el.className);

card.say("plain");
el = only("plain");
expect(el.className === "state", "no-tone: got className " + el.className);

const card2 = window.HearthSwitchCard.mount(fakeEl("m2"), {
  cls: {},
  state: async () => null,
  live: async () => null,
  submit: async () => ({ status: 200, data: { ok: true } }),
});
card2.say("x", "err");
el = only("x");
expect(el.className === "err", "no host state class: got className " + el.className);

console.log("OK");
process.exit(0);
"""

CLEAR_ON_EDGE_RE = re.compile(r"function clearOnEdge\(wasUp, nowUp, armed\) \{.*?\n\}", re.S)

DEFERRED_RE = re.compile(
    r"function deferredMessage\(\) \{.*?\nfunction disarmDeferred\(\) \{.*?\n\}", re.S)


@unittest.skipUnless(NODE, "node not installed — status-line node tests skipped")
class StatusLineNode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_card_wears_the_tone(self):
        harness = self.dir / "card_harness.js"
        harness.write_text(CARD_HARNESS, encoding="utf-8")
        card_path = self.dir / "switch_card.js"
        card_path.write_text(CARD_JS, encoding="utf-8")
        r = subprocess.run([NODE, str(harness), str(card_path)],
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        self.assertIn("OK", r.stdout)

    def test_clear_on_edge_rules(self):
        page = routes_mod._LAUNCH_PAGE()
        match = CLEAR_ON_EDGE_RE.search(page)
        self.assertIsNotNone(match, "clearOnEdge not found in the launch page")
        cases = [
            (False, True, False, {"start": True, "stop": True}),
            (True, False, False, {"start": True, "stop": False}),
            (True, True, False, {"start": False, "stop": False}),
            (False, False, True, {"start": False, "stop": False}),
            (False, True, True, {"start": False, "stop": True}),
            (None, False, False, {"start": True, "stop": False}),
        ]
        inputs = [c[:3] for c in cases]
        script = self.dir / "clear_on_edge.js"
        script.write_text(
            match.group(0) + "\n" +
            "const inputs = " + json.dumps(inputs) + ";\n"
            "console.log(JSON.stringify(inputs.map(c => clearOnEdge(c[0], c[1], c[2]))));\n",
            encoding="utf-8")
        r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        results = json.loads(r.stdout)
        for (wasUp, nowUp, armed, want), got in zip(cases, results):
            with self.subTest(wasUp=wasUp, nowUp=nowUp, armed=armed):
                self.assertEqual(got, {"start": want["start"], "stop": want["stop"]})

    def test_deferred_start_keeps_its_count_and_speaks_plainly(self):
        page = routes_mod._LAUNCH_PAGE()
        match = DEFERRED_RE.search(page)
        self.assertIsNotNone(match, "deferred-start functions not found in the launch page")
        script = self.dir / "deferred.js"
        script.write_text(
            "let deferred = null, deferredTimer = null;\n"
            "const card = { say(){} };\n"
            "let now = 0;\n"
            "const Date = { now: () => now };\n"
            "const setInterval = () => 1;\n"
            "const clearInterval = () => {};\n" +
            match.group(0) + "\n"
            "const results = {};\n"
            "now = 1000;\n"
            "armDeferred({}, {character: \"demo\", op: \"compact\"});\n"
            "results.first_since = deferred.since;\n"
            "now = 6000;\n"
            "armDeferred({}, {character: \"other\", op: \"compact\"});\n"
            "results.rearm_since = deferred.since;\n"
            "results.rearm_holder = deferred.holder.character;\n"
            "disarmDeferred();\n"
            "now = 9000;\n"
            "armDeferred({}, {character: \"demo\", op: \"compact\"});\n"
            "results.fresh_since = deferred.since;\n"
            "deferred = {holder: {op: \"compact\", character: \"demo\", "
            "session: \"session-2026-01-01T00-00-00\"}, since: 0};\n"
            "now = 5000;\n"
            "results.compact_msg = deferredMessage();\n"
            "deferred = {holder: {op: \"leg\", character: \"demo\"}, since: 0};\n"
            "results.maint_msg = deferredMessage();\n"
            "console.log(JSON.stringify(results));\n",
            encoding="utf-8")
        r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        results = json.loads(r.stdout)
        self.assertEqual(results["first_since"], 1000)
        self.assertEqual(results["rearm_since"], 1000)
        self.assertEqual(results["rearm_holder"], "other")
        self.assertEqual(results["fresh_since"], 9000)
        self.assertIn("demo", results["compact_msg"])
        self.assertIn("~2 min", results["compact_msg"])
        self.assertIn("waiting", results["compact_msg"])
        self.assertNotIn("session-2026-01-01T00-00-00", results["compact_msg"])
        self.assertIn("maintenance on demo", results["maint_msg"])
        self.assertNotIn("~2 min", results["maint_msg"])


if __name__ == "__main__":
    unittest.main()
