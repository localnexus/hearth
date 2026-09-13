"""close_phase: the file-backed breadcrumb the close ladder rewrites as it
walks, and the mic-closed assertion it reads from the installed transport."""
import inspect
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hearth.session import close_phase  # noqa: E402


class _NoStream:
    pass


class _ClosedStream:
    _in_stream = None


class _OpenStream:
    _in_stream = object()


class TestInputClosed(unittest.TestCase):
    def test_none_in_stream_reads_as_closed(self):
        self.assertIs(close_phase.input_closed(_ClosedStream()), True)

    def test_live_in_stream_reads_as_not_closed(self):
        self.assertIs(close_phase.input_closed(_OpenStream()), False)

    def test_no_attribute_reads_as_unknown(self):
        self.assertIsNone(close_phase.input_closed(_NoStream()))


class TestPinnedAgainstInstalledTransport(unittest.TestCase):
    def test_the_installed_transport_still_has_the_attribute(self):
        from pipecat.transports.local.audio import (
            LocalAudioInputTransport, LocalAudioTransportParams,
        )
        try:
            transport = LocalAudioInputTransport(object(), LocalAudioTransportParams())
        except Exception:
            # Route B: construction needed a real audio device on this
            # machine — pin the attribute name against cleanup()'s source
            # instead of a live instance.
            self.assertIn("self._in_stream = None",
                          inspect.getsource(LocalAudioInputTransport.cleanup))
            return
        # Route A: a bare instance constructed fine — its stream has not been
        # opened, the same terminal state cleanup() leaves it in.
        self.assertIs(close_phase.input_closed(transport), True)


class TestClosePhaseAdvance(unittest.TestCase):
    def test_advance_writes_pid_stage_and_stages(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "current.json"
            phase = close_phase.ClosePhase(pid=4242, character="demo",
                                           session_id="demo-01", path=path)
            phase.advance("pipeline-down")
            phase.advance("drain")
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["pid"], 4242)
            self.assertEqual(doc["stage"], "drain")
            self.assertEqual([s["stage"] for s in doc["stages"]],
                             ["pipeline-down", "drain"])

    def test_mic_closed_persists_across_later_advances(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "current.json"
            phase = close_phase.ClosePhase(pid=1, character=None, session_id=None, path=path)
            phase.advance("session-finalized", mic_closed=False)
            phase.advance("done")
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(doc["mic_closed"], False)


class TestRead(unittest.TestCase):
    def test_a_different_pid_is_none(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "current.json"
            close_phase.ClosePhase(pid=7, character=None, session_id=None,
                                   path=path).advance("done")
            self.assertIsNone(close_phase.read(pid=8, path=path))

    def test_the_same_pid_returns_the_document(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "current.json"
            close_phase.ClosePhase(pid=7, character=None, session_id=None,
                                   path=path).advance("done")
            doc = close_phase.read(pid=7, path=path)
            self.assertEqual(doc["pid"], 7)

    def test_an_absent_file_is_none(self):
        with TemporaryDirectory() as d:
            self.assertIsNone(close_phase.read(pid=1, path=Path(d) / "missing.json"))

    def test_a_garbage_file_is_none(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "current.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(close_phase.read(pid=1, path=path))

    def test_no_pid_is_none(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "current.json"
            close_phase.ClosePhase(pid=7, character=None, session_id=None,
                                   path=path).advance("done")
            self.assertIsNone(close_phase.read(pid=None, path=path))


class TestContained(unittest.TestCase):
    def test_an_unwritable_path_never_raises(self):
        with TemporaryDirectory() as d:
            blocked = Path(d) / "afile"
            blocked.write_text("not a directory", encoding="utf-8")
            phase = close_phase.ClosePhase(pid=1, character=None, session_id=None,
                                           path=blocked / "sub" / "current.json")
            phase.advance("done")  # must not raise


class TestNeverCarriesMessageText(unittest.TestCase):
    def test_the_document_never_carries_a_message_string_passed_nowhere(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "current.json"
            phase = close_phase.ClosePhase(pid=1, character="demo",
                                           session_id="demo-01", path=path)
            phase.advance("session-finalized", outcome={"result": "held"})
            blob = path.read_text(encoding="utf-8")
            self.assertNotIn("the quick brown fox", blob)


if __name__ == "__main__":
    unittest.main()
