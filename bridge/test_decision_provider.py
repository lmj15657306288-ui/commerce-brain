import unittest

from commerce_contracts import SUPPORTED_ACTIONS
from decision_provider import (
    DecisionProviderError,
    LayaProvider,
    RulesProvider,
    validate_decision_result,
    decide_live_state,
)
from live_state import FixtureLiveStateAdapter, build_live_state_snapshot


class DecisionProviderTests(unittest.TestCase):
    def state(self, **metrics):
        return {
            "snapshot_id": "fixture-snapshot",
            "metrics": {
                "roi": 1.2,
                "orders": 2,
                "inventory_days": 14,
                **metrics,
            },
        }

    def test_rules_provider_returns_closed_proposal_only(self):
        result = RulesProvider().decide(self.state(roi=0.8))
        self.assertEqual("CHECK_CAMPAIGN", result["choice"])
        self.assertIn(result["choice"], SUPPORTED_ACTIONS)
        self.assertEqual(64, len(result["state_hash"]))
        self.assertNotIn("shell", result)

    def test_missing_metrics_do_not_become_zero(self):
        result = RulesProvider().decide(self.state(orders=None, roi=None))
        self.assertEqual("OBSERVE", result["choice"])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["binary"])
        self.assertEqual("MISSING_METRIC", result["reason_code"])

    def test_laya_is_local_and_reports_checkpoint_boundary(self):
        result = LayaProvider().decide(self.state(inventory_days=2))
        self.assertEqual("laya", result["provider"])
        self.assertEqual("rules-fallback", result["checkpoint"])
        self.assertEqual("CHECK_PRODUCT", result["choice"])

    def test_output_validation_rejects_executable_fields(self):
        valid = RulesProvider().decide(self.state())
        with self.assertRaises(DecisionProviderError):
            validate_decision_result({**valid, "shell": "echo unsafe"})
        with self.assertRaises(DecisionProviderError):
            validate_decision_result({**valid, "choice": "ADJUST_BUDGET"})

    def test_one_hundred_fixture_decisions_are_stable_and_non_crashing(self):
        provider = LayaProvider()
        choices = []
        for index in range(100):
            result = provider.decide(self.state(roi=0.8 if index % 2 else 1.2))
            choices.append(result["choice"])
            self.assertIn(result["choice"], SUPPORTED_ACTIONS)
            self.assertGreaterEqual(result["confidence"], 0)
            self.assertLessEqual(result["confidence"], 1)
        self.assertEqual(100, len(choices))

    def test_live_rules_use_only_the_read_only_action_space(self):
        snapshot = build_live_state_snapshot(
            FixtureLiveStateAdapter([{
                "online_users": 100,
                "product_click_rate": 0.01,
                "leave_rate": 0.1,
                "roi": 1.2,
            }]),
            window_seconds=30,
        )
        self.assertEqual("CHECK_PRODUCT", decide_live_state(snapshot)["choice"])


if __name__ == "__main__":
    unittest.main()
