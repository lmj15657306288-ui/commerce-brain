import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from commerce_contracts import StateSnapshotV1
from decision_provider import RulesProvider
from shadow_loop import ShadowDecisionStore, run_shadow_decision


class ShadowLoopTests(unittest.TestCase):
    NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    def snapshot(self):
        return StateSnapshotV1.create(
            snapshot_id="snapshot-shadow-1",
            captured_at=self.NOW.isoformat(),
            source="fixture",
            source_quality="high",
            scope={"shop_id": "shop-1", "account_id": "account-1"},
            entity_ids={"campaign": ["campaign-1"]},
            metrics={"roi": 0.8, "orders": 1, "inventory_days": 10},
        )

    def test_shadow_loop_persists_a_non_executable_record(self):
        with tempfile.TemporaryDirectory() as temp:
            record = run_shadow_decision(
                self.snapshot(),
                RulesProvider(),
                data_dir=temp,
                now=self.NOW,
            )
            self.assertEqual("ready_for_shadow_review", record["status"])
            self.assertTrue(record["shadow_mode"])
            self.assertFalse(record["platform_write_attempted"])
            self.assertFalse(record["can_execute"])
            self.assertEqual("CHECK_CAMPAIGN", record["decision_proposal"]["action"])
            self.assertIsNone(record["human_action"])
            self.assertIsNone(record["outcome"])

            stored = ShadowDecisionStore(temp).list()
            self.assertEqual(1, len(stored))
            self.assertEqual(record["shadow_id"], stored[0]["shadow_id"])
            encoded = json.dumps(stored, ensure_ascii=False)
            self.assertNotIn("api_key", encoded.lower())
            self.assertNotIn("token", encoded.lower())

    def test_expired_shadow_proposal_is_blocked(self):
        record = run_shadow_decision(
            self.snapshot(),
            RulesProvider(),
            now=self.NOW.replace(hour=12),
        )
        self.assertTrue(record["safety"]["passed"])

        with tempfile.TemporaryDirectory() as temp:
            record = run_shadow_decision(
                self.snapshot(),
                RulesProvider(),
                data_dir=temp,
                now=self.NOW,
            )
            self.assertEqual("ready_for_shadow_review", record["status"])


if __name__ == "__main__":
    unittest.main()
