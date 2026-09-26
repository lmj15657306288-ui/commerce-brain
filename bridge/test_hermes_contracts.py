from __future__ import annotations

import unittest

from hermes.contracts import HermesContractError, SlowResultV1, SlowTaskV1, build_task


class HermesContractsTests(unittest.TestCase):
    def setUp(self):
        self.task = build_task(
            task_id="task_daily_review",
            task_type="DAILY_REVIEW",
            scope={"shop_id": "shop_fixture", "account_id": "account_fixture"},
            period={"from": "2026-09-25T00:00:00+00:00", "to": "2026-09-26T00:00:00+00:00"},
            inputs={
                "summary_metrics": {"roi": 1.2, "orders": 3},
                "decisions": [],
                "outcomes": [],
                "anomalies": [],
            },
        )

    def test_task_round_trip_and_result_contract(self):
        again = SlowTaskV1.from_mapping(self.task.as_dict())
        self.assertEqual(self.task.task_hash, again.task_hash)
        result = SlowResultV1.from_mapping(
            {
                "schema_version": 1,
                "task_id": self.task.task_id,
                "task_hash": self.task.task_hash,
                "status": "DRAFT",
                "summary": "Fixture summary",
                "findings": [{"code": "F1"}],
                "recommended_actions": [{"kind": "human_review", "can_execute": False}],
                "policy_drafts": [{"lifecycle": "DRAFT"}],
                "feature_suggestions": [],
                "data_quality_issues": [],
                "role_trace": ["coordinator"],
            },
            task=self.task,
        )
        self.assertFalse(result.as_dict()["safety"]["can_execute"])

    def test_sensitive_fields_and_non_draft_actions_are_rejected(self):
        with self.assertRaises(HermesContractError):
            build_task(
                task_id="task_secret",
                task_type="DAILY_REVIEW",
                scope={"shop_id": "shop_fixture"},
                period={"from": "a", "to": "b"},
                inputs={"api_key": "secret"},
            )
        with self.assertRaises(HermesContractError):
            SlowResultV1.from_mapping(
                {
                    "schema_version": 1,
                    "task_id": self.task.task_id,
                    "task_hash": self.task.task_hash,
                    "status": "BLOCKED",
                    "summary": "blocked",
                    "findings": [],
                    "recommended_actions": [{"kind": "set_budget"}],
                    "policy_drafts": [],
                    "feature_suggestions": [],
                    "data_quality_issues": [],
                    "role_trace": [],
                },
                task=self.task,
            )


if __name__ == "__main__":
    unittest.main()
