import unittest
from datetime import datetime, timedelta, timezone

from commerce_contracts import (
    ActionRequestV1,
    ContractValidationError,
    DECISION_PROPOSAL_SCHEMA_VERSION,
    OUTCOME_RECORD_SCHEMA_VERSION,
    STATE_SNAPSHOT_SCHEMA_VERSION,
    DecisionProposalV2,
    OutcomeRecordV1,
    StateSnapshotV1,
    validate_proposal_safety,
)


class CommerceContractTests(unittest.TestCase):
    NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    def snapshot(self, **overrides):
        values = {
            "snapshot_id": "snap-001",
            "captured_at": self.NOW.isoformat(),
            "source": "fixture",
            "source_quality": "high",
            "scope": {"shop_id": "shop-1", "account_id": "account-1"},
            "entity_ids": {"product": ["product-1"]},
            "metrics": {"roi": 1.2, "orders": None, "gmv": 100.0},
        }
        values.update(overrides)
        return StateSnapshotV1.create(**values)

    def proposal(self, snapshot=None, **overrides):
        snapshot = snapshot or self.snapshot()
        values = {
            "decision_id": "decision-001",
            "snapshot": snapshot,
            "decision_type": "live_guardrail",
            "action": "OBSERVE",
            "value": {"minutes": 2},
            "confidence": 0.9,
            "reason_code": "ROI_STABLE",
            "policy_version": "mvp-v1",
            "ttl_seconds": 300,
            "now": self.NOW,
        }
        values.update(overrides)
        return DecisionProposalV2.create(**values)

    def test_snapshot_hash_and_missing_values_are_preserved(self):
        snapshot = self.snapshot()
        self.assertEqual(STATE_SNAPSHOT_SCHEMA_VERSION, snapshot.schema_version)
        self.assertIsNone(snapshot.metrics["orders"])
        parsed = StateSnapshotV1.from_mapping(snapshot.as_dict())
        self.assertEqual(snapshot, parsed)

    def test_expired_proposal_is_rejected(self):
        proposal = self.proposal(ttl_seconds=300)
        errors = validate_proposal_safety(
            proposal,
            current_snapshot=self.snapshot(),
            now=self.NOW + timedelta(minutes=6),
        )
        self.assertIn("PROPOSAL_EXPIRED", {item["code"] for item in errors})

    def test_changed_snapshot_is_rejected(self):
        proposal = self.proposal()
        self.assertEqual(DECISION_PROPOSAL_SCHEMA_VERSION, proposal.schema_version)
        changed = self.snapshot(snapshot_id="snap-002")
        errors = validate_proposal_safety(proposal, current_snapshot=changed, now=self.NOW)
        self.assertIn("SNAPSHOT_CHANGED", {item["code"] for item in errors})
        self.assertIn("EVIDENCE_HASH_MISMATCH", {item["code"] for item in errors})

    def test_evidence_hash_mismatch_is_rejected(self):
        proposal = self.proposal()
        forged = DecisionProposalV2(
            **{**proposal.as_dict(), "evidence_hash": "0" * 64}
        )
        errors = validate_proposal_safety(forged, current_snapshot=self.snapshot(), now=self.NOW)
        self.assertIn("EVIDENCE_HASH_MISMATCH", {item["code"] for item in errors})

    def test_scope_mismatch_is_rejected(self):
        proposal = self.proposal()
        other_scope = self.snapshot(scope={"shop_id": "shop-2", "account_id": "account-1"})
        errors = validate_proposal_safety(proposal, current_snapshot=other_scope, now=self.NOW)
        self.assertIn("SCOPE_MISMATCH", {item["code"] for item in errors})

    def test_illegal_action_is_rejected(self):
        with self.assertRaisesRegex(ContractValidationError, "not allowed"):
            self.proposal(action="ADJUST_BUDGET")

    def test_mapping_with_illegal_action_is_rejected_by_safety(self):
        proposal = self.proposal().as_dict()
        proposal["action"] = "ADJUST_BUDGET"
        errors = validate_proposal_safety(proposal, current_snapshot=self.snapshot(), now=self.NOW)
        self.assertIn("ILLEGAL_ACTION", {item["code"] for item in errors})

    def test_slow_brain_escalation_is_proposal_only(self):
        proposal = self.proposal(action="ESCALATE_SLOW_BRAIN")
        self.assertEqual("ESCALATE_SLOW_BRAIN", proposal.action)
        self.assertFalse(proposal.can_execute)

    def test_action_request_is_non_executable(self):
        request = ActionRequestV1.from_proposal(self.proposal(), action_id="action-001")
        self.assertEqual(1, request.schema_version)
        self.assertFalse(request.can_execute)
        self.assertEqual("OBSERVE", request.action)

    def test_outcome_keeps_null_delta_for_missing_metrics(self):
        outcome = OutcomeRecordV1.create(
            decision_id="decision-001",
            action_id="action-001",
            horizon="2m",
            metrics_before={"orders": None, "gmv": 100},
            metrics_after={"orders": 2, "gmv": 120},
            source="fixture",
            captured_at=self.NOW.isoformat(),
        )
        self.assertEqual(OUTCOME_RECORD_SCHEMA_VERSION, outcome.schema_version)
        self.assertIsNone(outcome.delta["orders"])
        self.assertEqual(20, outcome.delta["gmv"])


if __name__ == "__main__":
    unittest.main()
