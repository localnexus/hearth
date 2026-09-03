"""Hindsight backend — recall shaping.

The recent-session boost (newest valid facts appended past semantic rank) and
the fact-count surface the panel gauge reads.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))




class _NSText:
    def __init__(self, text):  # noqa: ANN001
        self.text = text


class _BoostClient:
    """recall + list_memories double for the recent-boost path (no SDK here)."""

    def __init__(self, results, recent, fail_recent=False):  # noqa: ANN001
        self._results, self._recent, self._fail = results, recent, fail_recent

    def recall(self, bank_id, query):  # noqa: ANN001, ARG002
        r = type("R", (), {})()
        r.results = self._results
        return r

    def list_memories(self, bank_id, limit, offset=0):  # noqa: ANN001, ARG002
        if self._fail:
            raise RuntimeError("boom")
        r = type("R", (), {})()
        r.items = self._recent[:limit]
        return r

    def close(self):
        pass


class TestHindsightRecentBoost(unittest.TestCase):
    """The last-session slot (finding 2026-09-01): newest valid facts append
    past semantic rank — deduped, invalid-filtered, capped, contained."""

    def _backend(self, client, boost=2):  # noqa: ANN001
        from hearth.memory.backend_hindsight import HindsightBackend
        b = HindsightBackend({"mode": "sidecar", "llm_model": "m", "recent_boost": boost})
        b._ensure = lambda: None
        b._client = client
        return b

    def test_append_dedupe_invalid_filter_and_cap(self):
        sem = [_NSText("old fact A"), _NSText("old fact B")]
        recent = [
            {"text": "old fact A", "state": "valid", "date": "2026-09-01T10:00:00+00:00"},
            {"text": "fresh fact", "state": "valid", "date": "2026-09-01T10:22:31+00:00"},
            {"text": "retracted", "state": "invalidated", "date": "2026-09-01T09:00:00+00:00"},
            {"text": "second fresh", "state": "valid", "date": "2026-08-31T23:00:00+00:00"},
            {"text": "third fresh", "state": "valid", "date": "2026-08-30T01:00:00+00:00"},
        ]
        items = self._backend(_BoostClient(sem, recent)).recall("c", "q", 6)
        self.assertEqual([i.text for i in items],
                         ["old fact A", "old fact B", "fresh fact", "second fresh"])
        self.assertEqual(items[2].when, "2026-09-01")
        self.assertEqual(items[2].source_session, "hindsight/c/recent")

    def test_boost_failure_costs_only_the_boost(self):
        b = self._backend(_BoostClient([_NSText("only")], [], fail_recent=True))
        self.assertEqual([i.text for i in b.recall("c", "q", 6)], ["only"])

    def test_boost_zero_never_calls_list_memories(self):
        calls = []

        class _C(_BoostClient):
            def list_memories(self, **kw):  # noqa: ANN003
                calls.append(1)
                raise AssertionError("recent_boost=0 must not list")

        items = self._backend(_C([_NSText("x")], []), boost=0).recall("c", "q", 6)
        self.assertEqual([i.text for i in items], ["x"])
        self.assertEqual(calls, [])


class TestHindsightFactCount(unittest.TestCase):
    """fact_count — the curation pane's lazy gauge: valid-only, one bounded
    page, capped honestly instead of paging."""

    def _backend(self, memories):  # noqa: ANN001
        from hearth.memory.backend_hindsight import HindsightBackend

        b = HindsightBackend({"mode": "sidecar", "llm_model": "m"})
        b._ensure = lambda: None
        b._client = _BoostClient([], memories)
        return b

    def test_counts_valid_only(self):
        memories = [{"text": "a", "state": "valid"},
                    {"text": "b", "state": "invalidated"},
                    {"text": "c"}]  # absent state reads as valid
        self.assertEqual(self._backend(memories).fact_count("c"),
                         {"facts": 2, "capped": False})

    def test_capped_at_one_page(self):
        from hearth.memory import backend_hindsight as bh

        memories = [{"text": str(i), "state": "valid"}
                    for i in range(bh._FACT_COUNT_LIMIT + 5)]
        out = self._backend(memories).fact_count("c")
        self.assertEqual(out, {"facts": bh._FACT_COUNT_LIMIT, "capped": True})

if __name__ == "__main__":
    unittest.main()
