"""test_weights_fit.py — the fit arithmetic, pinned on the incumbent's numbers.

Pure functions, no filesystem. Pins:

  1. KV at 262 144 on a hybrid build (41 blocks, 1 of them the MTP layer,
     full_attention_interval 4 ⇒ 10 attention layers, 2 KV heads, key/value
     256, f16) is EXACTLY 5 368 709 120 bytes. The same header with the
     interval absent is 40 layers and exactly 21 474 836 480 — the 4x that a
     formula ignoring the interval gets wrong, and the reason context is cheap
     on this family;
  2. estimate() sums weights + projector + KV + a 1 GiB margin;
  3. verdict() steps the context down by halving and stops at 4096;
  4. `llama-server --list-devices` output parses to the Metal working set.

Run:  .venv/bin/python -m unittest tests.test_weights_fit
"""

from __future__ import annotations

import unittest

from hearth.weights import fit
from hearth.weights.header import HeaderFacts

REFERENCE = HeaderFacts(architecture="qwen35moe", name="reference", block_count=41,
                      context_length=262144, head_count=16, head_count_kv=2,
                      key_length=256, value_length=256, full_attention_interval=4,
                      expert_count=256, nextn_predict_layers=1, file_type=7,
                      tensor_bytes=37_791_158_784)

DEVICE_LINE = "MTL0: Apple M3 Ultra (475136 MiB, 475135 MiB free)"


class _Cand:
    """The two attributes estimate() reads off a Candidate."""
    def __init__(self, size_bytes, header=None):
        self.size_bytes = size_bytes
        self.header = header


class KvCache(unittest.TestCase):

    def test_hybrid_attention_interval_is_read(self):
        self.assertEqual(fit.attention_layers(REFERENCE), 10)
        self.assertEqual(fit.kv_bytes(REFERENCE, 262_144), 5_368_709_120)

    def test_without_the_interval_every_block_caches(self):
        dense = HeaderFacts(**{**REFERENCE.as_dict(), "full_attention_interval": None})
        self.assertEqual(fit.attention_layers(dense), 40)
        self.assertEqual(fit.kv_bytes(dense, 262_144), 21_474_836_480)

    def test_quantised_cache_halves_it(self):
        self.assertEqual(fit.kv_bytes(REFERENCE, 262_144, cache_bytes=1),
                         5_368_709_120 // 2)

    def test_a_header_that_says_nothing_costs_nothing(self):
        self.assertEqual(fit.kv_bytes(HeaderFacts(), 262_144), 0)


class Estimating(unittest.TestCase):

    def test_the_four_terms(self):
        cand = _Cand(37_802_153_120, REFERENCE)
        proj = _Cand(900_000_000)
        est = fit.estimate(cand, 262_144, proj)
        self.assertEqual(est.weights_bytes, REFERENCE.tensor_bytes)
        self.assertEqual(est.mmproj_bytes, 900_000_000)
        self.assertEqual(est.kv_bytes, 5_368_709_120)
        self.assertEqual(est.margin_bytes, fit.MARGIN_BYTES)
        self.assertEqual(est.total, est.weights_bytes + est.mmproj_bytes
                         + est.kv_bytes + est.margin_bytes)
        self.assertEqual(est.ctx, 262_144)

    def test_no_header_falls_back_to_the_file_size(self):
        est = fit.estimate(_Cand(1_000), 4096)
        self.assertEqual(est.weights_bytes, 1_000)
        self.assertEqual(est.kv_bytes, 0)


class Verdicts(unittest.TestCase):

    def _budget(self, available):
        return fit.Budget(total_bytes=available, working_set_bytes=available,
                          reserve_bytes=0, available=available, source="test")

    def test_fits(self):
        est = fit.estimate(_Cand(0, REFERENCE), 262_144)
        self.assertEqual(fit.verdict(est, self._budget(est.total), REFERENCE), "fits")

    def test_steps_down_by_halving(self):
        est = fit.estimate(_Cand(0, REFERENCE), 262_144)
        fixed = est.weights_bytes + est.margin_bytes
        room = fixed + fit.kv_bytes(REFERENCE, 65_536)
        self.assertEqual(fit.verdict(est, self._budget(room), REFERENCE),
                         "fits at ctx 65536")

    def test_too_large_when_even_the_floor_does_not_fit(self):
        est = fit.estimate(_Cand(0, REFERENCE), 262_144)
        self.assertEqual(fit.verdict(est, self._budget(est.weights_bytes // 2), REFERENCE),
                         "too large")

    def test_the_floor_is_the_doors_own_fit_ctx(self):
        est = fit.estimate(_Cand(0, REFERENCE), 262_144)
        fixed = est.weights_bytes + est.margin_bytes
        room = fixed + fit.kv_bytes(REFERENCE, fit.MIN_CTX)
        self.assertEqual(fit.verdict(est, self._budget(room), REFERENCE),
                         f"fits at ctx {fit.MIN_CTX}")


class DeviceLine(unittest.TestCase):

    def test_the_metal_working_set_is_read_from_the_doors_own_listing(self):
        text = "load_backend: loaded Metal backend\n" + DEVICE_LINE + "\n"
        self.assertEqual(fit.parse_devices(text), 475_136 * 1024 * 1024)

    def test_nothing_to_parse_is_zero_not_a_crash(self):
        self.assertEqual(fit.parse_devices(""), 0)


if __name__ == "__main__":
    unittest.main()
