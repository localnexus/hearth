"""test_turn_echo.py — the panel's last-reply echo (GET /turn).

The route reads the live LLMContext the panel already holds, so what needs
proving is not plumbing but what it will and will not hand back:

  1. the composed persona is a system message, and no shape of context can get
     one out of this route — the reason the filter is an allow-list;
  2. the two states the page distinguishes — the reply newest (paint it) vs your
     turn newest (still answering);
  3. content flattening: a message's content is a string OR a list of parts, and
     only text parts are read — the mic path's base64 audio must never ride a
     two-second poll;
  4. `seq` moves when a turn lands and holds when nothing did, since that is
     what tells the page "nothing new" from "that was said twice".

Duck-typed context: the module is pipecat-free, so this runs in the base venv.

Run:  .venv/bin/python -m unittest tests.test_turn_echo
"""

from __future__ import annotations

import asyncio
import types
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hearth.control.features import turn_echo

PERSONA = {"role": "system", "content": "## IDENTITY\nthe composed persona\n"}


def _ctx(*messages):
    """A PanelContext stand-in: the route touches `.context.get_messages()`."""
    return types.SimpleNamespace(
        context=types.SimpleNamespace(get_messages=lambda: list(messages)))


class TurnEcho(unittest.TestCase):

    def _get(self, *messages) -> dict:
        async def go():
            app = web.Application()
            app.add_routes(turn_echo.turn_echo_routes(_ctx(*messages)))
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/turn")
                self.assertEqual(resp.status, 200)
                return await resp.json()
        return asyncio.run(go())

    # 1 · the persona never comes back out

    def test_a_system_only_context_echoes_nothing(self):
        body = self._get(PERSONA)
        self.assertEqual((body["seq"], body["role"], body["text"]), (0, None, ""))

    def test_the_persona_is_never_the_message_echoed(self):
        """Even as the newest message — a mid-session system note, a rebased
        context — role is the filter, so there is no ordering that leaks it."""
        body = self._get({"role": "user", "content": "hello"}, PERSONA)
        self.assertEqual(body["role"], "user")
        self.assertNotIn("composed persona", body["text"])

    def test_no_message_of_any_shape_carries_persona_text(self):
        for body in (self._get(PERSONA),
                     self._get(PERSONA, {"role": "assistant", "content": "hi"}),
                     self._get(PERSONA, {"role": "user", "content": "?"})):
            self.assertNotIn("composed persona", body["text"])

    # 2 · the two states the page paints

    def test_her_reply_newest_is_the_echo(self):
        body = self._get(PERSONA,
                         {"role": "user", "content": "how did you sleep"},
                         {"role": "assistant", "content": "I don't, quite."})
        self.assertEqual(body["role"], "assistant")
        self.assertEqual(body["text"], "I don't, quite.")
        self.assertFalse(body["waiting"])
        self.assertEqual(body["seq"], 2)   # the system message is not counted

    def test_your_turn_newest_reads_as_waiting(self):
        body = self._get(PERSONA, {"role": "user", "content": "you there?"})
        self.assertEqual(body["role"], "user")
        self.assertTrue(body["waiting"])

    def test_seq_moves_only_when_a_turn_lands(self):
        first = self._get(PERSONA, {"role": "assistant", "content": "same"})
        again = self._get(PERSONA, {"role": "assistant", "content": "same"})
        self.assertEqual(first["seq"], again["seq"])
        later = self._get(PERSONA,
                          {"role": "assistant", "content": "same"},
                          {"role": "user", "content": "and?"},
                          {"role": "assistant", "content": "same"})
        self.assertGreater(later["seq"], first["seq"])

    # 3 · content flattening

    def test_list_content_reads_its_text_parts_in_order(self):
        body = self._get({"role": "assistant", "content": [
            {"type": "text", "text": "two "}, {"type": "text", "text": "parts"}]})
        self.assertEqual(body["text"], "two parts")

    def test_binary_parts_never_reach_the_poll(self):
        """The mic path puts base64 audio in a user message; an image can ride
        one too. Neither is displayable, and both are large."""
        blob = "A" * 4096
        body = self._get({"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": blob, "format": "wav"}},
            {"type": "text", "text": "spoken words"}]})
        self.assertEqual(body["text"], "spoken words")
        self.assertNotIn(blob, body["text"])

    def test_an_unreadable_content_shape_is_empty_not_a_crash(self):
        body = self._get({"role": "assistant", "content": None})
        self.assertEqual(body["text"], "")

    # 4 · entries that are not plain messages

    def test_provider_specific_entries_are_skipped(self):
        """LLMContext can hold LLMSpecificMessage objects — one provider's wire
        format, not a conversation turn."""
        specific = types.SimpleNamespace(llm="anthropic", message={"role": "user"})
        body = self._get(PERSONA, {"role": "assistant", "content": "mine"}, specific)
        self.assertEqual(body["role"], "assistant")
        self.assertEqual(body["text"], "mine")


if __name__ == "__main__":
    unittest.main()
