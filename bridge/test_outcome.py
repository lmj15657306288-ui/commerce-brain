import tempfile
import unittest
from pathlib import Path

from shadow_loop import ShadowDecisionStore


class OutcomeTests(unittest.TestCase):
    def test_outcome_is_linked_and_missing_delta_stays_null(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ShadowDecisionStore(Path(temp))
            store.append({
                "shadow_id": "shadow-outcome-1",
                "snapshot": {"metrics": {"roi": 1.0, "orders": None}},
                "decision_proposal": {"decision_id": "decision-1"},
                "human_action": {"action": "confirmed"},
            })
            result = store.add_outcome(
                "shadow-outcome-1",
                horizon="2m",
                metrics_after={"roi": 1.2, "orders": None},
                source="manual",
            )
            outcome = result["outcome"]
            self.assertEqual("decision-1", outcome["decision_id"])
            self.assertEqual("shadow-outcome-1", outcome["action_id"])
            self.assertEqual(0.2, outcome["delta"]["roi"])
            self.assertIsNone(outcome["delta"]["orders"])

    def test_outcome_horizon_and_source_are_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ShadowDecisionStore(Path(temp))
            store.append({"shadow_id": "shadow-outcome-2", "snapshot": {"metrics": {}}, "decision_proposal": {}})
            with self.assertRaises(ValueError):
                store.add_outcome("shadow-outcome-2", horizon="1h", metrics_after={}, source="manual")
            with self.assertRaises(ValueError):
                store.add_outcome("shadow-outcome-2", horizon="30s", metrics_after={}, source="platform")


if __name__ == "__main__":
    unittest.main()
