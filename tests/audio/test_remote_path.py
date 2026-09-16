"""The hello's three questions, answered without a network.

Who is on the socket, how far away, and how deep the buffer must be for them —
`audio/remote_path.py`. Every answer here is a pure function of a peer address
and a canned `tailscale status --json` document, which is why it can be pinned
in a unit test rather than found out during a conversation.

The classification cases come from Norma's measurements of 2026-09-15: a peer
with `CurAddr` populated is going straight there (LAN 14 ms, hotspot ~88 ms);
one without is going through a relay, where the median barely moves but the
tail roughly doubles and the buffer requirement went to 141–188 ms.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hearth.audio import remote_path

# A status document in the shape tailscale actually emits, with the three
# cases side by side: a direct peer, a relayed one, and this machine.
STATUS = {
    "Self": {"DNSName": "desk.example.ts.net.", "TailscaleIPs": ["100.64.0.1"],
             "CurAddr": ""},
    "Peer": {
        "key-direct": {"HostName": "phone", "TailscaleIPs": ["100.64.0.2"],
                       "CurAddr": "69.238.59.222:1312", "Relay": "sfo"},
        "key-relayed": {"HostName": "other", "TailscaleIPs": ["100.64.0.3"],
                        "CurAddr": "", "Relay": "sfo"},
        "key-nameless": {"HostName": "broken"},
    },
}


class TheHello(unittest.TestCase):

    def test_a_well_formed_hello_is_read_out_of_the_frame(self):
        hello = remote_path.parse_hello('{"hello": {"device": "p", "token": "t"}}')
        self.assertEqual(hello, {"device": "p", "token": "t"})

    def test_anything_that_is_not_a_hello_answers_none(self):
        for frame in (b"\x00\x01audio", bytearray(b"\x00"), "not json",
                      "[1,2,3]", '"a string"', "{}", '{"hello": "words"}',
                      '{"hello": null}', "", None):
            with self.subTest(frame=frame):
                self.assertIsNone(remote_path.parse_hello(frame))

    def test_the_sittings_own_device_with_the_key_is_accepted(self):
        self.assertTrue(remote_path.hello_accepted(
            {"device": "pixel", "token": "secret"},
            device_id="pixel", token="secret"))

    def test_a_wrong_key_a_wrong_device_and_a_missing_half_are_all_refused(self):
        cases = (
            ({"device": "pixel", "token": "wrong"}, "wrong key"),
            ({"device": "laptop", "token": "secret"}, "wrong device"),
            ({"device": "pixel"}, "no key at all"),
            ({"token": "secret"}, "no device at all"),
            ({"device": "pixel", "token": 7}, "a key that is not a string"),
            ({"device": None, "token": "secret"}, "a null device"),
            ({}, "an empty hello"),
            (None, "no hello at all"),
        )
        for hello, why in cases:
            with self.subTest(why=why):
                self.assertFalse(remote_path.hello_accepted(
                    hello, device_id="pixel", token="secret"))

    def test_a_prefix_of_the_key_is_not_the_key(self):
        """The compare is constant time AND whole — no startswith anywhere."""
        self.assertFalse(remote_path.hello_accepted(
            {"device": "pixel", "token": "sec"}, device_id="pixel", token="secret"))
        self.assertFalse(remote_path.hello_accepted(
            {"device": "pixel", "token": "secretly"},
            device_id="pixel", token="secret"))


class TheKeyOnDisk(unittest.TestCase):

    def test_an_absent_or_empty_key_file_answers_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            config.mkdir()
            with mock.patch.object(remote_path, "serve_token_path",
                                   return_value=config / "serve-token"):
                self.assertIsNone(remote_path.read_serve_token())
                (config / "serve-token").write_text("   \n", encoding="utf-8")
                self.assertIsNone(remote_path.read_serve_token())
                (config / "serve-token").write_text("a-key\n", encoding="utf-8")
                self.assertEqual(remote_path.read_serve_token(), "a-key")


class ThePeerAddress(unittest.TestCase):
    """The socket arrives through a proxy, so the loopback side is never the
    peer — the first hop in X-Forwarded-For is."""

    def test_the_forwarded_header_wins_over_the_proxys_own_address(self):
        self.assertEqual(
            remote_path.peer_address({"X-Forwarded-For": "100.64.0.2"},
                                     ("127.0.0.1", 51234)),
            "100.64.0.2")

    def test_only_the_first_hop_of_a_forwarded_chain_is_taken(self):
        self.assertEqual(
            remote_path.peer_address(
                {"X-Forwarded-For": "100.64.0.2, 10.0.0.9, 127.0.0.1"}, None),
            "100.64.0.2")

    def test_without_the_header_the_socket_address_is_used(self):
        self.assertEqual(remote_path.peer_address({}, ("100.64.0.2", 1)), "100.64.0.2")
        self.assertEqual(remote_path.peer_address(None, "100.64.0.2"), "100.64.0.2")

    def test_nothing_to_go_on_answers_none(self):
        self.assertIsNone(remote_path.peer_address({}, None))
        self.assertIsNone(remote_path.peer_address({"X-Forwarded-For": "  "}, None))


class TheClassification(unittest.TestCase):

    def test_a_peer_with_a_current_address_is_direct(self):
        self.assertEqual(remote_path.classify("100.64.0.2", STATUS), "direct")

    def test_a_peer_without_one_is_relayed(self):
        self.assertEqual(remote_path.classify("100.64.0.3", STATUS), "relayed")

    def test_a_peer_we_cannot_place_is_unknown_and_never_guessed_direct(self):
        """The expensive mistake is a shallow buffer on a long path, so an
        address that is not in the document buys the deeper one."""
        for address, doc in (("100.64.0.99", STATUS), ("100.64.0.2", None),
                             ("100.64.0.2", {}), (None, STATUS),
                             ("100.64.0.2", {"Peer": "not a map"})):
            with self.subTest(address=address):
                self.assertEqual(remote_path.classify(address, doc), "unknown")

    def test_a_malformed_peer_entry_does_not_stop_the_search(self):
        self.assertEqual(remote_path.classify("100.64.0.2", STATUS), "direct")


class TheBufferDepth(unittest.TestCase):

    def setUp(self):
        for name in ("HEARTH_AUDIO_BUFFER_DIRECT_MS", "HEARTH_AUDIO_BUFFER_RELAYED_MS"):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def test_the_measured_leans_are_the_defaults(self):
        self.assertEqual(remote_path.buffer_ms("direct"), 60)
        self.assertEqual(remote_path.buffer_ms("relayed"), 200)

    def test_an_unknown_path_is_charged_the_relayed_depth(self):
        self.assertEqual(remote_path.buffer_ms("unknown"), 200)

    def test_the_environment_overrides_each_depth(self):
        os.environ["HEARTH_AUDIO_BUFFER_DIRECT_MS"] = "40"
        os.environ["HEARTH_AUDIO_BUFFER_RELAYED_MS"] = "300"
        self.assertEqual(remote_path.buffer_ms("direct"), 40)
        self.assertEqual(remote_path.buffer_ms("relayed"), 300)

    def test_a_nonsense_override_falls_back_rather_than_failing_a_sitting(self):
        for bad in ("", "  ", "soon", "0", "-5"):
            with self.subTest(bad=bad):
                os.environ["HEARTH_AUDIO_BUFFER_DIRECT_MS"] = bad
                self.assertEqual(remote_path.buffer_ms("direct"), 60)

    def test_describe_pairs_the_word_with_the_depth(self):
        self.assertEqual(remote_path.describe("100.64.0.2", STATUS), ("direct", 60))
        self.assertEqual(remote_path.describe("100.64.0.3", STATUS), ("relayed", 200))
        self.assertEqual(remote_path.describe("100.64.0.9", STATUS), ("unknown", 200))


class TheOriginsAreLookedUpNotWrittenDown(unittest.TestCase):

    def test_the_machines_own_names_become_origins_under_both_schemes(self):
        origins = remote_path.facade_origins(STATUS)
        self.assertIn("https://desk.example.ts.net:65001", origins)
        self.assertIn("http://desk.example.ts.net:65001", origins)
        self.assertTrue(all(o.endswith(":65001") for o in origins), origins)

    def test_the_trailing_dot_of_a_dns_name_is_dropped(self):
        self.assertNotIn(".:", " ".join(remote_path.facade_origins(STATUS)))

    def test_the_page_port_follows_the_environment(self):
        with mock.patch.dict(os.environ, {"HEARTH_FACADE_PORT": "65011"}):
            self.assertEqual(remote_path.facade_port(), 65011)
            self.assertIn("https://desk.example.ts.net:65011",
                          remote_path.facade_origins(STATUS))
        with mock.patch.dict(os.environ, {"HEARTH_FACADE_PORT": "nope"}):
            self.assertEqual(remote_path.facade_port(), 65001)


class TheTailscaleLookupIsBounded(unittest.TestCase):

    def test_no_binary_at_all_answers_none_rather_than_raising(self):
        with mock.patch.object(remote_path, "tailscale_binary", return_value=None):
            self.assertIsNone(remote_path.tailscale_status())

    def test_a_failing_or_unreadable_command_answers_none(self):
        class _Proc:
            def __init__(self, code, out):
                self.returncode, self.stdout = code, out

        for proc in (_Proc(1, b""), _Proc(0, b"not json"), _Proc(0, b"[1,2]")):
            with self.subTest(returncode=proc.returncode):
                with mock.patch.object(remote_path, "tailscale_binary",
                                       return_value="/bin/true"), \
                     mock.patch.object(remote_path.subprocess, "run",
                                       return_value=proc):
                    self.assertIsNone(remote_path.tailscale_status())

    def test_a_timeout_is_an_unknown_path_not_a_failed_sitting(self):
        import subprocess as sp

        with mock.patch.object(remote_path, "tailscale_binary",
                               return_value="/bin/true"), \
             mock.patch.object(remote_path.subprocess, "run",
                               side_effect=sp.TimeoutExpired("tailscale", 3)):
            self.assertIsNone(remote_path.tailscale_status())
            self.assertEqual(remote_path.classify("100.64.0.2", None), "unknown")


if __name__ == "__main__":
    unittest.main()
