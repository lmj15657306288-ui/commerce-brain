from __future__ import annotations

import unittest

from central_brain.contracts import AlertV1, TaskV1
from central_brain.events import CentralState, EventStoreError


class CentralBrainEventTests(unittest.TestCase):
    def setUp(self):
        self.state = CentralState()

    def test_task_idempotency_and_aggregate_order(self):
        task = TaskV1.create(
            task_id="task_one",
            kind="review",
            created_by="device_brain",
            idempotency_key="idem_task_one",
            payload={"platform_write_attempted": False},
        )
        first = self.state.submit_task(
            task,
            origin_device_id="device_brain",
            recipient_device_id="device_edge",
            session_id="session_brain",
        )
        replay = self.state.submit_task(
            task,
            origin_device_id="device_brain",
            recipient_device_id="device_edge",
            session_id="session_brain",
        )
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(1, self.state.cursor)
        second = self.state.transition_task(
            task.task_id,
            "in_progress",
            origin_device_id="device_brain",
            recipient_device_id="device_edge",
            session_id="session_brain",
            idempotency_key="idem_task_one_progress",
        )
        self.assertEqual(2, second.envelope.cursor)
        self.assertEqual([1, 2], [item.cursor for item in self.state.events_after(0)])
        with self.assertRaises(EventStoreError):
            self.state.submit_task(
                TaskV1.create(
                    task_id="task_one",
                    kind="review",
                    created_by="device_brain",
                    idempotency_key="idem_conflict",
                ),
                origin_device_id="device_brain",
                recipient_device_id="device_edge",
                session_id="session_brain",
            )

    def test_alert_dedupe_does_not_create_duplicate_event(self):
        alert = AlertV1.create(
            alert_id="alert_one",
            severity="warning",
            dedupe_key="live:one",
            title="fixture alert",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        first = self.state.submit_alert(
            alert,
            origin_device_id="device_brain",
            recipient_device_id="device_edge",
            session_id="session_brain",
        )
        replay = self.state.submit_alert(
            alert,
            origin_device_id="device_brain",
            recipient_device_id="device_edge",
            session_id="session_brain",
        )
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(1, self.state.cursor)


if __name__ == "__main__":
    unittest.main()
