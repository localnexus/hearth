"""S3: the supervisor's SIGINT grace follows [memory] close_budget_s."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from hearth.supervisor.routes import stop_grace_for  # noqa: E402
from hearth.supervisor.child import STOP_GRACE_S  # noqa: E402


class TestStopGrace(unittest.TestCase):
    def test_explicit_supervisor_value_wins(self):
        self.assertEqual(stop_grace_for({"stop_grace_s": 7}, {"close_budget_s": 500}), 7.0)

    def test_follows_memory_budget_plus_base(self):
        self.assertEqual(stop_grace_for({}, {"close_budget_s": 120}), STOP_GRACE_S + 120)

    def test_memory_off_is_the_base_and_default_budget_applies_when_on(self):
        self.assertEqual(stop_grace_for({}, None), STOP_GRACE_S)  # memory disabled/absent
        self.assertEqual(stop_grace_for({}, {"enabled": True}), STOP_GRACE_S + 120)  # on, no key

    def test_bad_budget_falls_back_to_base(self):
        self.assertEqual(stop_grace_for({}, {"close_budget_s": "lots"}), STOP_GRACE_S)
