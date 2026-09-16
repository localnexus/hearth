"""The phone page's four pure helpers, and the launch page's route line.

All five are cut out of the served HTML and run under node, in the same spirit
as test_stop_card.py — no DOM, no clock, no socket, so a case is a one-line
assertion about what comes back.

They are worth this because each carries a measured requirement that a reading
of the page would not reveal:

  pcmFrames    the microphone worklet delivers blocks burstily (~19 % of gaps
               under 1 ms, then a pause), so the page must accumulate into
               paced 20 ms frames rather than send per block.
  ringDrop     a queue that only absorbs reproduces the unbounded accumulation
               measured on 2026-09-15; the far end must DROP the oldest audio.
               The playback worklet is built from this function's own source,
               so what runs in the audio thread is what is checked here.
  socketUrl    a secure page may not open a plain ws:// socket, and the socket
               lives on its own port.
  backoffDelay a phone that lost its network keeps trying all through a walk,
               without hammering anything.
  routeLine    what the Stop card says about where the audio is.

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

NODE = shutil.which("node")

CUTS = {
    "socketUrl": re.compile(r"function socketUrl\(protocol, host, port\) \{.*?\n\}", re.S),
    "pcmFrames": re.compile(r"function pcmFrames\(carry, block, frameSamples\) \{.*?\n\}", re.S),
    "ringDrop": re.compile(r"function ringDrop\(queued, incoming, depth\) \{.*?\n\}", re.S),
    "backoffDelay": re.compile(r"function backoffDelay\(attempt\) \{.*?\n\}", re.S),
}
ROUTE_LINE = re.compile(r"function routeLine\(switches, route\) \{.*?\n\}", re.S)


class _NodeCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _cut(self, page: str, pattern, name: str) -> str:
        match = pattern.search(page)
        self.assertIsNotNone(match, f"{name} not found in the page")
        return match.group(0)

    def _run(self, name: str, source: str) -> dict:
        script = self.dir / f"{name}.js"
        script.write_text(source, encoding="utf-8")
        r = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip())
        return json.loads(r.stdout)


@unittest.skipUnless(NODE, "node not installed — voice-page node tests skipped")
class TheVoicePageHelpers(_NodeCase):

    def setUp(self):
        self.page = routes_mod._VOICE_PAGE()

    def _helpers(self, *names) -> str:
        return "const SOCKET_PORT = 65021;\n" + "\n".join(
            self._cut(self.page, CUTS[name], name) for name in names) + "\n"

    def test_the_socket_is_secure_whenever_the_page_is(self):
        out = self._run("socket_url", self._helpers("socketUrl") + """
          const r = {};
          r.https = socketUrl("https:", "phone.example.ts.net", 65021);
          r.http = socketUrl("http:", "phone.example.ts.net", 65021);
          r.default_port = socketUrl("https:", "h", 0);
          r.overridden = socketUrl("http:", "h", 65099);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["https"], "wss://phone.example.ts.net:65021")
        self.assertEqual(out["http"], "ws://phone.example.ts.net:65021")
        self.assertEqual(out["default_port"], "wss://h:65021")
        self.assertEqual(out["overridden"], "ws://h:65099")

    def test_blocks_become_whole_frames_and_the_remainder_is_carried(self):
        """128-sample blocks at 16 kHz; a 20 ms frame is 320 samples. Three
        blocks make one frame with 64 samples left over, and the carry is what
        keeps the cadence honest from there: the fifth block completes the
        next frame exactly, the sixth starts the one after."""
        out = self._run("pcm_frames", self._helpers("pcmFrames") + """
          const block = new Float32Array(128).fill(0.5);
          let carry = new Float32Array(0);
          const counts = [], carried = [];
          for (let i = 0; i < 6; i++) {
            const paced = pcmFrames(carry, block, 320);
            carry = paced.carry;
            counts.push(paced.frames.length);
            carried.push(carry.length);
          }
          const one = pcmFrames(new Float32Array(0), new Float32Array(320).fill(0.5), 320);
          console.log(JSON.stringify({ counts, carried,
            width: one.frames[0].length,
            sample: one.frames[0][0],
            type: one.frames[0].constructor.name }));
        """)
        self.assertEqual(out["counts"], [0, 0, 1, 0, 1, 0])
        self.assertEqual(out["carried"], [128, 256, 64, 192, 0, 128])
        self.assertEqual(out["width"], 320, "a frame is 20 ms at 16 kHz")
        self.assertEqual(out["type"], "Int16Array")
        self.assertEqual(out["sample"], int(0.5 * 0x7fff),
                         "a positive sample scales by 32767, not 32768")

    def test_a_full_scale_sample_never_wraps_to_the_other_sign(self):
        """The classic int16 conversion bug: +1.0 scaled by 32768 overflows to
        -32768 and a loud moment becomes a click."""
        out = self._run("pcm_clip", self._helpers("pcmFrames") + """
          const loud = new Float32Array(320);
          loud.fill(1.0); loud[0] = -1.0; loud[1] = 2.5; loud[2] = -2.5;
          const f = pcmFrames(new Float32Array(0), loud, 320).frames[0];
          console.log(JSON.stringify({ min: f[0], over: f[1], under: f[2], max: f[3] }));
        """)
        self.assertEqual(out["min"], -32768)
        self.assertEqual(out["max"], 32767)
        self.assertEqual(out["over"], 32767, "a sample above 1.0 is clipped")
        self.assertEqual(out["under"], -32768, "a sample below -1.0 is clipped")

    def test_the_ring_drops_only_what_it_must_and_only_when_it_must(self):
        out = self._run("ring_drop", self._helpers("ringDrop") + """
          const r = {};
          r.room = ringDrop(100, 200, 1000);
          r.exactly_full = ringDrop(800, 200, 1000);
          r.one_over = ringDrop(900, 200, 1000);
          r.far_over = ringDrop(1000, 5000, 1000);
          r.empty_queue = ringDrop(0, 5000, 1000);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["room"], 0)
        self.assertEqual(out["exactly_full"], 0, "at the depth, nothing goes")
        self.assertEqual(out["one_over"], 100, "only the overflow goes")
        self.assertEqual(out["far_over"], 1000,
                         "it can never drop more than it holds")
        self.assertEqual(out["empty_queue"], 0,
                         "an incoming chunk larger than the depth is still played")

    def test_the_backoff_doubles_and_stops_at_three_minutes(self):
        out = self._run("backoff", self._helpers("backoffDelay") + """
          const waits = [];
          for (let i = 0; i < 12; i++) waits.push(backoffDelay(i));
          console.log(JSON.stringify({ waits, negative: backoffDelay(-3) }));
        """)
        self.assertEqual(out["waits"][:5], [500, 1000, 2000, 4000, 8000])
        self.assertEqual(max(out["waits"]), 180000, "capped at three minutes")
        self.assertEqual(out["waits"][-1], 180000)
        self.assertEqual(out["negative"], 500, "a nonsense attempt still waits")

    def test_the_playback_worklet_is_built_from_the_checked_rule_itself(self):
        """Not a second copy of the drop rule living in a template string: the
        worklet source is `ringDrop.toString()`, so the two cannot drift."""
        self.assertIn("ringDrop.toString()", self.page)
        self.assertEqual(self.page.count("function ringDrop("), 1)

    def test_the_page_reads_the_same_key_the_pairing_page_writes(self):
        pair = routes_mod._PAIR_PAGE()
        key = re.search(r'const TOKEN_KEY = "([^"]+)"', pair).group(1)
        self.assertIn(f'const TOKEN_KEY = "{key}"', self.page)

    def test_the_page_says_what_to_do_when_the_microphone_is_not_offered(self):
        """The measured symptom looks like a broken page, not a refusal, so the
        page has to name it in words and name the fallback."""
        self.assertIn("navigator.mediaDevices", self.page)
        self.assertIn("https", self.page)
        self.assertIn("insecure origins", self.page)


@unittest.skipUnless(NODE, "node not installed — voice-page node tests skipped")
class TheStopCardsRouteLine(_NodeCase):

    def setUp(self):
        self.page = routes_mod._LAUNCH_PAGE()

    def test_it_states_the_route_fixed_at_start_and_what_became_of_it(self):
        source = self._cut(self.page, ROUTE_LINE, "routeLine") + """
          const r = {};
          r.desk = routeLine({route: "desk"}, {kind: "desk", state: "pinned"});
          r.nothing = routeLine(null, null);
          r.waiting = routeLine({route: "remote:Pixel"},
                                {kind: "remote", state: "waiting"});
          r.connected = routeLine({route: "remote:Pixel"},
            {kind: "remote", state: "connected", path: "direct", buffer_ms: 60, shed_ms: 0});
          r.relayed = routeLine({route: "remote:Pixel"},
            {kind: "remote", state: "connected", path: "relayed", buffer_ms: 200, shed_ms: 140});
          r.lost = routeLine({route: "remote:Pixel"}, {kind: "remote", state: "lost"});
          r.no_report_yet = routeLine({route: "remote:Pixel"}, null);
          console.log(JSON.stringify(r));
        """
        out = self._run("route_line", source)
        self.assertEqual(out["desk"], "audio: the desk")
        self.assertEqual(out["nothing"], "audio: the desk")
        self.assertEqual(out["waiting"], "audio: Pixel — waiting for it to connect")
        self.assertEqual(out["connected"],
                         "audio: Pixel — connected (direct, 60 ms buffer)")
        self.assertIn("relayed, 200 ms buffer", out["relayed"])
        self.assertIn("dropped 140 ms", out["relayed"])
        self.assertEqual(out["lost"], "audio: Pixel — that device dropped off")
        self.assertEqual(out["no_report_yet"], "audio: Pixel")


if __name__ == "__main__":
    unittest.main()
