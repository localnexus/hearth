"""close_path: finalize + compaction request run BEFORE the memory tail."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hearth.session import close_path  # noqa: E402


class _Store:
    session_id = "s1"


class _Seam:
    def __init__(self, log):
        self.log = log
        self.closed = False

    def on_session_end(self, messages, store=None):
        self.log.append("seam")
        return "record kept (s1)"

    def close(self):
        self.closed = True


class TestClosePath(unittest.TestCase):
    def test_finalize_and_request_precede_the_memory_tail(self):
        log = []
        seam = _Seam(log)
        lines = close_path.run_close(
            _Store(), seam, [{"role": "user", "content": "x"}], live_tokens=45000,
            finalize=lambda st, m: log.append("finalize") or "held session kept",
            request=lambda st, live_tokens=None: log.append(f"request:{live_tokens}") or
            "auto-compaction requested",
            emit=lambda _l: None)
        self.assertEqual(log, ["finalize", "request:45000", "seam"])
        self.assertEqual([l.split("]")[0] for l in lines], ["[session", "[session", "[memory"])
        self.assertTrue(seam.closed)

    def test_memory_failure_still_closes_the_seam_after_finalize_ran(self):
        log = []

        class _Boom(_Seam):
            def on_session_end(self, messages, store=None):
                raise RuntimeError("index down")
        seam = _Boom(log)
        with self.assertRaises(RuntimeError):
            close_path.run_close(_Store(), seam, [], finalize=lambda st, m: log.append("finalize") or "ok",
                                 request=lambda st, live_tokens=None: None, emit=lambda _l: None)
        self.assertEqual(log, ["finalize"])
        self.assertTrue(seam.closed)

    def test_finalize_failure_skips_the_request_but_not_memory(self):
        log = []

        def bad_finalize(st, m):
            raise OSError("disk")
        lines = close_path.run_close(_Store(), _Seam(log), [], finalize=bad_finalize,
                                     request=lambda st, live_tokens=None: log.append("request") or "x",
                                     emit=lambda _l: None)
        self.assertEqual(log, ["seam"])
        self.assertEqual(len(lines), 1)

    def test_no_store_no_seam_is_a_quiet_noop(self):
        self.assertEqual(close_path.run_close(None, None, [], finalize=None, request=None,
                                              emit=lambda _l: None), [])


if __name__ == "__main__":
    unittest.main()
