from __future__ import annotations

import tempfile
import unittest
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from control_plane import DurableOutbox, FakeClock, LocalAuthProvider, RequestIdentity, create_app
from control_plane.app import ControlPlane
from control_plane.errors import ControlPlaneError
from control_plane.event_store import StoredEvent
from control_plane.websocket import EventBroker, EventSubscription
from core.context_registry import ContextRegistry
from core.contracts import (
    ActorV1,
    CategoryRecordV1,
    EventEnvelopeV1,
    OrganizationRecordV1,
    RoleAssignmentV1,
    ScopeGrantV1,
    ScopeV1,
    ShopCategoryMembershipV1,
    ShopRecordV1,
)
from core.persistence import (
    InMemoryPersistenceAdapter,
    PersistenceError,
    SQLitePersistenceAdapter,
)


ORG = "org_demo"
SHOP_A = "shop_a"
SHOP_B = "shop_b"
CAT_A = "category_a"


class ControlPlaneFixture:
    def __init__(self, *, sqlite_path: Path | None = None) -> None:
        self.adapter = (
            SQLitePersistenceAdapter(sqlite_path)
            if sqlite_path is not None
            else InMemoryPersistenceAdapter()
        )
        self.registry = ContextRegistry(self.adapter)
        self.clock = FakeClock(datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc))
        self._seed()
        self.identities = {
            "owner": RequestIdentity(
                "actor_owner",
                ORG,
                "device_edge",
                ("OWNER",),
                True,
                "local-test",
            ),
            "operator_a": RequestIdentity(
                "actor_operator_a",
                ORG,
                "device_operator_a",
                ("OPERATOR",),
                True,
                "local-test",
            ),
            "anonymous": RequestIdentity.unauthenticated(),
        }
        self.auth = LocalAuthProvider(self.identities)
        self.app = create_app(
            adapter=self.adapter,
            registry=self.registry,
            auth_provider=self.auth,
            clock=self.clock,
        )
        self.client = TestClient(self.app)

    def _seed(self) -> None:
        records = [
            OrganizationRecordV1.create(organization_id=ORG, name="Demo"),
            CategoryRecordV1.create(category_id=CAT_A, organization_id=ORG, name="Apparel"),
            ShopRecordV1.create(shop_id=SHOP_A, organization_id=ORG, name="A"),
            ShopRecordV1.create(shop_id=SHOP_B, organization_id=ORG, name="B"),
            ShopCategoryMembershipV1.create(
                membership_id="membership_shop_a_category_a",
                organization_id=ORG,
                shop_id=SHOP_A,
                category_id=CAT_A,
                source="MANUAL",
            ),
            ShopCategoryMembershipV1.create(
                membership_id="membership_shop_b_category_a",
                organization_id=ORG,
                shop_id=SHOP_B,
                category_id=CAT_A,
                source="MANUAL",
            ),
            ActorV1.create(
                actor_id="actor_owner",
                actor_type="HUMAN",
                role="Owner",
                scope=ScopeV1.create(organization_id=ORG),
            ),
            ActorV1.create(
                actor_id="actor_operator_a",
                actor_type="HUMAN",
                role="Operator",
                scope=ScopeV1.create(organization_id=ORG, shop_id=SHOP_A),
            ),
            RoleAssignmentV1.create(
                assignment_id="assignment_owner",
                actor_id="actor_owner",
                role="Owner",
                scope=ScopeV1.create(organization_id=ORG),
            ),
            RoleAssignmentV1.create(
                assignment_id="assignment_operator_a",
                actor_id="actor_operator_a",
                role="Operator",
                scope=ScopeV1.create(organization_id=ORG, shop_id=SHOP_A),
            ),
            ScopeGrantV1.create(
                grant_id="grant_owner",
                actor_id="actor_owner",
                scope=ScopeV1.create(organization_id=ORG),
                capabilities=[
                    "context.read",
                    "task.read",
                    "task.create",
                    "task.assign",
                    "task.update",
                    "alert.read",
                    "alert.create",
                    "alert.update",
                    "approval.read",
                    "approval.create",
                    "approval.decide",
                    "event.read",
                    "event.write",
                    "sync.read",
                    "sync.write",
                    "device.session",
                    "device.heartbeat",
                    "worker.read",
                    "worker.heartbeat",
                    "lease.acquire",
                    "lease.renew",
                    "lease.release",
                ],
                effect="ALLOW",
            ),
            ScopeGrantV1.create(
                grant_id="grant_operator_a",
                actor_id="actor_operator_a",
                scope=ScopeV1.create(organization_id=ORG, shop_id=SHOP_A),
                capabilities=[
                    "context.read",
                    "task.read",
                    "task.update",
                    "alert.read",
                    "alert.update",
                    "event.read",
                    "event.write",
                    "sync.read",
                    "sync.write",
                    "device.session",
                    "device.heartbeat",
                    "lease.acquire",
                    "lease.renew",
                    "lease.release",
                ],
                effect="ALLOW",
            ),
        ]
        for record in records:
            self.registry.register(record)

    def headers(self, token: str = "owner") -> dict[str, str]:
        return {"X-Local-Auth": token}

    def close(self) -> None:
        self.adapter.close()


class ControlPlaneApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ControlPlaneFixture()
        self.client = self.fixture.client

    def tearDown(self) -> None:
        self.fixture.close()

    def test_health_and_unauthenticated_write_fail_closed(self) -> None:
        self.assertEqual(200, self.client.get("/health").status_code)
        response = self.client.post(
            "/tasks",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "task_type": "review",
                "priority": 10,
                "idempotency_key": "idem_unauth",
            },
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("UNAUTHENTICATED", response.json()["error_code"])

    def test_task_lifecycle_is_explicit_and_idempotent(self) -> None:
        payload = {
            "scope": {"organization_id": ORG, "shop_id": SHOP_A},
            "task_type": "review",
            "priority": 10,
            "idempotency_key": "idem_task_1",
        }
        first = self.client.post("/tasks", json=payload, headers=self.fixture.headers())
        replay = self.client.post("/tasks", json=payload, headers=self.fixture.headers())
        self.assertEqual(200, first.status_code)
        self.assertTrue(replay.json()["replayed"])
        task_id = first.json()["data"]["task_id"]
        started = self.client.post(
            f"/tasks/{task_id}/start",
            json={"idempotency_key": "idem_task_start"},
            headers=self.fixture.headers(),
        )
        self.assertEqual("IN_PROGRESS", started.json()["data"]["status"])
        completed = self.client.post(
            f"/tasks/{task_id}/complete",
            json={"idempotency_key": "idem_task_done"},
            headers=self.fixture.headers(),
        )
        self.assertEqual("DONE", completed.json()["data"]["status"])
        invalid = self.client.post(
            f"/tasks/{task_id}/start",
            json={"idempotency_key": "idem_task_invalid"},
            headers=self.fixture.headers(),
        )
        self.assertEqual(409, invalid.status_code)
        self.assertEqual("INVALID_TRANSITION", invalid.json()["error_code"])

    def test_idempotency_conflict_does_not_overwrite_task(self) -> None:
        base = {
            "scope": {"organization_id": ORG, "shop_id": SHOP_A},
            "task_type": "review",
            "priority": 10,
            "idempotency_key": "idem_same_key",
        }
        self.assertEqual(200, self.client.post("/tasks", json=base, headers=self.fixture.headers()).status_code)
        conflict = self.client.post(
            "/tasks",
            json={**base, "priority": 99},
            headers=self.fixture.headers(),
        )
        self.assertEqual(409, conflict.status_code)
        self.assertEqual("IDEMPOTENCY_CONFLICT", conflict.json()["error_code"])

    def test_task_mutation_rolls_back_when_late_persistence_write_fails(self) -> None:
        control = self.fixture.app.state.control_plane
        before_cursor = control.events.latest_cursor
        with mock.patch.object(
            control.idempotency,
            "save_result",
            side_effect=PersistenceError("simulated persistence failure"),
        ):
            with self.assertRaises(PersistenceError):
                control.tasks.create(
                    self.fixture.identities["owner"],
                    scope=ScopeV1.create(
                        organization_id=ORG,
                        shop_id=SHOP_A,
                    ),
                    task_type="atomicity_test",
                    priority=1,
                    owner=None,
                    due_at=None,
                    business_impact=None,
                    source_refs=[],
                    idempotency_key="idem_atomic_rollback",
                )

        self.assertEqual([], self.fixture.adapter.list_records("cp_tasks"))
        self.assertEqual(before_cursor, control.events.latest_cursor)

    def test_idempotent_replay_still_requires_auth_and_capability(self) -> None:
        payload = {
            "scope": {"organization_id": ORG, "shop_id": SHOP_A},
            "task_type": "review",
            "priority": 10,
            "idempotency_key": "idem_auth_replay",
        }
        created = self.client.post("/tasks", json=payload, headers=self.fixture.headers())
        self.assertEqual(200, created.status_code)

        unauthenticated = self.client.post("/tasks", json=payload)
        self.assertEqual(401, unauthenticated.status_code)
        self.assertEqual("UNAUTHENTICATED", unauthenticated.json()["error_code"])

        insufficient = self.client.post(
            "/tasks",
            json=payload,
            headers=self.fixture.headers("operator_a"),
        )
        self.assertEqual(403, insufficient.status_code)
        self.assertEqual("SCOPE_DENIED", insufficient.json()["error_code"])

    def test_cross_shop_read_and_websocket_subscription_are_denied(self) -> None:
        created = self.client.post(
            "/tasks",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_B},
                "task_type": "review",
                "priority": 10,
                "idempotency_key": "idem_shop_b",
            },
            headers=self.fixture.headers(),
        )
        task_id = created.json()["data"]["task_id"]
        denied = self.client.get(
            f"/tasks/{task_id}",
            headers=self.fixture.headers("operator_a"),
        )
        self.assertEqual(403, denied.status_code)
        self.assertEqual("SCOPE_DENIED", denied.json()["error_code"])
        with self.assertRaises(WebSocketDisconnect) as websocket_error:
            with self.client.websocket_connect(
                    "/ws/events?device_id=device_operator_a&organization_id=org_demo&shop_id=shop_b",
                    headers=self.fixture.headers("operator_a"),
                ):
                pass
        self.assertEqual(4403, websocket_error.exception.code)

    def test_alert_cooldown_deduplicates_without_new_event(self) -> None:
        body = {
            "scope": {"organization_id": ORG, "shop_id": SHOP_A},
            "priority": "P1",
            "reason_code": "LOW_TRAFFIC",
            "summary": "traffic is low",
            "recommended_action": "review evidence",
            "dedupe_key": "shop_a:low_traffic",
            "cooldown_until": "2026-09-27T12:10:00+00:00",
            "idempotency_key": "idem_alert_one",
        }
        first = self.client.post("/alerts", json=body, headers=self.fixture.headers())
        second = self.client.post(
            "/alerts",
            json={**body, "idempotency_key": "idem_alert_two"},
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, first.status_code)
        self.assertTrue(second.json()["deduped"])
        self.assertEqual(first.json()["data"]["alert_id"], second.json()["data"]["alert_id"])

    def test_approval_approved_is_state_only(self) -> None:
        created = self.client.post(
            "/approvals",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "proposal_id": "proposal_demo",
                "risk_level": "LOW",
                "expires_at": "2026-09-27T13:00:00+00:00",
                "reason": "approve a human review draft",
                "idempotency_key": "idem_approval",
            },
            headers=self.fixture.headers(),
        )
        approval_id = created.json()["data"]["approval_id"]
        approved = self.client.post(
            f"/approvals/{approval_id}/approve",
            json={"idempotency_key": "idem_approval_decision", "decision_note": "reviewed"},
            headers=self.fixture.headers(),
        )
        self.assertEqual("APPROVED", approved.json()["data"]["status"])
        self.assertFalse(approved.json()["data"].get("can_execute", False))
        history = self.client.get("/events", headers=self.fixture.headers()).json()["events"]
        approval_events = [item for item in history if item["event_type"] == "approval.updated"]
        self.assertTrue(approval_events)
        self.assertFalse(approval_events[-1]["payload"]["can_execute"])

    def test_event_payload_secret_is_rejected(self) -> None:
        opened = self.client.post(
            "/devices/session",
            json={
                "device_id": "device_edge",
                "device_type": "BROWSER_EXTENSION",
                "organization_id": ORG,
                "idempotency_key": "idem_device",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, opened.status_code, opened.text)
        actor = self.fixture.registry.get_actor("actor_owner")
        event = EventEnvelopeV1.create(
            event_id="event_client_secret",
            event_type="client.event",
            scope=ScopeV1.create(organization_id=ORG),
            actor=actor,
            source="edge",
            occurred_at="2026-09-27T12:00:00+00:00",
            received_at="2026-09-27T12:00:00+00:00",
            idempotency_key="idem_client_secret",
            payload={"safe": True},
        ).as_dict()
        event["payload"] = {"api_key": "must-not-enter"}
        response = self.client.post(
            "/sync",
            json={
                "device_id": "device_edge",
                "actor_id": "actor_owner",
                "client_time": "2026-09-27T12:00:00+00:00",
                "events": [event],
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("VALIDATION_ERROR", response.json()["error_code"])

    def test_device_revoke_blocks_further_heartbeat(self) -> None:
        opened = self.client.post(
            "/devices/session",
            json={
                "device_id": "device_edge",
                "device_type": "BROWSER_EXTENSION",
                "organization_id": ORG,
                "idempotency_key": "idem_device_revoke",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, opened.status_code)
        revoked = self.client.post(
            "/devices/device_edge/revoke",
            json={"idempotency_key": "idem_revoke"},
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, revoked.status_code)
        heartbeat = self.client.post(
            "/devices/device_edge/heartbeat",
            json={"idempotency_key": "idem_after_revoke"},
            headers=self.fixture.headers(),
        )
        self.assertEqual(403, heartbeat.status_code)


class ControlPlaneRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ControlPlaneFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_sync_duplicate_upload_and_cursor_replay(self) -> None:
        opened = self.fixture.client.post(
            "/devices/session",
            json={
                "device_id": "device_edge",
                "device_type": "BROWSER_EXTENSION",
                "organization_id": ORG,
                "idempotency_key": "idem_sync_device",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, opened.status_code)
        actor = self.fixture.registry.get_actor("actor_owner")
        event = EventEnvelopeV1.create(
            event_id="event_offline_one",
            event_type="offline.outcome",
            scope=ScopeV1.create(organization_id=ORG),
            actor=actor,
            source="edge",
            occurred_at="2026-09-27T12:00:00+00:00",
            received_at="2026-09-27T12:00:00+00:00",
            idempotency_key="idem_offline_one",
            payload={"outcome": "human_reviewed", "can_execute": False},
        ).as_dict()
        request = {
            "device_id": "device_edge",
            "actor_id": "actor_owner",
            "idempotency_key": "idem_sync_batch_one",
            "last_ack_cursor": 0,
            "client_time": "2030-01-01T00:00:00+00:00",
            "events": [event],
        }
        first = self.fixture.client.post("/sync", json=request, headers=self.fixture.headers())
        cursor = first.json()["latest_cursor"]
        second = self.fixture.client.post("/sync", json=request, headers=self.fixture.headers())
        self.assertEqual(200, first.status_code)
        self.assertEqual(cursor, second.json()["latest_cursor"])
        self.assertEqual(1, len(second.json()["received_event_cursors"]))
        conflict = self.fixture.client.post(
            "/sync",
            json={**request, "client_time": "2030-01-01T00:00:01+00:00"},
            headers=self.fixture.headers(),
        )
        self.assertEqual(409, conflict.status_code)
        self.assertEqual("IDEMPOTENCY_CONFLICT", conflict.json()["error_code"])
        replay = self.fixture.client.get("/events?cursor=0", headers=self.fixture.headers())
        self.assertTrue(any(item["event_id"] == "event_offline_one" for item in replay.json()["events"]))
        stale = self.fixture.client.get(f"/events?cursor={cursor + 100}", headers=self.fixture.headers())
        self.assertEqual(409, stale.status_code)
        self.assertEqual("STALE_CURSOR", stale.json()["error_code"])

    def test_sqlite_event_store_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sqlite3"
            first = ControlPlaneFixture(sqlite_path=path)
            task = first.client.post(
                "/tasks",
                json={
                    "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                    "task_type": "review",
                    "priority": 10,
                    "idempotency_key": "idem_restart_task",
                },
                headers=first.headers(),
            )
            self.assertEqual(200, task.status_code)
            latest = first.app.state.control_plane.events.latest_cursor
            first.close()
            second = ControlPlaneFixture(sqlite_path=path)
            self.assertEqual(latest, second.app.state.control_plane.events.latest_cursor)
            history = second.client.get("/events", headers=second.headers())
            self.assertEqual(200, history.status_code)
            self.assertGreaterEqual(len(history.json()["events"]), 2)
            second.close()

    def test_websocket_replays_persisted_events_and_delivers_live_event(self) -> None:
        opened = self.fixture.client.post(
            "/devices/session",
            json={
                "device_id": "device_edge",
                "device_type": "BROWSER_EXTENSION",
                "organization_id": ORG,
                "idempotency_key": "idem_ws_device",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, opened.status_code)
        created = self.fixture.client.post(
            "/tasks",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "task_type": "review",
                "priority": 10,
                "idempotency_key": "idem_ws_replay_task",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, created.status_code)
        with self.fixture.client.websocket_connect(
            "/ws/events?device_id=device_edge&organization_id=org_demo&shop_id=shop_a&cursor=0",
            headers=self.fixture.headers(),
        ) as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()
            self.assertIn(first["event_type"], {"task.created", "security.audit"})
            self.assertIn(second["event_type"], {"task.created", "security.audit"})
            follow_up = self.fixture.client.post(
                "/alerts",
                json={
                    "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                    "priority": "P2",
                    "reason_code": "WS_LIVE",
                    "summary": "live event",
                    "recommended_action": "review",
                    "dedupe_key": "ws:live",
                    "idempotency_key": "idem_ws_live_alert",
                },
                headers=self.fixture.headers(),
            )
            self.assertEqual(200, follow_up.status_code)
            live_events = [websocket.receive_json(), websocket.receive_json()]
            self.assertIn("alert.created", {item["event_type"] for item in live_events})


class ControlPlaneBrokerTests(unittest.TestCase):
    def test_bounded_queue_marks_slow_consumer(self) -> None:
        async def scenario() -> None:
            broker = EventBroker(default_queue_size=1)
            subscription = await broker.subscribe(
                client_id="device_slow",
                scope=ScopeV1.create(organization_id=ORG),
                max_queue=1,
            )
            actor = ActorV1.create(
                actor_id="actor_owner",
                actor_type="HUMAN",
                role="Owner",
                scope=ScopeV1.create(organization_id=ORG),
            )
            event_a = EventEnvelopeV1.create(
                event_id="event_queue_a",
                event_type="test",
                scope=ScopeV1.create(organization_id=ORG),
                actor=actor,
                source="test",
                occurred_at="2026-09-27T12:00:00+00:00",
                received_at="2026-09-27T12:00:00+00:00",
                idempotency_key="idem_queue_a",
                payload={"can_execute": False},
            )
            event_b = EventEnvelopeV1.create(
                event_id="event_queue_b",
                event_type="test",
                scope=ScopeV1.create(organization_id=ORG),
                actor=actor,
                source="test",
                occurred_at="2026-09-27T12:00:01+00:00",
                received_at="2026-09-27T12:00:01+00:00",
                idempotency_key="idem_queue_b",
                payload={"can_execute": False},
            )
            first = StoredEvent(1, event_a)
            second = StoredEvent(2, event_b)
            await broker.publish(first)
            await broker.publish(second)
            self.assertEqual("SLOW_CONSUMER", subscription.closed_reason)
            self.assertEqual(1, subscription.queue.maxsize)

        asyncio.run(scenario())

    def test_live_delivery_applies_per_event_visibility(self) -> None:
        async def scenario() -> None:
            broker = EventBroker()
            subscription = await broker.subscribe(
                client_id="owner_device",
                scope=ScopeV1.create(organization_id=ORG),
                visible=lambda event: event.scope.shop_id != SHOP_B,
            )
            actor = ActorV1.create(
                actor_id="actor_owner",
                actor_type="HUMAN",
                role="Owner",
                scope=ScopeV1.create(organization_id=ORG),
            )
            event = EventEnvelopeV1.create(
                event_id="event_hidden_shop_b",
                event_type="task.created",
                scope=ScopeV1.create(organization_id=ORG, shop_id=SHOP_B),
                actor=actor,
                source="test",
                occurred_at="2026-09-27T12:00:00+00:00",
                received_at="2026-09-27T12:00:00+00:00",
                idempotency_key="idem_hidden_shop_b",
                payload={"can_execute": False},
            )
            await broker.publish(StoredEvent(1, event))
            self.assertTrue(subscription.queue.empty())

        asyncio.run(scenario())


class DurableOutboxTests(unittest.TestCase):
    def _event(self, event_id: str, idempotency_key: str) -> EventEnvelopeV1:
        actor = ActorV1.create(
            actor_id="actor_owner",
            actor_type="HUMAN",
            role="Owner",
            scope=ScopeV1.create(organization_id=ORG),
        )
        return EventEnvelopeV1.create(
            event_id=event_id,
            event_type="offline.test",
            scope=ScopeV1.create(organization_id=ORG),
            actor=actor,
            source="edge",
            occurred_at="2026-09-27T12:00:00+00:00",
            received_at="2026-09-27T12:00:00+00:00",
            idempotency_key=idempotency_key,
            payload={"can_execute": False},
        )

    def test_reconnect_retry_ack_and_duplicate_enqueue_are_safe(self) -> None:
        adapter = InMemoryPersistenceAdapter()
        outbox = DurableOutbox(adapter, max_attempts=2)
        try:
            event = self._event("event_outbox_one", "idem_outbox_one")
            first = outbox.enqueue(event, destination="device_brain")
            duplicate = outbox.enqueue(event, destination="device_brain")
            self.assertEqual(first, duplicate)
            claimed = outbox.claim()
            self.assertEqual(1, len(claimed))
            self.assertEqual("IN_FLIGHT", outbox.list()[0].state)
            self.assertEqual(1, outbox.recover_in_flight())
            self.assertEqual("PENDING", outbox.list()[0].state)
            claimed = outbox.claim()
            self.assertEqual("PENDING", outbox.retry(claimed[0].message_id, error_code="OFFLINE").state)
            claimed = outbox.claim()
            self.assertEqual("DEAD_LETTER", outbox.retry(claimed[0].message_id, error_code="OFFLINE").state)
            self.assertFalse(outbox.acknowledge("event_missing"))
        finally:
            adapter.close()


class ControlPlaneServiceTests(unittest.TestCase):
    def test_worker_health_is_server_computed_and_lease_conflict_is_fail_closed(self) -> None:
        fixture = ControlPlaneFixture()
        try:
            client = fixture.client
            worker = client.post(
                "/workers/register",
                json={
                    "worker_id": "worker_mac",
                    "worker_type": "MAC",
                    "organization_id": ORG,
                    "capabilities": ["decision"],
                    "idempotency_key": "idem_worker",
                },
                headers=fixture.headers(),
            )
            self.assertEqual(200, worker.status_code, worker.text)
            lease = client.post(
                "/leases/acquire",
                json={
                    "resource_type": "context",
                    "resource_id": "shop_a",
                    "worker_id": "worker_mac",
                    "duration_seconds": 60,
                    "idempotency_key": "idem_lease_a",
                },
                headers=fixture.headers(),
            )
            self.assertEqual(200, lease.status_code, lease.text)
            conflict = client.post(
                "/leases/acquire",
                json={
                    "resource_type": "context",
                    "resource_id": "shop_a",
                    "worker_id": "worker_other",
                    "duration_seconds": 60,
                    "idempotency_key": "idem_lease_b",
                },
                headers=fixture.headers(),
            )
            self.assertEqual(409, conflict.status_code)
            self.assertEqual("LEASE_CONFLICT", conflict.json()["error_code"])
            fixture.clock.advance(seconds=121)
            workers = client.get("/workers", headers=fixture.headers())
            self.assertEqual("OFFLINE", workers.json()["items"][0]["worker"]["status"])
            task = client.post(
                "/tasks",
                json={
                    "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                    "task_type": "review",
                    "priority": 10,
                    "idempotency_key": "idem_worker_offline_task",
                },
                headers=fixture.headers(),
            )
            self.assertEqual(200, task.status_code)
            self.assertEqual("PENDING", task.json()["data"]["status"])
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()
