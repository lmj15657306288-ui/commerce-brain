import unittest

from shadow_action_card import build_shadow_action_card


class ShadowActionCardTests(unittest.TestCase):
    def test_card_exposes_operator_fields_without_execution_controls(self):
        card = build_shadow_action_card({
            "shadow_id": "shadow-1",
            "snapshot": {"metrics": {"roi": 0.8, "orders": None, "secret": "omit"}},
            "decision_proposal": {
                "action": "CHECK_CAMPAIGN",
                "reason_code": "ROI_BELOW_REFERENCE",
                "evidence_hash": "a" * 64,
            },
        })
        self.assertEqual("检查投放", card["recommendation"])
        self.assertEqual("high", card["risk_level"])
        self.assertIsNone(card["metrics"]["orders"])
        self.assertNotIn("secret", card["metrics"])
        self.assertFalse(card["can_execute"])
        self.assertFalse(card["platform_write_attempted"])


if __name__ == "__main__":
    unittest.main()
