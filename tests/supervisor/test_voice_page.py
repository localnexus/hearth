"""The phone page's pure helpers, and the launch page's route line.

All of them are cut out of the served HTML and run under node, in the same
spirit as test_stop_card.py — no DOM, no clock, no socket, so a case is a
one-line assertion about what comes back.

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
  retryVerdict a phone in a pocket must stop retrying once the conversation it
               is retrying into has certainly closed — a flat battery and a
               warm radio otherwise, all night.
  routeLine    what the Stop card says about where the audio is — and the two
               losses are different stories, which is the whole reason this
               one grew.
  routeChoices what the audio-route control should hold, from the enrolled
               devices: the selector must not move under somebody's hand when
               a poll arrives, and it must not offer to forget the device a
               live conversation is speaking on.
  guessLabel   what to call a device, guessed from what its browser says it
               is — a Pixel says Android too, and an iPhone says Mac OS X, so
               the order of the questions is the whole of the answer.
  graceText    a countdown as a person reads one.
  lastExitLine why the last conversation ended, when nobody was there to see.

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
    "retryVerdict": re.compile(
        r"function retryVerdict\(elapsedMs, graceS\) \{.*?\n\}", re.S),
}
#: The launch page's own, cut from the launch page.
CARD = {
    "routeLine": re.compile(
        r"function routeLine\(switches, route, graceLeft, devices\) \{.*?\n\}",
        re.S),
    "deviceLabel": re.compile(r"function deviceLabel\(devices, id\) \{.*?\n\}", re.S),
    "graceText": re.compile(r"function graceText\(seconds\) \{.*?\n\}", re.S),
    "lastExitLine": re.compile(r"function lastExitLine\(bot\) \{.*?\n\}", re.S),
    "routeChoices": re.compile(
        r"function routeChoices\(devices, stored, checkedNow, liveRoute\) \{.*?\n\}",
        re.S),
    "routeSignature": re.compile(
        r"function routeSignature\(devices, liveRoute\) \{.*?\n\}", re.S),
}
#: The pairing page's one pure helper, cut from the pairing page.
PAIR = {
    "guessLabel": re.compile(r"function guessLabel\(userAgent\) \{.*?\n\}", re.S),
}


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

    def test_it_stops_retrying_once_the_conversation_has_certainly_closed(self):
        """Thirty seconds past the wait the conversation is gone, and a phone
        that keeps trying is spending a battery on a door that is shut. When
        the server never said how long it waits — an older one — retry the way
        this page always has: the caution belongs to the side that knows."""
        out = self._run("retry_verdict", self._helpers("retryVerdict") + """
          const r = {};
          r.fresh = retryVerdict(0, 180);
          r.inside = retryVerdict(150000, 180);
          r.on_the_line = retryVerdict(209000, 180);
          r.past_it = retryVerdict(211000, 180);
          r.no_word_from_the_server = retryVerdict(9999999, null);
          r.nonsense_word = retryVerdict(9999999, 0);
          r.short_window = retryVerdict(40000, 5);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["fresh"], "retry")
        self.assertEqual(out["inside"], "retry")
        self.assertEqual(out["on_the_line"], "retry", "the wait plus thirty")
        self.assertEqual(out["past_it"], "give-up")
        self.assertEqual(out["no_word_from_the_server"], "retry")
        self.assertEqual(out["nonsense_word"], "retry")
        self.assertEqual(out["short_window"], "give-up")

    def test_the_page_never_says_disconnected_and_says_the_wait_instead(self):
        """The conversation is still there; a page that says otherwise makes a
        person stop trying while it waits for them."""
        for line, why in (
                ("the conversation waits ", "the wait, on the reconnect line"),
                ("the conversation closed while this device was away",
                 "what 4410 means, in words"),
                ("press Start when you are back", "what to do after giving up")):
            with self.subTest(line=line):
                self.assertTrue(line in self.page, why)
        self.assertFalse("disconnected" in self.page,
                         "the page must never say the word that means it is over")

    def test_a_rejoin_flushes_the_ring_before_anything_new_is_played(self):
        """Audio from before the loss is the companion answering the question
        before last."""
        self.assertTrue(
            "if (rejoined) playNode.port.postMessage({ flush: true });" in self.page,
            "the ring is flushed on a rejoin, before anything new is played")

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
    """One card, two stories.

    A desk headset that is away is silence you can wait out: the conversation
    waits for it indefinitely, and only that device will do. A device on the
    far end of a socket is a countdown, at the end of which the conversation
    closes itself. The same word "waiting" for both would be true and useless,
    which is what these cases are here to prevent.
    """

    def setUp(self):
        self.page = routes_mod._LAUNCH_PAGE()

    def _helpers(self, *names) -> str:
        """routeLine reads a device's label out of the list, so it never cuts
        alone — the helper it calls comes with it."""
        wanted = list(names)
        if "routeLine" in wanted and "deviceLabel" not in wanted:
            wanted.append("deviceLabel")
        return "\n".join(self._cut(self.page, CARD[name], name)
                         for name in wanted) + "\n"

    def test_the_desk_tells_its_own_loss_story(self):
        out = self._run("route_desk", self._helpers("routeLine", "graceText") + """
          const r = {};
          const sw = {route: "desk"};
          r.pinned = routeLine(sw, {kind: "desk", state: "pinned"});
          r.lost = routeLine(sw, {kind: "desk", state: "lost"});
          r.recovered = routeLine(sw, {kind: "desk", state: "recovered"});
          r.unpinned = routeLine(sw, {kind: "desk", state: "unpinned"});
          r.nothing = routeLine(null, null);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["pinned"], "audio: the desk")
        self.assertEqual(out["lost"],
                         "audio: the desk — the headset is away; waiting, and "
                         "it will come back on the same device only")
        self.assertEqual(out["recovered"], "audio: the desk — the headset came back")
        self.assertEqual(out["unpinned"], "audio: the desk — no device pinned")
        self.assertEqual(out["nothing"], "audio: the desk")
        self.assertNotIn("left", out["lost"], "the desk never counts down")

    def test_the_remote_route_counts_down_and_says_what_is_at_the_end_of_it(self):
        out = self._run("route_remote", self._helpers("routeLine", "graceText") + """
          const r = {};
          const sw = {route: "remote:Pixel"};
          r.waiting = routeLine(sw, {kind: "remote", state: "waiting"});
          r.connected = routeLine(sw,
            {kind: "remote", state: "connected", path: "direct", buffer_ms: 120, shed_ms: 0});
          r.behind = routeLine(sw,
            {kind: "remote", state: "connected", path: "relayed", buffer_ms: 200, shed_ms: 140});
          r.local = routeLine(sw,
            {kind: "remote", state: "connected", path: "local", buffer_ms: 120, shed_ms: 0});
          r.lost = routeLine(sw, {kind: "remote", state: "lost", grace_left: 160}, 160);
          r.nearly = routeLine(sw, {kind: "remote", state: "lost", grace_left: 5}, 5);
          r.lost_unknown = routeLine(sw, {kind: "remote", state: "lost"}, null);
          r.ended = routeLine(sw, {kind: "remote", state: "ended"});
          r.no_report_yet = routeLine(sw, null);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["waiting"], "audio: Pixel — waiting for it to "
                                         "connect (open the talk page on it)")
        self.assertEqual(out["connected"],
                         "audio: Pixel — connected (direct, 120 ms buffer)")
        self.assertIn("relayed, 200 ms buffer", out["behind"])
        self.assertIn("the phone is falling behind (140 ms dropped)", out["behind"])
        self.assertIn("(local, 120 ms buffer)", out["local"])
        self.assertEqual(out["lost"], "audio: Pixel — waiting for Pixel, 2:40 "
                                      "left; then this conversation closes")
        self.assertIn("0:05 left", out["nearly"])
        self.assertEqual(out["lost_unknown"], "audio: Pixel — waiting for "
                                              "Pixel; then this conversation closes")
        self.assertEqual(out["ended"], "audio: Pixel — it did not come back; closing")
        self.assertEqual(out["no_report_yet"], "audio: Pixel")

    def test_the_countdown_reads_as_a_clock(self):
        out = self._run("grace_text", self._helpers("graceText") + """
          const r = {};
          for (const s of [160, 180, 60, 59, 5, 0, -30]) r[String(s)] = graceText(s);
          r.nothing = graceText(null);
          r.words = graceText("soon");
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["160"], "2:40")
        self.assertEqual(out["180"], "3:00")
        self.assertEqual(out["60"], "1:00")
        self.assertEqual(out["59"], "0:59")
        self.assertEqual(out["5"], "0:05", "two digits, always")
        self.assertEqual(out["0"], "0:00")
        self.assertEqual(out["-30"], "0:00", "never below zero")
        self.assertEqual(out["nothing"], "0:00")
        self.assertEqual(out["words"], "0:00")

    def test_a_conversation_that_closed_itself_says_so_afterwards(self):
        """Nobody was there to see it. The exit status is the only witness, and
        3 means exactly one thing — but only on a remote route, and only once
        the conversation is actually down."""
        out = self._run("last_exit", self._helpers("lastExitLine") + """
          const r = {};
          r.gone = lastExitLine({last_exit: {code: 3}, switches: {route: "remote:Pixel"}});
          r.desk = lastExitLine({last_exit: {code: 3}, switches: {route: "desk"}});
          r.stopped = lastExitLine({last_exit: {code: 0}, switches: {route: "remote:Pixel"}});
          r.crashed = lastExitLine({last_exit: {code: 1}, switches: {route: "remote:Pixel"}});
          r.unknowable = lastExitLine({last_exit: {code: null}, switches: {route: "remote:Pixel"}});
          r.never_ran = lastExitLine({last_exit: null, switches: {route: "remote:Pixel"}});
          r.nothing = lastExitLine(null);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["gone"], "the last conversation closed itself: "
                                      "Pixel did not come back within the wait")
        self.assertEqual(out["desk"], "", "the desk waits forever; it cannot be this")
        for quiet in ("stopped", "crashed", "unknowable", "never_ran", "nothing"):
            with self.subTest(case=quiet):
                self.assertEqual(out[quiet], "")

    def test_an_enrolled_device_is_named_the_way_its_owner_named_it(self):
        """`remote:pixel-3f4a` is a route word, not something to read. The Stop
        card says the label the person typed when they paired the thing — and
        falls back to the id, which is what a conversation on a device that has
        since been forgotten looks like."""
        out = self._run("route_label",
                        self._helpers("routeLine", "graceText", "deviceLabel") + """
          const r = {};
          const sw = {route: "remote:pixel-3f4a"};
          const list = [{id: "pixel-3f4a", label: "Pixel"}, {id: "ipad", label: "iPad"}];
          const live = {kind: "remote", state: "connected", path: "direct", buffer_ms: 120};
          r.labelled = routeLine(sw, live, null, list);
          r.forgotten = routeLine(sw, live, null, [{id: "ipad", label: "iPad"}]);
          r.no_list = routeLine(sw, live, null, null);
          r.unlabelled = routeLine(sw, live, null, [{id: "pixel-3f4a", label: ""}]);
          r.waiting = routeLine(sw, {kind: "remote", state: "waiting"}, null, list);
          r.desk = routeLine({route: "desk"}, {kind: "desk", state: "pinned"}, null, list);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["labelled"],
                         "audio: Pixel — connected (direct, 120 ms buffer)")
        self.assertEqual(out["forgotten"],
                         "audio: pixel-3f4a — connected (direct, 120 ms buffer)")
        self.assertEqual(out["no_list"],
                         "audio: pixel-3f4a — connected (direct, 120 ms buffer)")
        self.assertEqual(out["unlabelled"],
                         "audio: pixel-3f4a — connected (direct, 120 ms buffer)")
        self.assertEqual(out["waiting"],
                         "audio: Pixel — waiting for it to connect "
                         "(open the talk page on it)")
        self.assertEqual(out["desk"], "audio: the desk")

    def test_the_countdown_is_derived_from_the_clock_not_from_the_poll(self):
        """The page polls on its own cadence; a count drawn only on arrival
        would sit still and then jump, which reads as a stuck page at exactly
        the moment a person is watching it closely. And it must never run
        backwards — a slow answer cannot push the count back up."""
        for line, why in (
                ("function graceNow()", "the seconds come from the clock"),
                ("Math.floor((Date.now() - graceAt) / 1000)",
                 "counted from when the poll's number arrived"),
                ("route.grace_left < showing",
                 "a later poll is adopted only when it is lower"),
                ("setInterval(", "and it is redrawn between polls")):
            with self.subTest(line=line):
                self.assertTrue(line in self.page, why)


@unittest.skipUnless(NODE, "node not installed — voice-page node tests skipped")
class TheAudioRouteSelector(_NodeCase):
    """The control a person picks a device with, before there is a device.

    The list arrives on a poll, which means it can arrive while somebody is
    halfway through choosing — so what is checked must survive a redraw, and a
    redraw must not happen at all unless something actually changed.
    """

    def setUp(self):
        self.page = routes_mod._LAUNCH_PAGE()

    def _helpers(self, *names) -> str:
        return "\n".join(self._cut(self.page, CARD[name], name)
                          for name in names) + "\n"

    def test_with_nothing_paired_the_only_remote_choice_is_held_shut(self):
        out = self._run("route_empty", self._helpers("routeChoices") + """
          const r = {};
          r.empty = routeChoices([], "", "", "");
          r.missing = routeChoices(null, "", "", "");
          console.log(JSON.stringify(r));
        """)
        for case in ("empty", "missing"):
            with self.subTest(case=case):
                rows = out[case]["rows"]
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["value"], "desk")
                self.assertTrue(rows[1]["disabled"])
                self.assertIn("pair one first", rows[1]["label"])
                self.assertEqual(out[case]["checked"], "desk")

    def test_every_paired_device_is_a_radio_named_the_way_it_was_named(self):
        out = self._run("route_two", self._helpers("routeChoices") + """
          const list = [
            {id: "pixel-3f4a", label: "Pixel", paired_at: "2026-09-18T04:10:11-07:00"},
            {id: "ipad", label: "the kitchen iPad", paired_at: "2026-09-01T09:00:00-07:00"}];
          console.log(JSON.stringify(routeChoices(list, "", "", "")));
        """)
        rows = out["rows"]
        self.assertEqual([row["value"] for row in rows],
                         ["desk", "remote:pixel-3f4a", "remote:ipad"])
        self.assertEqual(rows[1]["label"], "Pixel")
        self.assertEqual(rows[1]["title"], "pixel-3f4a · paired 2026-09-18")
        self.assertEqual(rows[2]["label"], "the kitchen iPad")
        self.assertEqual(out["checked"], "desk", "the desk is the default")

    def test_the_last_choice_comes_back_when_that_device_still_exists(self):
        out = self._run("route_remembered", self._helpers("routeChoices") + """
          const r = {};
          const list = [{id: "pixel", label: "Pixel"}, {id: "ipad", label: "iPad"}];
          r.remembered = routeChoices(list, "ipad", "", "").checked;
          r.desk = routeChoices(list, "desk", "", "").checked;
          r.forgotten = routeChoices(list, "nobody", "", "").checked;
          r.none_yet = routeChoices(list, "", "", "").checked;
          r.gone_entirely = routeChoices([], "ipad", "", "").checked;
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["remembered"], "remote:ipad")
        self.assertEqual(out["desk"], "desk")
        self.assertEqual(out["forgotten"], "desk",
                         "a remembered device that is gone falls back")
        self.assertEqual(out["none_yet"], "desk")
        self.assertEqual(out["gone_entirely"], "desk")

    def test_a_poll_never_moves_the_radio_under_somebodys_hand(self):
        out = self._run("route_stable", self._helpers("routeChoices") + """
          const r = {};
          const list = [{id: "pixel", label: "Pixel"}, {id: "ipad", label: "iPad"}];
          // remembered says one thing, the person has already clicked another
          r.kept = routeChoices(list, "ipad", "remote:pixel", "").checked;
          // …unless what they clicked has just been forgotten elsewhere
          r.vanished = routeChoices([{id: "ipad", label: "iPad"}], "ipad",
                                    "remote:pixel", "").checked;
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["kept"], "remote:pixel")
        self.assertEqual(out["vanished"], "remote:ipad")

    def test_the_device_in_use_is_not_offered_for_forgetting(self):
        out = self._run("route_held", self._helpers("routeChoices") + """
          const list = [{id: "pixel", label: "Pixel"}, {id: "ipad", label: "iPad"}];
          console.log(JSON.stringify(routeChoices(list, "", "", "remote:pixel")));
        """)
        rows = out["rows"]
        self.assertEqual(rows[1]["forget"], "held")
        self.assertEqual(rows[2]["forget"], "ipad")

    def test_a_redraw_happens_only_when_something_actually_changed(self):
        out = self._run("route_signature",
                        self._helpers("routeSignature") + """
          const r = {};
          const a = [{id: "pixel", label: "Pixel"}];
          r.same = routeSignature(a, "") === routeSignature([{id: "pixel", label: "Pixel"}], "");
          r.relabelled = routeSignature(a, "") === routeSignature([{id: "pixel", label: "P"}], "");
          r.added = routeSignature(a, "") ===
                    routeSignature([{id: "pixel", label: "Pixel"}, {id: "x", label: "X"}], "");
          r.in_use = routeSignature(a, "") === routeSignature(a, "remote:pixel");
          console.log(JSON.stringify(r));
        """)
        self.assertTrue(out["same"], "an unchanged list must not redraw")
        self.assertFalse(out["relabelled"])
        self.assertFalse(out["added"])
        self.assertFalse(out["in_use"])

    def test_the_text_field_is_gone(self):
        """Phase A asked for the device by name and two people had to spell the
        same short word the same way. There is a list now."""
        self.assertNotIn('id="pick-device"', self.page)
        self.assertNotIn("window.prompt", routes_mod._VOICE_PAGE())


@unittest.skipUnless(NODE, "node not installed — voice-page node tests skipped")
class ThePairingPagesGuess(_NodeCase):

    def setUp(self):
        self.page = routes_mod._PAIR_PAGE()

    def test_a_device_is_named_from_what_its_browser_says_it_is(self):
        out = self._run("guess_label",
                        self._cut(self.page, PAIR["guessLabel"], "guessLabel") + """
          const r = {};
          r.pixel = guessLabel("Mozilla/5.0 (Linux; Android 16; Pixel 9) Chrome/1");
          r.iphone = guessLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)");
          r.ipad = guessLabel("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)");
          r.android = guessLabel("Mozilla/5.0 (Linux; Android 14; SM-S911B)");
          r.mac = guessLabel("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)");
          r.windows = guessLabel("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
          r.nothing = guessLabel("");
          r.missing = guessLabel(null);
          console.log(JSON.stringify(r));
        """)
        self.assertEqual(out["pixel"], "Pixel", "a Pixel says Android too")
        self.assertEqual(out["iphone"], "iPhone", "an iPhone says Mac OS X too")
        self.assertEqual(out["ipad"], "iPad")
        self.assertEqual(out["android"], "Android")
        self.assertEqual(out["mac"], "Mac")
        self.assertEqual(out["windows"], "Windows")
        self.assertEqual(out["nothing"], "phone")
        self.assertEqual(out["missing"], "phone")

    def test_the_page_sends_the_label_and_keeps_the_id_it_gets_back(self):
        """The two halves the re-pair path needs: what this device is called
        goes up, and the name it is known by afterwards is kept beside the key."""
        self.assertIn('JSON.stringify({ code, label, device_id: deviceId })', self.page)
        self.assertIn('localStorage.setItem(DEVICE_KEY, data.device_id)', self.page)


if __name__ == "__main__":
    unittest.main()
