from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import server
from mcp_fast_tools import FAST_TOOL_NAMES, FastToolInputError, invoke_fast_tool


def _call(name: str, arguments: dict) -> dict:
    content = asyncio.run(server.call_tool(name, arguments))
    return json.loads(content[0].text)


def _state(**metrics) -> dict:
    return {
        "snapshot_id": "fixture-1",
        "metrics": {
            "roi": 0.8,
            "orders": 2,
            "inventory_days": 14,
            **metrics,
        },
    }


class MCPFastToolTests(unittest.TestCase):
    def test_all_fast_tools_are_allowlisted_and_sealed(self):
        self.assertTrue(FAST_TOOL_NAMES.issubset({tool.name for tool in server.TOOLS}))
        result = _call("fast_choice", {"state": _state(), "provider": "rules"})
        self.assertEqual("CHECK_CAMPAIGN", result["choice"])
        self.assertEqual("proposal_only", result["mode"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["can_execute"])
        self.assertFalse(result["execution_performed"])

    def test_scalar_tools_return_only_the_requested_projection(self):
        for name, field in (
            ("fast_choice", "choice"),
            ("fast_binary", "binary"),
            ("fast_score", "score"),
        ):
            with self.subTest(name=name):
                result = _call(name, {"state": _state(), "provider": "rules"})
                self.assertIn(field, result)
                self.assertIn("state_hash", result)
                self.assertNotIn("metrics", result)
                self.assertFalse(result["can_execute"])

    def test_route_task_escalates_only_as_a_proposal(self):
        result = _call(
            "route_task",
            {"task": "live_state", "state": _state(), "provider": "rules"},
        )
        self.assertEqual("fast_brain", result["route"])
        result = _call(
            "route_task",
            {"task": "live_state", "state": _state(roi=1.2), "provider": "rules"},
        )
        self.assertEqual("fast_brain", result["route"])
        result = _call("route_task", {"task": "strategy"})
        self.assertEqual("slow_brain", result["route"])
        self.assertFalse(result["execution_performed"])

    def test_live_state_classification_uses_fixture_rules(self):
        snapshot = {
            "schema_version": 1,
            "snapshot_id": "live-fixture-1",
            "captured_at": "2026-09-26T12:00:00+00:00",
            "source": "demo_fixture",
            "window_seconds": 30,
            "metrics": {
                "online_users": 100,
                "enter_rate": 0.2,
                "leave_rate": 0.1,
                "product_click_rate": 0.01,
                "orders": 2,
                "gmv": 200,
                "ad_spend": 100,
                "roi": 1.2,
            },
            "evidence_hash": "fixture-hash",
        }
        result = _call("classify_live_state", {"snapshot": snapshot})
        self.assertEqual("CHECK_PRODUCT", result["classification"]["choice"])
        self.assertEqual("live-fixture-1", result["snapshot_id"])

    def test_laya_provider_is_called_through_the_adapter(self):
        expected = {
            "choice": "OBSERVE",
            "binary": False,
            "score": 0.5,
            "confidence": 0.9,
            "reason_code": "TEST",
            "provider": "laya",
            "model": "test",
            "checkpoint": "test",
            "state_hash": "a" * 64,
            "latency_ms": 3,
        }
        with patch("mcp_fast_tools._provider") as provider_factory:
            provider = provider_factory.return_value
            provider.decide.return_value = expected
            result = invoke_fast_tool("fast_choice", {"state": _state()})
        provider_factory.assert_called_once_with("laya")
        provider.decide.assert_called_once()
        self.assertEqual("OBSERVE", result["choice"])

    def test_sensitive_and_unbounded_inputs_are_rejected(self):
        with self.assertRaises(FastToolInputError):
            invoke_fast_tool("fast_choice", {"state": {"metrics": {"api_key": "secret"}}})
        with self.assertRaises(FastToolInputError):
            invoke_fast_tool("fast_choice", {"state": {"metrics": {"roi": float("inf")}}})

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(FastToolInputError):
            invoke_fast_tool("execute_action", {})


if __name__ == "__main__":
    unittest.main()
