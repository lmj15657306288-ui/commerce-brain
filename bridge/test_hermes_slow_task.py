from __future__ import annotations

import json
import unittest

from hermes.slow_task import (
    DRAFT_KINDS,
    HERMES_ROLES,
    FixtureHermesBackend,
    HermesSlowLoop,
    SlowTaskContractError,
    SlowTaskV1,
    UnavailableHermesBackend,
    build_role_catalog,
)


def _task(**updates):
    value = {
        "schema_version": 1,
        "task_id": "slow-fixture-1",
        "objective": "解释直播间转化变化并提出下一步观察建议",
        "requested_role": "coordinator",
        "context": {"window_seconds": 300, "roi": 1.2, "orders": 3},
        "evidence": [
            {
                "evidence_ref": "live-snapshot-1",
                "source": "fixture",
                "quality": {"fresh": True, "score": 92},
                "metrics": {"roi": 1.2, "orders": 3, "product_click_rate": 0.04},
            }
        ],
        "max_steps": 2,
        **updates,
    }
    return value


class HermesSlowTaskTests(unittest.TestCase):
    def test_role_catalog_has_exactly_five_roles(self):
        catalog = build_role_catalog()
        self.assertEqual(set(HERMES_ROLES), set(catalog))
        self.assertEqual(5, len(catalog))
        self.assertTrue(set(DRAFT_KINDS))

    def test_task_rejects_sensitive_fields_and_execution(self):
        with self.assertRaises(SlowTaskContractError):
            SlowTaskV1.from_mapping({**_task(), "context": {"api_key": "secret"}})
        with self.assertRaises(SlowTaskContractError):
            SlowTaskV1.from_mapping({**_task(), "execution_allowed": True})
        with self.assertRaises(SlowTaskContractError):
            SlowTaskV1.from_mapping({**_task(), "requested_role": "browser"})

    def test_stale_or_low_quality_evidence_blocks_summary(self):
        result = HermesSlowLoop(FixtureHermesBackend()).run(
            _task(
                evidence=[
                    {
                        "evidence_ref": "stale-1",
                        "source": "fixture",
                        "quality": {"fresh": False, "score": 95},
                        "metrics": {"roi": 1.0},
                    }
                ]
            )
        )
        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["draft"])
        self.assertFalse(result["safety"]["strong_conclusion_allowed"])
        self.assertFalse(result["safety"]["execution_allowed"])

    def test_fixture_backend_runs_one_role_at_a_time_and_seals_draft(self):
        result = HermesSlowLoop(FixtureHermesBackend()).run(_task())
        self.assertEqual("draft", result["status"])
        self.assertIn(result["draft_kind"], DRAFT_KINDS)
        self.assertTrue(result["draft"])
        self.assertEqual(["live-snapshot-1"], result["evidence_refs"])
        self.assertFalse(result["safety"]["can_execute"])
        self.assertFalse(result["safety"]["execution_performed"])
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("api_key", encoded.lower())
        self.assertNotIn("token", encoded.lower())

    def test_unavailable_hermes_is_not_reported_as_draft(self):
        result = HermesSlowLoop(UnavailableHermesBackend()).run(_task())
        self.assertEqual("unavailable", result["status"])
        self.assertFalse(result["draft"])
        self.assertFalse(result["safety"]["execution_allowed"])

    def test_backend_cannot_smuggle_execution_fields(self):
        class UnsafeBackend:
            def generate(self, **kwargs):
                del kwargs
                return {
                    "status": "draft",
                    "draft_kind": "report",
                    "title": "unsafe",
                    "summary": "unsafe",
                    "evidence_refs": ["live-snapshot-1"],
                    "shell": "rm -rf /",
                }

        with self.assertRaises(SlowTaskContractError):
            HermesSlowLoop(UnsafeBackend()).run(_task())

    def test_task_round_trip_preserves_hash(self):
        task = SlowTaskV1.from_mapping(_task())
        again = SlowTaskV1.from_mapping(task.as_dict() | {"evidence": list(task.evidence)})
        self.assertEqual(task.task_hash, again.task_hash)


if __name__ == "__main__":
    unittest.main()
