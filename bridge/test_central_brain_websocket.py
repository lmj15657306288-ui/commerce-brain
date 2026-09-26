from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from central_brain.contracts import CentralEnvelopeV1, DeviceV1
from central_brain.offline_queue import OfflineQueue
from central_brain.websocket import FixtureGateway, RealtimeClient


class CentralBrainWebSocketTests(unittest.TestCase):
    def test_brain_gateway_edge_realtime_ack_and_resume(self):
        async def scenario() -> None:
            gateway = FixtureGateway()
            brain_device = DeviceV1.create(
                device_id="device_brain",
                device_type="brain",
                platform="macos",
                client_version="0.1.0",
            )
            edge_device = DeviceV1.create(
                device_id="device_edge",
                device_type="edge",
                platform="chrome",
                client_version="0.1.0",
            )
            with tempfile.TemporaryDirectory() as directory:
                queue = OfflineQueue(Path(directory) / "edge.db")
                brain = RealtimeClient(
                    device=brain_device,
                    gateway=gateway,
                    session_id="session_brain",
                    auto_ack=True,
                )
                edge = RealtimeClient(
                    device=edge_device,
                    gateway=gateway,
                    session_id="session_edge",
                    offline_queue=queue,
                    auto_ack=True,
                )
                await brain.connect()
                await edge.connect()
                event = CentralEnvelopeV1.create(
                    message_type="event",
                    sender_device_id=brain_device.device_id,
                    recipient_device_id=edge_device.device_id,
                    session_id=brain.session_id,
                    cursor=1,
                    idempotency_key="idem_live_event",
                    payload={"topic": "state.updated", "platform_write_attempted": False},
                )
                self.assertTrue(await brain.publish(event))
                received = await edge.receive_once()
                self.assertEqual(event.message_id, received.message_id)
                ack = await brain.receive_once()
                self.assertEqual("ack", ack.message_type)

                await edge.disconnect()
                offline_event = CentralEnvelopeV1.create(
                    message_type="event",
                    sender_device_id=edge_device.device_id,
                    recipient_device_id=brain_device.device_id,
                    session_id=edge.session_id,
                    cursor=2,
                    idempotency_key="idem_edge_offline",
                    payload={"topic": "outcome.recorded", "can_execute": False},
                )
                self.assertFalse(await edge.publish(offline_event))
                self.assertEqual("pending", queue.list()[0]["state"])
                await edge.connect(resume_cursor=0)
                resumed = await edge.receive_once()
                self.assertEqual(event.message_id, resumed.message_id)
                resumed_ack = await brain.receive_once()
                self.assertEqual("ack", resumed_ack.message_type)
                self.assertEqual(1, await edge.flush())
                replayed = await brain.receive_once()
                self.assertEqual(offline_event.message_id, replayed.message_id)
                edge_ack = await edge.receive_once()
                self.assertEqual("ack", edge_ack.message_type)
                self.assertEqual("acked", queue.list()[0]["state"])
                await edge.disconnect()
                await brain.disconnect()
                await gateway.close()
                queue.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
