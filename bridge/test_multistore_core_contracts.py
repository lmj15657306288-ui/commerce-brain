from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from core.contracts import (
    ActorV1,
    AlertV1,
    ApprovalRequestV1,
    BrainWorkerV1,
    BusinessImpactV1,
    ContractValidationError,
    DeviceSessionV1,
    EventEnvelopeV1,
    ExternalRefV1,
    IdempotencyLedger,
    LeaseV1,
    ScopeV1,
    TaskV1,
    can_acquire_lease,
    stable_hash,
)


class MultiStoreCoreContractTests(unittest.TestCase):
    NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
    LATER = (NOW + timedelta(minutes=5)).isoformat()

    def scope(self, **overrides) -> ScopeV1:
        values = {
            "organization_id": "org_demo",
            "brand_id": "brand_demo",
            "category_id": "category_apparel",
            "shop_id": "shop_a",
        }
        values.update(overrides)
        return ScopeV1.create(**values)

    def actor(self, *, shop_id: str | None = "shop_a", category_id: str | None = "category_apparel") -> ActorV1:
        return ActorV1.create(
            actor_id="actor_owner",
            actor_type="HUMAN",
            role="Owner",
            scope=ScopeV1.create(
                organization_id="org_demo",
                category_id=category_id,
                shop_id=shop_id,
            ),
        )

    def task(self, *, scope: ScopeV1 | None = None, idempotency_key: str = "idem_task_1") -> TaskV1:
        return TaskV1.create(
            task_id="task_1",
            scope=scope or self.scope(),
            task_type="review",
            priority=50,
            created_by=self.actor(),
            created_at=self.NOW.isoformat(),
            updated_at=self.NOW.isoformat(),
            due_at=self.LATER,
            business_impact=BusinessImpactV1.create(profit_impact=120.0, confidence=0.8),
            source_refs=["event_1"],
            idempotency_key=idempotency_key,
        )

    def test_scope_hierarchy_and_round_trip(self):
        scope = self.scope(
            channel_id="channel_live",
            product_id="product_coat",
            sku_id="sku_black_m",
        )
        self.assertEqual("sku", scope.kind)
        self.assertEqual(scope, ScopeV1.from_mapping(scope.as_dict()))

        category = ScopeV1.create(
            organization_id="org_demo",
            brand_id="brand_demo",
            category_id="category_apparel",
        )
        shop_b = ScopeV1.create(
            organization_id="org_demo",
            brand_id="brand_demo",
            category_id="category_apparel",
            shop_id="shop_b",
        )
        self.assertTrue(category.contains(scope))
        self.assertTrue(category.contains(shop_b))
        self.assertFalse(self.scope().contains(shop_b))

    def test_product_scope_requires_category_and_sku_lineage(self):
        with self.assertRaisesRegex(ContractValidationError, "category_id"):
            ScopeV1.create(organization_id="org_demo", shop_id="shop_a", product_id="product_1")
        with self.assertRaisesRegex(ContractValidationError, "product_id"):
            ScopeV1.create(
                organization_id="org_demo",
                category_id="category_apparel",
                shop_id="shop_a",
                sku_id="sku_1",
            )

    def test_category_knowledge_can_serve_multiple_shops_but_not_other_category(self):
        category = ScopeV1.create(organization_id="org_demo", category_id="category_apparel")
        shop_a = ScopeV1.create(
            organization_id="org_demo", category_id="category_apparel", shop_id="shop_a"
        )
        shop_b = ScopeV1.create(
            organization_id="org_demo", category_id="category_apparel", shop_id="shop_b"
        )
        category_b = ScopeV1.create(organization_id="org_demo", category_id="category_food")
        self.assertTrue(category.contains(shop_a))
        self.assertTrue(category.contains(shop_b))
        self.assertFalse(category.contains(category_b))

    def test_global_scope_is_explicit_and_unknown_scope_fields_fail_closed(self):
        self.assertTrue(ScopeV1.create().is_global)
        with self.assertRaisesRegex(ContractValidationError, "unsupported"):
            ScopeV1.from_mapping({"scope_kind": "global"})
        with self.assertRaises(ContractValidationError):
            ScopeV1.create(organization_id="not valid")

    def test_scope_hash_is_stable_when_mapping_order_changes(self):
        left = {"organization_id": "org_demo", "category_id": "category_apparel", "shop_id": "shop_a"}
        right = {"shop_id": "shop_a", "category_id": "category_apparel", "organization_id": "org_demo"}
        self.assertEqual(stable_hash(left), stable_hash(right))
        self.assertEqual(ScopeV1.create(**left).stable_hash(), ScopeV1.create(**right).stable_hash())

    def test_external_id_is_not_internal_identity_and_conflicts_are_visible(self):
        first = ExternalRefV1.create(
            source="douyin",
            entity_type="product",
            external_id=12345,
            shop_id="shop_a",
        )
        second = ExternalRefV1.create(
            source="douyin",
            entity_type="product",
            external_id="12345",
            shop_id="shop_b",
        )
        self.assertEqual(("douyin", "product", "12345"), first.identity_key())
        self.assertTrue(first.conflicts_with(second))
        self.assertEqual(first, ExternalRefV1.from_mapping(first.as_dict()))

    def test_actor_scope_denies_other_shop(self):
        actor = self.actor()
        self.assertTrue(actor.can_access(self.scope()))
        self.assertFalse(actor.can_access(self.scope(shop_id="shop_b")))
        with self.assertRaises(ContractValidationError):
            EventEnvelopeV1.create(
                event_id="event_1",
                event_type="task.created",
                scope=self.scope(shop_id="shop_b"),
                actor=actor,
                source="fixture",
                occurred_at=self.NOW.isoformat(),
                received_at=self.NOW.isoformat(),
                idempotency_key="idem_event_1",
                payload={"kind": "task"},
            )

    def test_device_session_round_trip_and_shop_boundary(self):
        session = DeviceSessionV1.create(
            device_id="device_edge_1",
            device_type="BROWSER_EXTENSION",
            user_id="user_1",
            organization_id="org_demo",
            shop_ids=["shop_b", "shop_a"],
            capabilities=["observer", "task_sync"],
            connected_at=self.NOW.isoformat(),
            last_seen_at=self.NOW.isoformat(),
        )
        self.assertEqual(["shop_a", "shop_b"], session.as_dict()["shop_ids"])
        self.assertEqual("shop_a", session.scope_for_shop("shop_a").shop_id)
        with self.assertRaises(ContractValidationError):
            session.scope_for_shop("shop_c")
        self.assertEqual(session, DeviceSessionV1.from_mapping(session.as_dict()))

    def test_worker_health_contract_has_no_scheduler_behavior(self):
        worker = BrainWorkerV1.create(
            worker_id="worker_mac_1",
            worker_type="MAC",
            capabilities=["laya", "hermes"],
            status="DEGRADED",
            heartbeat_at=self.NOW.isoformat(),
            last_seen_at=self.NOW.isoformat(),
        )
        self.assertEqual("DEGRADED", worker.status)
        self.assertEqual(worker, BrainWorkerV1.from_mapping(worker.as_dict()))

    def test_actor_lease_and_event_round_trip(self):
        actor = self.actor()
        self.assertEqual(actor, ActorV1.from_mapping(actor.as_dict()))
        lease = LeaseV1.create(
            lease_id="lease_1",
            resource_type="task",
            resource_id="task_1",
            worker_id="worker_1",
            issued_at=self.NOW.isoformat(),
            expires_at=self.LATER,
            idempotency_key="idem_lease_1",
        )
        self.assertEqual(lease, LeaseV1.from_mapping(lease.as_dict()))
        event = EventEnvelopeV1.create(
            event_id="event_1",
            event_type="task.created",
            scope=self.scope(),
            actor=actor,
            source="fixture",
            occurred_at=self.NOW.isoformat(),
            received_at=self.NOW.isoformat(),
            idempotency_key="idem_event_1",
            payload={"kind": "task", "platform_write_attempted": False},
        )
        self.assertEqual(event, EventEnvelopeV1.from_mapping(event.as_dict()))

    def test_unexpired_lease_blocks_second_worker_and_expired_lease_reopens_resource(self):
        lease = LeaseV1.create(
            lease_id="lease_1",
            resource_type="task",
            resource_id="task_1",
            worker_id="worker_1",
            issued_at=self.NOW.isoformat(),
            expires_at=self.LATER,
            idempotency_key="idem_lease_1",
        )
        self.assertFalse(
            can_acquire_lease(
                lease,
                now=self.NOW,
                worker_id="worker_2",
                resource_type="task",
                resource_id="task_1",
            )
        )
        expired = self.NOW + timedelta(minutes=10)
        self.assertTrue(can_acquire_lease(lease, now=expired))

    def test_business_impact_preserves_unknown_and_zero(self):
        impact = BusinessImpactV1.create(gmv_impact=0, profit_impact=None)
        self.assertEqual(0.0, impact.gmv_impact)
        self.assertTrue(impact.is_unknown("profit_impact"))
        parsed = BusinessImpactV1.from_mapping(impact.as_dict())
        self.assertEqual(impact, parsed)

    def test_task_and_alert_round_trip_and_fail_closed_transitions(self):
        task = self.task()
        self.assertEqual(task, TaskV1.from_mapping(task.as_dict()))
        self.assertEqual("ASSIGNED", task.transition("ASSIGNED").status)
        with self.assertRaises(ContractValidationError):
            task.transition("DONE")

        alert = AlertV1.create(
            alert_id="alert_1",
            scope=self.scope(),
            priority="P1",
            reason_code="PROFIT_RISK",
            summary="需要人工复核",
            evidence_refs=["evidence_1"],
            business_impact={"profit_impact": -100, "confidence": 0.7},
            recommended_action="review",
            dedupe_key="profit:shop_a",
            created_at=self.NOW.isoformat(),
            updated_at=self.NOW.isoformat(),
        )
        self.assertEqual(alert, AlertV1.from_mapping(alert.as_dict()))
        self.assertEqual("ACKNOWLEDGED", alert.transition("ACKNOWLEDGED").status)

    def test_approval_scope_must_not_exceed_actor_scope(self):
        approval = ApprovalRequestV1.create(
            approval_id="approval_1",
            scope=self.scope(),
            proposal_id="proposal_1",
            risk_level="LOW",
            requested_by="actor_owner",
            requested_at=self.NOW.isoformat(),
            expires_at=self.LATER,
            reason="review proposal",
        )
        approval.validate_requester(self.actor())
        with self.assertRaises(ContractValidationError):
            approval.validate_requester(self.actor(shop_id="shop_b"))

    def test_approval_does_not_have_executable_payload(self):
        approval = ApprovalRequestV1.create(
            approval_id="approval_1",
            scope=self.scope(),
            proposal_id="proposal_1",
            risk_level="HIGH",
            requested_by="actor_owner",
            requested_at=self.NOW.isoformat(),
            expires_at=self.LATER,
            reason="review proposal",
        )
        self.assertNotIn("payload", approval.as_dict())
        self.assertEqual(approval, ApprovalRequestV1.from_mapping(approval.as_dict()))

    def test_event_filters_sensitive_and_execution_payload(self):
        with self.assertRaises(ContractValidationError):
            EventEnvelopeV1.create(
                event_id="event_1",
                event_type="task.created",
                scope=self.scope(),
                actor=self.actor(),
                source="fixture",
                occurred_at=self.NOW.isoformat(),
                received_at=self.NOW.isoformat(),
                idempotency_key="idem_event_1",
                payload={"token": "<REDACTED>"},
            )
        with self.assertRaises(ContractValidationError):
            EventEnvelopeV1.create(
                event_id="event_1",
                event_type="task.created",
                scope=self.scope(),
                actor=self.actor(),
                source="fixture",
                occurred_at=self.NOW.isoformat(),
                received_at=self.NOW.isoformat(),
                idempotency_key="idem_event_1",
                payload={"can_execute": True},
            )

    def test_idempotency_duplicate_is_same_semantics_and_conflict_is_rejected(self):
        ledger = IdempotencyLedger()
        self.assertEqual("task_1", ledger.register(kind="task", idempotency_key="idem_1", resource_id="task_1"))
        self.assertEqual("task_1", ledger.register(kind="task", idempotency_key="idem_1", resource_id="task_1"))
        with self.assertRaises(ContractValidationError):
            ledger.register(kind="task", idempotency_key="idem_1", resource_id="task_2")

    def test_all_contracts_reject_unknown_fields(self):
        with self.assertRaises(ContractValidationError):
            TaskV1.from_mapping(self.task().as_dict() | {"platform_write": True})
        with self.assertRaises(ContractValidationError):
            BrainWorkerV1.from_mapping(
                BrainWorkerV1.create(worker_id="worker_1", worker_type="MAC").as_dict()
                | {"secret": "must not exist"}
            )


if __name__ == "__main__":
    unittest.main()
