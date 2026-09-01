"""test_token_meter.py — the runway gauge's open-time seed + completion fold (2026-09-01).

Proves the fix for the panel's pre-fill dead zone: after a resume or live switch
the server reports nothing until turn 1, so the gauge used to claim 0 held.

  1. prime_estimate  — chars/4 over instruction + messages (string and
     multimodal-parts content), snapshot flagged estimated=True
  2. ground truth    — the first real usage report CLEARS the estimate;
     held becomes prompt + completion (the reply sits in context too)
  3. trailing fold   — net_turn_growth still diffs consecutive prompts
  4. unprimed        — a fresh meter is byte-compatible with the old shape
     (held 0, estimated False)
  5. re-prime        — a live switch can re-seed after reports (estimated again)

Real pipecat MetricsFrame/LLMUsageMetricsData objects drive on_push_frame — the
observer path is exercised, not simulated. Run: ./.venv/bin/python tests/test_token_meter.py
"""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pipecat.frames.frames import MetricsFrame  # noqa: E402
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData  # noqa: E402

from hearth.session.token_meter import TokenMeter  # noqa: E402


def _push(meter: TokenMeter, prompt: int, completion: int) -> None:
    usage = LLMTokenUsage(prompt_tokens=prompt, completion_tokens=completion,
                          total_tokens=prompt + completion)
    frame = MetricsFrame(data=[LLMUsageMetricsData(processor="llm", value=usage)])
    asyncio.run(meter.on_push_frame(SimpleNamespace(frame=frame)))


class TestPrimeEstimate(unittest.TestCase):
    def test_estimate_counts_instruction_and_messages(self):
        m = TokenMeter()
        m.prime_estimate("x" * 400, [
            {"role": "user", "content": "y" * 100},
            {"role": "assistant", "content": [{"type": "text", "text": "z" * 100},
                                              {"type": "image_url", "image_url": {}}]},
        ])
        snap = m.snapshot()
        self.assertEqual(snap["held_in_ctx"], 600 // 4)
        self.assertTrue(snap["estimated"])

    def test_estimate_tolerates_empty_and_none(self):
        m = TokenMeter()
        m.prime_estimate("", None)
        snap = m.snapshot()
        self.assertEqual(snap["held_in_ctx"], 0)
        self.assertTrue(snap["estimated"])  # primed-empty is still "no report yet"

    def test_unprimed_meter_keeps_old_shape(self):
        snap = TokenMeter().snapshot()
        self.assertEqual(snap["held_in_ctx"], 0)
        self.assertFalse(snap["estimated"])


class TestGroundTruthReplacesEstimate(unittest.TestCase):
    def test_first_report_clears_estimate_and_folds_completion(self):
        m = TokenMeter()
        m.prime_estimate("x" * 40000, [])   # ~10k est
        _push(m, prompt=100, completion=20)
        snap = m.snapshot()
        self.assertFalse(snap["estimated"])
        self.assertEqual(snap["held_in_ctx"], 120)  # prompt + its reply
        self.assertEqual(snap["turns"], 1)

    def test_net_turn_growth_still_diffs_prompts(self):
        m = TokenMeter()
        _push(m, prompt=100, completion=20)
        _push(m, prompt=150, completion=30)
        snap = m.snapshot()
        self.assertEqual(snap["held_in_ctx"], 180)
        self.assertEqual(snap["net_turn_growth"], 50)  # prompt Δ, completion-free

    def test_reprime_after_reports_estimates_again(self):
        m = TokenMeter()
        _push(m, prompt=100, completion=20)
        m.prime_estimate("x" * 4000, [])    # live switch: new companion's pre-fill
        snap = m.snapshot()
        self.assertTrue(snap["estimated"])
        self.assertEqual(snap["held_in_ctx"], 1000)
        _push(m, prompt=900, completion=10)
        snap = m.snapshot()
        self.assertFalse(snap["estimated"])
        self.assertEqual(snap["held_in_ctx"], 910)


if __name__ == "__main__":
    unittest.main(verbosity=2)
