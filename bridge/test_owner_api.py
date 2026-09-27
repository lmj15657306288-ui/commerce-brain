from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from control_plane import RequestIdentity, create_app
from core.contracts import ScopeGrantV1, ScopeV1
from test_control_plane import ORG, SHOP_A, SHOP_B, ControlPlaneFixture


ORIGIN = "https://app.example.test"


class OwnerReadModelApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ControlPlaneFixture()
        self.client = self.fixture.client

    def tearDown(self) -> None:
        self.fixture.close()

    def test_owner_overview_inbox_and_unknown_impact(self) -> None:
        task = self.client.post(
            "/tasks",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "task_type": "owner_review",
                "priority": 80,
                "owner": "actor_owner",
                "idempotency_key": "owner_task",
            },
            headers=self.fixture.headers(),
        )
        not_owner_task = self.client.post(
            "/tasks",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "task_type": "employee_follow_up",
                "priority": 90,
                "owner": "actor_operator_a",
                "idempotency_key": "employee_task",
            },
            headers=self.fixture.headers(),
        )
        alert = self.client.post(
            "/alerts",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "priority": "P1",
                "reason_code": "LOW_STOCK_SIGNAL",
                "summary": "Stock signal needs review",
                "recommended_action": "Review the linked evidence.",
                "dedupe_key": "stock_signal",
                "idempotency_key": "owner_alert",
            },
            headers=self.fixture.headers(),
        )
        approval = self.client.post(
            "/approvals",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "proposal_id": "proposal_owner_review",
                "risk_level": "HIGH",
                "expires_at": "2026-09-28T12:00:00+00:00",
                "reason": "A proposal requires a human decision.",
                "idempotency_key": "owner_approval",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, task.status_code, task.text)
        self.assertEqual(200, not_owner_task.status_code, not_owner_task.text)
        self.assertEqual(200, alert.status_code, alert.text)
        self.assertEqual(200, approval.status_code, approval.text)

        inbox = self.client.get("/owner/inbox", headers=self.fixture.headers())
        self.assertEqual(200, inbox.status_code, inbox.text)
        payload = inbox.json()
        by_category = {
            category: [item for item in payload["items"] if item["owner_category"] == category]
            for category in ("NEED_DECISION", "NEED_APPROVAL", "NEED_AWARENESS")
        }
        self.assertEqual(1, len(by_category["NEED_DECISION"]))
        self.assertEqual(task.json()["data"]["task_id"], by_category["NEED_DECISION"][0]["item_id"])
        self.assertEqual(
            {"NEED_APPROVAL", "NEED_AWARENESS"},
            {item["owner_category"] for item in payload["items"] if item["item_id"] != task.json()["data"]["task_id"]},
        )
        self.assertNotIn(
            not_owner_task.json()["data"]["task_id"],
            {item["item_id"] for item in payload["items"]},
        )
        alert_item = next(item for item in payload["items"] if item["item_type"] == "ALERT")
        self.assertEqual("Impact not quantified yet.", alert_item["why_it_matters"])
        self.assertTrue(all(item["rank_score"] is None for item in payload["items"]))

        summary = self.client.get("/owner/summary", headers=self.fixture.headers())
        self.assertEqual(200, summary.status_code, summary.text)
        self.assertEqual(2, summary.json()["shop_count"])
        self.assertEqual(1, summary.json()["attention_counts"]["need_approval"])

    def test_owner_reads_require_auth_and_enforce_shop_scope_and_deny(self) -> None:
        unauthenticated = self.client.get("/owner/summary")
        self.assertEqual(401, unauthenticated.status_code)

        shop_b_task = self.client.post(
            "/tasks",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_B},
                "task_type": "private",
                "priority": 80,
                "owner": "actor_owner",
                "idempotency_key": "shop_b_owner_task",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, shop_b_task.status_code)
        scoped_identity = self.client.get(
            f"/owner/shops/{SHOP_B}",
            headers=self.fixture.headers("operator_a"),
        )
        self.assertEqual(403, scoped_identity.status_code)

        self.fixture.registry.register(
            ScopeGrantV1.create(
                grant_id="grant_owner_deny_shop_b",
                actor_id="actor_owner",
                scope=ScopeV1.create(organization_id=ORG, shop_id=SHOP_B),
                capabilities=["context.read", "task.read", "alert.read", "approval.read"],
                effect="DENY",
            )
        )
        shops = self.client.get("/owner/shops", headers=self.fixture.headers())
        self.assertEqual(200, shops.status_code, shops.text)
        self.assertEqual([SHOP_A], [item["shop_id"] for item in shops.json()["items"]])
        inbox = self.client.get("/owner/inbox", headers=self.fixture.headers())
        self.assertEqual(200, inbox.status_code, inbox.text)
        self.assertNotIn(
            shop_b_task.json()["data"]["task_id"],
            {item["item_id"] for item in inbox.json()["items"]},
        )

    def test_owner_summary_and_shop_do_not_invent_live_or_business_metrics(self) -> None:
        shops = self.client.get("/owner/shops", headers=self.fixture.headers()).json()
        self.assertEqual(2, shops["total"])
        for shop in shops["items"]:
            self.assertEqual("UNKNOWN", shop["health"])
            self.assertEqual("NOT_CONNECTED", shop["live_status"])
            self.assertEqual("UNKNOWN", shop["freshness"])
            self.assertEqual(0, shop["high_priority_alerts"])

        live = self.client.get("/owner/live-status", headers=self.fixture.headers())
        self.assertEqual(200, live.status_code)
        self.assertEqual("NOT_CONNECTED", live.json()["status"])
        self.assertIsNone(live.json()["last_updated"])

    def test_shop_lists_are_bounded_and_include_next_offset(self) -> None:
        page = self.client.get(
            "/owner/shops?limit=1&offset=0",
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, page.status_code, page.text)
        self.assertEqual(1, len(page.json()["items"]))
        self.assertEqual(2, page.json()["total"])
        self.assertEqual(1, page.json()["next_offset"])
        self.assertEqual(422, self.client.get(
            "/owner/inbox?limit=101",
            headers=self.fixture.headers(),
        ).status_code)


class OwnerRealtimeTicketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ControlPlaneFixture()
        self.fixture.auth._identities["owner_browser"] = RequestIdentity(
            "actor_owner",
            ORG,
            None,
            ("OWNER",),
            True,
            "local-test",
        )
        self.app = create_app(
            adapter=self.fixture.adapter,
            registry=self.fixture.registry,
            auth_provider=self.fixture.auth,
            clock=self.fixture.clock,
            allowed_origins=[ORIGIN],
        )
        self.client = TestClient(self.app)
        self.owner_headers = {
            "X-Local-Auth": "owner_browser",
            "Origin": ORIGIN,
        }

    def tearDown(self) -> None:
        self.fixture.close()

    def _create_mobile_device(self) -> None:
        response = self.client.post(
            "/devices/session",
            json={
                "device_id": "device_owner_mobile",
                "device_type": "MOBILE",
                "organization_id": ORG,
                "idempotency_key": "owner_mobile_session",
            },
            headers=self.owner_headers,
        )
        self.assertEqual(200, response.status_code, response.text)

    def _ticket(self, cursor: int = 0) -> str:
        response = self.client.post(
            "/owner/realtime-ticket",
            json={
                "device_id": "device_owner_mobile",
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "cursor": cursor,
            },
            headers=self.owner_headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        return response.json()["ticket"]

    def _create_alert(self, key: str) -> None:
        response = self.client.post(
            "/alerts",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "priority": "P1",
                "reason_code": "REALTIME_TEST",
                "summary": key,
                "recommended_action": "Review.",
                "dedupe_key": key,
                "idempotency_key": f"alert_{key}",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, response.status_code, response.text)

    def test_ticket_origin_binding_single_use_and_cursor_replay(self) -> None:
        self._create_mobile_device()
        self._create_alert("event_a")

        ticket = self._ticket()
        with self.client.websocket_connect(
            "/ws/events?device_id=device_owner_mobile&cursor=0",
            headers={"Origin": ORIGIN},
        ) as websocket:
            websocket.send_json({"type": "authenticate", "ticket": ticket})
            self.assertEqual("authenticated", websocket.receive_json()["type"])
            event_a = websocket.receive_json()
            self.assertEqual("alert.created", event_a["event_type"])
            audit = websocket.receive_json()
            self.assertEqual("security.audit", audit["event_type"])
            cursor_a = audit["cursor"]

        self._create_alert("event_b")
        ticket_b = self._ticket(cursor=cursor_a)
        with self.client.websocket_connect(
            f"/ws/events?device_id=device_owner_mobile&cursor={cursor_a}",
            headers={"Origin": ORIGIN},
        ) as websocket:
            websocket.send_json({"type": "authenticate", "ticket": ticket_b})
            self.assertEqual("authenticated", websocket.receive_json()["type"])
            replayed = websocket.receive_json()
            self.assertEqual("alert.created", replayed["event_type"])
            self.assertEqual("event_b", replayed["payload"]["alert"]["dedupe_key"])
            self.assertGreater(replayed["cursor"], cursor_a)

        self.assertIsNone(self.app.state.control_plane.ephemeral.consume_ticket(ticket_b))

        with self.assertRaises(Exception):
            with self.client.websocket_connect(
                "/ws/events?device_id=device_owner_mobile&cursor=0",
                headers={"Origin": ORIGIN},
            ) as websocket:
                websocket.send_json({"type": "authenticate", "ticket": ticket_b})
                websocket.receive_json()

    def test_ticket_requires_allowlisted_origin_and_authorized_scope(self) -> None:
        self._create_mobile_device()
        denied_origin = self.client.post(
            "/owner/realtime-ticket",
            json={
                "device_id": "device_owner_mobile",
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "cursor": 0,
            },
            headers={**self.owner_headers, "Origin": "https://attacker.example"},
        )
        self.assertEqual(403, denied_origin.status_code)

        denied_scope = self.client.post(
            "/owner/realtime-ticket",
            json={
                "device_id": "device_owner_mobile",
                "scope": {"organization_id": "org_other"},
                "cursor": 0,
            },
            headers=self.owner_headers,
        )
        self.assertEqual(403, denied_scope.status_code)

    def test_owner_actions_require_mobile_device_and_do_not_execute_platform_writes(self) -> None:
        self._create_mobile_device()
        alert = self.client.post(
            "/alerts",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "priority": "P1",
                "reason_code": "OWNER_ACTION_TEST",
                "summary": "Owner action alert",
                "recommended_action": "Acknowledge the alert.",
                "dedupe_key": "owner_action_alert",
                "idempotency_key": "owner_action_alert_create",
            },
            headers=self.fixture.headers(),
        )
        approval = self.client.post(
            "/approvals",
            json={
                "scope": {"organization_id": ORG, "shop_id": SHOP_A},
                "proposal_id": "proposal_owner_action",
                "risk_level": "HIGH",
                "expires_at": "2026-09-28T12:00:00+00:00",
                "reason": "Human approval is required.",
                "idempotency_key": "owner_action_approval_create",
            },
            headers=self.fixture.headers(),
        )
        self.assertEqual(200, alert.status_code, alert.text)
        self.assertEqual(200, approval.status_code, approval.text)

        alert_id = alert.json()["data"]["alert_id"]
        approval_id = approval.json()["data"]["approval_id"]
        owner_action_headers = self.owner_headers
        ack = self.client.post(
            f"/owner/alerts/{alert_id}/ack",
            json={
                "device_id": "device_owner_mobile",
                "idempotency_key": "owner_action_alert_ack",
            },
            headers=owner_action_headers,
        )
        approved = self.client.post(
            f"/owner/approvals/{approval_id}/approve",
            json={
                "device_id": "device_owner_mobile",
                "idempotency_key": "owner_action_approval_approve",
            },
            headers=owner_action_headers,
        )
        self.assertEqual(200, ack.status_code, ack.text)
        self.assertEqual(200, approved.status_code, approved.text)
        self.assertEqual("ACKNOWLEDGED", self.client.get(
            f"/owner/alerts/{alert_id}",
            headers=owner_action_headers,
        ).json()["status"])
        self.assertEqual("APPROVED", self.client.get(
            f"/owner/approvals/{approval_id}",
            headers=owner_action_headers,
        ).json()["status"])
        self.assertFalse(approved.json()["data"].get("execution_performed", False))


if __name__ == "__main__":
    unittest.main()
