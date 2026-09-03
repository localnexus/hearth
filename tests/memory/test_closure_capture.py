"""Memory seam — closure-gated capture.

ONE extraction call answers {closure, topic}; the JSON parser is hostile
(think-tags, prose, bad types, over-long topics). Capture writes a slot ONLY when
a topic was stated — a bare close writes nothing, and no closure writes nothing.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.memory import intent as intent_mod  # noqa: E402


class TestClosureDetection(unittest.TestCase):
    """The ONE extraction call, parsed hostilely. The transport is mocked in
    every test — these make zero network calls."""

    CFG = {"llm_provider": "ollama", "llm_model": "testmodel", "llm_url": ""}
    MSGS = [{"role": "user", "content": "goodnight"},
            {"role": "assistant", "content": "sleep well"}]

    def _detect(self, answer):
        with mock.patch.object(intent_mod, "_ollama_chat", return_value=answer) as llm:
            got = intent_mod.detect_closure_and_topic(self.MSGS, self.CFG)
        self.assertEqual(llm.call_count, 1)  # one call answers both questions
        return got

    def test_good_json(self):
        self.assertEqual(self._detect('{"closure": true, "topic": "the tea ceremony"}'),
                         (True, "the tea ceremony"))
        self.assertEqual(self._detect('{"closure": false, "topic": null}'), (False, None))
        self.assertEqual(self._detect('{"closure": true, "topic": null}'), (True, None))
        self.assertEqual(self._detect('{"closure": false, "topic": "the tea ceremony"}'),
                         (False, "the tea ceremony"))

    def test_think_tags_fences_and_commentary_are_scanned_past(self):
        noisy = ("<think>they said goodnight</think>\n"
                 "```json\n"
                 '{"closure": true, "topic": "the tea ceremony"}\n'
                 "```\n"
                 "Hope that helps.")
        self.assertEqual(self._detect(noisy), (True, "the tea ceremony"))

    def test_a_brace_inside_the_topic_does_not_end_the_scan(self):
        self.assertEqual(self._detect('{"closure": true, "topic": "the {tea} ceremony"}'),
                         (True, "the {tea} ceremony"))

    def test_malformed_answers_conclude_nothing(self):
        for answer in ("", "none", "yes, they said goodbye", "{not json}",
                       '{"closure": "yes", "topic": "x"}', '["closure"]',
                       '{"topic": "the tea ceremony"}'):
            self.assertEqual(self._detect(answer), (False, None), answer[:28])

    def test_an_unusable_topic_is_dropped_but_the_closure_verdict_survives(self):
        # A rambling or mistyped topic says nothing about whether the user
        # actually said goodbye — the two answers fail independently.
        self.assertEqual(self._detect(json.dumps({"closure": True, "topic": "x" * 500})),
                         (True, None))
        self.assertEqual(self._detect('{"closure": true, "topic": 42}'), (True, None))
        self.assertEqual(self._detect('{"closure": true, "topic": "none"}'), (True, None))

    def test_no_seat_never_reaches_the_transport(self):
        with mock.patch.object(intent_mod, "_ollama_chat") as llm:
            self.assertEqual(
                intent_mod.detect_closure_and_topic(self.MSGS, {"llm_provider": "openai"}),
                (False, None))
            self.assertEqual(
                intent_mod.detect_closure_and_topic(self.MSGS, {"llm_provider": "ollama"}),
                (False, None))
        llm.assert_not_called()

    def test_transport_failure_concludes_nothing(self):
        with mock.patch.object(intent_mod, "_ollama_chat", side_effect=OSError("refused")):
            self.assertEqual(intent_mod.detect_closure_and_topic(self.MSGS, self.CFG),
                             (False, None))


class TestCaptureGating(unittest.TestCase):
    """capture()'s three outcomes: only a STATED topic keeps anything."""

    CFG = {"llm_provider": "ollama", "llm_model": "testmodel", "llm_url": ""}
    MSGS = [{"role": "user", "content": "goodnight — next time the tea ceremony"},
            {"role": "assistant", "content": "I'd like that."}]

    def _capture(self, answer):
        with tempfile.TemporaryDirectory() as tmp:
            slot = Path(tmp) / "intent.json"
            with mock.patch.object(intent_mod, "intent_path", return_value=slot), \
                 mock.patch.object(intent_mod, "_ollama_chat", return_value=answer):
                topic = intent_mod.capture("testchar", self.MSGS, "s1", self.CFG)
            return topic, slot.exists()

    def test_stated_topic_writes_the_slot(self):
        self.assertEqual(self._capture('{"closure": true, "topic": "the tea ceremony"}'),
                         ("the tea ceremony", True))

    def test_topic_without_closure_still_writes(self):
        # A plan named mid-conversation is still the plan.
        self.assertEqual(self._capture('{"closure": false, "topic": "the tea ceremony"}'),
                         ("the tea ceremony", True))

    def test_closure_without_a_topic_writes_nothing(self):
        self.assertEqual(self._capture('{"closure": true, "topic": null}'), (None, False))

    def test_no_closure_writes_nothing(self):
        # The gate the facade lane needs: an idle timeout is not a goodbye.
        self.assertEqual(self._capture('{"closure": false, "topic": null}'), (None, False))

if __name__ == "__main__":
    unittest.main()
