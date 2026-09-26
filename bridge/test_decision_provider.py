import unittest

from commerce_contracts import SUPPORTED_ACTIONS
from decision_provider import (
    DecisionProviderError,
    LayaProvider,
    RulesProvider,
    validate_decision_result,
    decide_live_state,
)
from laya_client import LayaClientError
from live_state import FixtureLiveStateAdapter, build_live_state_snapshot


class StubLayaClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.last_latency_ms = 17
        self.timeout_seconds = 3.0
        self.calls = []

    def systemone(self, **kwargs):
        self.calls.append({**kwargs, "timeout_seconds": self.timeout_seconds})
        if self.error:
            raise self.error
        return self.response


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
        result = LayaProvider(
            client=StubLayaClient(error=LayaClientError("UNAVAILABLE", "offline"))
        ).decide(self.state(inventory_days=2))
        self.assertEqual("rules", result["provider"])
        self.assertEqual("fallback_from_laya", result["checkpoint"])
        self.assertEqual("CHECK_PRODUCT", result["choice"])

    def test_output_validation_rejects_executable_fields(self):
        valid = RulesProvider().decide(self.state())
        with self.assertRaises(DecisionProviderError):
            validate_decision_result({**valid, "shell": "echo unsafe"})
        with self.assertRaises(DecisionProviderError):
            validate_decision_result({**valid, "choice": "ADJUST_BUDGET"})

    def test_one_hundred_fixture_decisions_are_stable_and_non_crashing(self):
        provider = LayaProvider(
            client=StubLayaClient(error=LayaClientError("UNAVAILABLE", "offline"))
        )
        choices = []
        for index in range(100):
            result = provider.decide(self.state(roi=0.8 if index % 2 else 1.2))
            choices.append(result["choice"])
            self.assertIn(result["choice"], SUPPORTED_ACTIONS)
            self.assertGreaterEqual(result["confidence"], 0)
            self.assertLessEqual(result["confidence"], 1)
        self.assertEqual(100, len(choices))

    def test_laya_choice_is_mapped_to_closed_result(self):
        client = StubLayaClient(
            response={
                "model": "laya-rl-agent",
                "routing": {"model": "typed-decisions"},
                "answers": {
                    "next_action": {
                        "type": "choice",
                        "choice": "ESCALATE_SLOW_BRAIN",
                        "confidence": 0.8,
                        "answer_confidence": 0.9,
                    },
                    "actionable": {"type": "noul", "noul": 0.8},
                    "priority": {"type": "score", "score": 3},
                },
            }
        )
        result = LayaProvider(client=client, timeout_ms=250).decide(self.state())
        self.assertEqual("ESCALATE_SLOW_BRAIN", result["choice"])
        self.assertEqual("laya", result["provider"])
        self.assertEqual("typed-decisions", result["checkpoint"])
        self.assertTrue(result["binary"])
        self.assertEqual(1.0, result["score"])
        self.assertEqual(17, result["latency_ms"])
        self.assertEqual(
            {
                "OBSERVE",
                "PROMPT_HOST",
                "CHECK_PRODUCT",
                "CHECK_CAMPAIGN",
                "ESCALATE_SLOW_BRAIN",
            },
            set(client.calls[0]["questions"]["next_action"]["criteria"]),
        )
        self.assertEqual(0.25, client.calls[0]["timeout_seconds"])

    def test_laya_invalid_action_and_low_confidence_fallback(self):
        invalid = {
            "model": "laya-rl-agent",
            "answers": {
                "next_action": {
                    "type": "choice",
                    "choice": "SET_BUDGET",
                    "confidence": 0.9,
                    "answer_confidence": 0.9,
                }
            },
        }
        result = LayaProvider(client=StubLayaClient(response=invalid)).decide(self.state())
        self.assertEqual("rules", result["provider"])
        self.assertEqual("fallback_from_laya", result["checkpoint"])
        low = {
            "model": "laya-rl-agent",
            "answers": {
                "next_action": {
                    "type": "choice",
                    "choice": "OBSERVE",
                    "confidence": 0.1,
                    "answer_confidence": 0.1,
                }
            },
        }
        result = LayaProvider(client=StubLayaClient(response=low)).decide(self.state())
        self.assertEqual("rules", result["provider"])
        self.assertEqual("fallback_from_laya", result["checkpoint"])

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
