from __future__ import annotations

import unittest

from hermes.adapter import FixtureHermesBackend, HermesCoordinator, UnavailableHermesBackend, role_catalog
from hermes.contracts import build_task
from hermes.tasks import FORBIDDEN_TOOLS, HermesToolRegistry


def _task():
    return build_task(
        task_id="task_live_review",
        task_type="LIVE_REVIEW",
        scope={"shop_id": "shop_fixture"},
        period={"from": "2026-09-26T00:00:00+00:00", "to": "2026-09-26T01:00:00+00:00"},
        inputs={"summary_metrics": {"online_users": 10}, "decisions": [], "outcomes": [], "anomalies": []},
        max_steps=2,
    )


class HermesAdapterTests(unittest.TestCase):
    def test_fixture_coordinator_runs_one_subagent_and_seals_draft(self):
        result = HermesCoordinator(FixtureHermesBackend()).run(_task())
        self.assertEqual("DRAFT", result["status"])
        self.assertEqual(["coordinator", "live_review"], result["role_trace"])
        self.assertFalse(result["safety"]["execution_allowed"])
        self.assertFalse(result["safety"]["platform_write_attempted"])

    def test_unavailable_backend_is_explicit(self):
        result = HermesCoordinator(UnavailableHermesBackend()).run(_task())
        self.assertEqual("UNAVAILABLE", result["status"])
        self.assertFalse(result["safety"]["can_execute"])

    def test_tool_registry_has_no_production_write_capability(self):
        catalog = role_catalog()
        self.assertEqual(5, len(catalog))
        registry = HermesToolRegistry()
        draft = registry.create_draft("create_policy_draft", {"lifecycle": "DRAFT"})
        self.assertFalse(draft.as_dict()["can_execute"])
        for forbidden in FORBIDDEN_TOOLS:
            with self.assertRaises(Exception):
                registry.create_draft(forbidden, {})


if __name__ == "__main__":
    unittest.main()
