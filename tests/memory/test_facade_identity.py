"""The facade seam — app.py driven directly, no sockets.

Client-declared identity, the internal-request bypass, and what the memory glue
is handed on each request.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from hearth.serve import app as serve_app  # noqa: E402


# ── the facade seam: app.py driven directly, no sockets ──────────────────────

class _FakeUpstream:
    """One non-streaming LLM reply, without a server."""

    def __init__(self, reply: str) -> None:
        self.status = 200
        self._reply = reply

    async def json(self):
        return {"choices": [{"message": {"content": self._reply}}]}

    def release(self):
        pass


class _FakeSession:
    def __init__(self, reply: str = "hi there") -> None:
        self.posts: list = []
        self._reply = reply

    async def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.posts.append(json)
        return _FakeUpstream(self._reply)


class _RecordingMemory:
    """Stands in for ServeMemory at the app seam (the glue itself is covered
    above): records what the facade asked, and returns a MARKED instruction so
    the swap is visible in the upstream payload."""

    def __init__(self) -> None:
        self.opened: list = []
        self.exchanges: list = []
        self.cues: list = []
        self.turn_cues: list = []

    async def instruction(self, companion, persona, channel, hint, base, cue=""):  # noqa: ANN001
        self.opened.append((companion, persona, channel, hint, base))
        self.cues.append(cue)
        return base + "\n\n[MEMORY]"

    async def turn_block(self, companion, channel, hint, cue):  # noqa: ANN001
        self.turn_cues.append(cue)
        return "[TURN BLOCK]" if cue and "favorite" in cue else ""

    def note_exchange(self, companion, channel, hint, user_text, reply_text):  # noqa: ANN001
        self.exchanges.append((companion, channel, hint, user_text, reply_text))


class _FakeRequest:
    def __init__(self, body: dict, headers=None, deps=None) -> None:
        self._body = body
        self.headers = dict(headers or {})
        self.app = {"deps": deps}

    async def json(self):
        return self._body


class TestFacadeIdentityAndMemory(unittest.TestCase):
    """serve/app.py's chat + models + speech seams, driven directly."""

    def _deps(self, memory=None, characters=None):
        return serve_app.FacadeDeps(
            system_instruction="BASE PROMPT", model_id="m", temperature=0.7,
            reasoning_effort="", character="base", ref_wav="/dev/null", tts_model="t",
            lm_base_url="http://127.0.0.1:1/v1", lm_token="x", bearer="b",
            cfg={"tts_model": "t"}, tap=None, model_name="mdl", persona="default",
            characters=dict(characters or {}), memory=memory, session=_FakeSession(),
        )

    def _chat(self, deps, body, headers=None):
        request = _FakeRequest(body, headers, deps)
        with mock.patch.object(serve_app.tts_prep, "live_llm_temperature", return_value=0.7):
            asyncio.run(serve_app._chat(request))
        return deps.session.posts[-1]

    def test_no_memory_sends_the_plain_instruction(self):
        deps = self._deps()
        out = self._chat(deps, {"messages": [{"role": "user", "content": "hello"}]})
        self.assertEqual(out["messages"][0]["content"], "BASE PROMPT")

    def test_client_declared_character_is_honored_and_junk_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "persona.md"
            persona.write_text("x", encoding="utf-8")

            def fake_persona_path(name, variant=None):  # noqa: ANN001
                return persona if name == "guest" else Path(tmp) / "missing.md"

            deps = self._deps(characters={"guest": "guest-a"})
            with mock.patch.object(serve_app.config_loader, "persona_path",
                                   side_effect=fake_persona_path), \
                 mock.patch.object(serve_app.config_loader, "compose_system_instruction",
                                   return_value="GUEST PROMPT") as compose:
                out = self._chat(deps, {"model": "guest",
                                        "messages": [{"role": "user", "content": "hello"}]})
                self.assertEqual(out["messages"][0]["content"], "GUEST PROMPT")
                self._chat(deps, {"model": "guest",
                                  "messages": [{"role": "user", "content": "again"}]})
                compose.assert_called_once_with("mdl", "guest")  # cached per companion

                for junk in ("not-a-character", "../etc/passwd", "qwen3-coder:30b", ""):
                    out = self._chat(deps, {"model": junk,
                                            "messages": [{"role": "user", "content": "hi"}]})
                    self.assertEqual(out["messages"][0]["content"], "BASE PROMPT", junk)

    def test_memory_swaps_the_instruction_and_is_fed_the_exchange(self):
        memory = _RecordingMemory()
        deps = self._deps(memory=memory)
        out = self._chat(deps, {"messages": [{"role": "user", "content": "hello"}]},
                         {"X-Hearth-Channel": "voice", "X-Hearth-Session": "walk-1"})
        self.assertEqual(out["messages"][0]["content"], "BASE PROMPT\n\n[MEMORY]")
        self.assertEqual(memory.opened,
                         [("base", "default", "voice", "walk-1", "BASE PROMPT")])
        self.assertEqual(memory.exchanges,
                         [("base", "voice", "walk-1", "hello", "hi there")])

    def test_memory_follows_the_declared_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "persona.md"
            persona.write_text("x", encoding="utf-8")
            memory = _RecordingMemory()
            deps = self._deps(memory=memory, characters={"guest": "guest-a"})
            with mock.patch.object(serve_app.config_loader, "persona_path",
                                   return_value=persona), \
                 mock.patch.object(serve_app.config_loader, "compose_system_instruction",
                                   return_value="GUEST PROMPT"):
                self._chat(deps, {"model": "guest",
                                  "messages": [{"role": "user", "content": "hello"}]})
            self.assertEqual(memory.opened[0][0], "guest")
            self.assertEqual(memory.opened[0][4], "GUEST PROMPT")
            self.assertEqual(memory.exchanges[0][0], "guest")

    def test_internal_requests_bypass_persona_and_memory(self):
        memory = _RecordingMemory()
        deps = self._deps(memory=memory)
        out = self._chat(deps, {"messages": [{"role": "system", "content": "SUMMARIZE"},
                                             {"role": "user", "content": "transcript"}]},
                         {"X-Hearth-Internal": "task"})
        self.assertEqual(out["messages"][0]["content"], "SUMMARIZE")  # its own prompt kept
        self.assertEqual(memory.opened, [])
        self.assertEqual(memory.exchanges, [])

    def test_models_lists_the_resolved_identity_plus_the_roster(self):
        deps = self._deps(characters={"guest": "guest-a", "base": "base-a"})
        resp = asyncio.run(serve_app._models(_FakeRequest({}, {}, deps)))
        ids = [row["id"] for row in json.loads(resp.body)["data"]]
        self.assertEqual(ids, ["base", "guest"])  # deduped, resolved identity first

    def test_declared_voice_bundle_is_used_only_for_roster_characters(self):
        deps = self._deps(characters={"guest": "guest-a"})
        with mock.patch.object(serve_app.config_loader, "load_voice",
                               return_value={"ref_wav": "/clip/guest.wav",
                                             "model_repo": "repo/guest"}) as load:
            self.assertIs(serve_app._voice_deps(deps, {"voice": "stranger"}), deps)
            load.assert_not_called()
            routed = serve_app._voice_deps(deps, {"voice": "guest"})
            self.assertEqual((routed.ref_wav, routed.tts_model),
                             ("/clip/guest.wav", "repo/guest"))
            self.assertEqual((deps.ref_wav, deps.tts_model), ("/dev/null", "t"))
            serve_app._voice_deps(deps, {"model": "guest"})
            load.assert_called_once_with("guest", "guest-a")  # cached per character


class TestFacadeCuePassthrough(unittest.TestCase):
    """app.py hands the user's LAST line to the glue as the recall cue —
    extracted BEFORE the instruction call (the lane (b) reorder)."""

    def test_chat_passes_the_users_last_line_as_the_cue(self):
        memory = _RecordingMemory()
        helper = TestFacadeIdentityAndMemory("test_memory_follows_the_declared_companion")
        deps = helper._deps(memory=memory)
        helper._chat(deps,
            {"messages": [{"role": "user", "content": "first line"},
                          {"role": "assistant", "content": "sure"},
                          {"role": "user", "content": "what was my favorite show?"}]})
        self.assertEqual(memory.cues, ["what was my favorite show?"])
        # and the composed messages still open with the augmented system layer
        sent = deps.session.posts[-1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("[MEMORY]", sent[0]["content"])
        self.assertEqual([m["role"] for m in sent[1:]],
                         ["user", "assistant", "user"])
        # the per-turn block rides the TAIL (the newest user message of the
        # outgoing body), never the system layer — the prompt-cache rule
        self.assertEqual(memory.turn_cues, ["what was my favorite show?"])
        self.assertEqual(sent[-1]["content"], "what was my favorite show?\n\n[TURN BLOCK]")
        self.assertEqual(sent[1]["content"], "first line")
        self.assertNotIn("[TURN BLOCK]", sent[0]["content"])

if __name__ == "__main__":
    unittest.main()
