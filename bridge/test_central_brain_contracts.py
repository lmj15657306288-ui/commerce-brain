from __future__ import annotations

import unittest

from central_brain.contracts import (
    AlertV1,
    CentralEnvelopeV1,
    ContractValidationError,
    DeviceV1,
    EventV1,
    SessionV1,
    TaskV1,
)


class CentralBrainContractTests(unittest.TestCase):
    def setUp(self):
        self.brain = DeviceV1.create(
            device_id="device_brain",
            device_type="brain",
            platform="macos",
            client_version="0.1.0",
            capabilities=["brain_state"],
        )

    def test_all_versioned_contracts_round_trip(self):
        session = SessionV1.create(
            session_id="session_brain",
            device_id=self.brain.device_id,
            role="brain",
            scope={"workspace_id": "workspace_local"},
            expires_at="2099-01-01T00:00:00+00:00",
        )
        task = TaskV1.create(
            task_id="task_review",
            kind="review",
            created_by=self.brain.device_id,
            idempotency_key="idem_task_review",
            payload={"reason": "fixture", "can_execute": False},
        )
        alert = AlertV1.create(
            alert_id="alert_live",
            severity="warning",
            dedupe_key="live:fixture",
            title="需要人工复核",
            evidence_refs=["evidence_live"],
            expires_at="2099-01-01T00:00:00+00:00",
        )
        event = EventV1.create(
            event_id="evt_task_review",
            topic="task.updated",
            aggregate_id=task.task_id,
            aggregate_version=1,
            origin_device_id=self.brain.device_id,
            payload={"task": task.as_dict(), "can_execute": False},
        )
        envelope = CentralEnvelopeV1.create(
            message_id="msg_task_review",
            event_id=event.event_id,
            message_type="event",
            sender_device_id=self.brain.device_id,
            recipient_device_id="device_edge",
            session_id=session.session_id,
            idempotency_key="idem_event_review",
            cursor=1,
            payload={"event": event.as_dict(), "platform_write_attempted": False},
        )
        self.assertEqual(self.brain, DeviceV1.from_mapping(self.brain.as_dict()))
        self.assertEqual(session, SessionV1.from_mapping(session.as_dict()))
        self.assertEqual(task, TaskV1.from_mapping(task.as_dict()))
        self.assertEqual(alert, AlertV1.from_mapping(alert.as_dict()))
        self.assertEqual(event, EventV1.from_mapping(event.as_dict()))
        self.assertEqual(envelope, CentralEnvelopeV1.from_mapping(envelope.as_dict()))

    def test_sensitive_and_execution_fields_are_rejected(self):
        with self.assertRaises(ContractValidationError):
            CentralEnvelopeV1.create(
                message_type="event",
                sender_device_id="device_brain",
                recipient_device_id="device_edge",
                session_id="session_brain",
                payload={"api_key": "real-looking-secret"},
            )
        with self.assertRaises(ContractValidationError):
            CentralEnvelopeV1.create(
                message_type="event",
                sender_device_id="device_brain",
                recipient_device_id="device_edge",
                session_id="session_brain",
                payload={"can_execute": True},
            )
        with self.assertRaises(ContractValidationError):
            TaskV1.from_mapping(
                TaskV1.create(kind="review", created_by="device_brain").as_dict()
                | {"can_execute": True}
            )

    def test_task_and_alert_transitions_are_fail_closed(self):
        task = TaskV1.create(kind="review", created_by="device_brain")
        self.assertEqual("in_progress", task.transition("in_progress").status)
        with self.assertRaises(ContractValidationError):
            task.transition("completed")
        alert = AlertV1.create(
            severity="info",
            dedupe_key="fixture:one",
            title="fixture",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        self.assertEqual("acknowledged", alert.transition("acknowledged").status)
        with self.assertRaises(ContractValidationError):
            alert.transition("open")


if __name__ == "__main__":
    unittest.main()
