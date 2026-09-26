from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from central_brain.contracts import CentralEnvelopeV1
from central_brain.offline_queue import OfflineQueue


class CentralBrainQueueTests(unittest.TestCase):
    def test_queue_claim_ack_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = OfflineQueue(Path(directory) / "queue.db", max_attempts=3)
            envelope = CentralEnvelopeV1.create(
                message_type="event",
                sender_device_id="device_edge",
                recipient_device_id="device_brain",
                session_id="session_edge",
                idempotency_key="idem_offline_one",
                payload={"platform_write_attempted": False, "can_execute": False},
            )
            queue_id = queue.enqueue(envelope, destination="device_brain")
            claimed = queue.claim()
            self.assertEqual(queue_id, claimed[0]["queue_id"])
            self.assertEqual(envelope, claimed[0]["payload"])
            self.assertEqual(1, queue.recover_in_flight())
            self.assertEqual(1, len(queue.claim()))
            self.assertTrue(queue.acknowledge(envelope.message_id))
            self.assertEqual("acked", queue.list()[0]["state"])
            queue.close()

    def test_failed_messages_enter_dead_letter_after_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = OfflineQueue(Path(directory) / "queue.db", max_attempts=2)
            envelope = CentralEnvelopeV1.create(
                message_type="event",
                sender_device_id="device_edge",
                recipient_device_id="device_brain",
                session_id="session_edge",
                idempotency_key="idem_dead_letter",
                payload={"can_execute": False},
            )
            queue.enqueue(envelope, destination="device_brain")
            queue.claim()
            self.assertEqual("pending", queue.fail(envelope.message_id, error_code="TEMPORARY"))
            queue.claim()
            self.assertEqual("dead_letter", queue.fail(envelope.message_id, error_code="REPEATED"))
            self.assertEqual("dead_letter", queue.list()[0]["state"])
            self.assertEqual("REPEATED", queue.list()[0]["last_error_code"])
            queue.close()


if __name__ == "__main__":
    unittest.main()
