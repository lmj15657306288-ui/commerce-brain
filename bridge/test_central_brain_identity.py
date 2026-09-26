from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from central_brain.contracts import DeviceV1
from central_brain.identity import DeviceRegistry, IdentityError, SessionManager


class CentralBrainIdentityTests(unittest.TestCase):
    def test_device_registration_touch_and_revocation(self):
        registry = DeviceRegistry()
        device = DeviceV1.create(
            device_id="device_edge",
            device_type="edge",
            platform="chrome",
            client_version="0.1.0",
        )
        registry.register(device)
        self.assertEqual("active", registry.touch(device.device_id).trust_state)
        registry.set_trust_state(device.device_id, "revoked")
        with self.assertRaises(IdentityError):
            registry.register(device)

    def test_session_resume_cursor_expiry_and_revoke(self):
        current = [datetime(2026, 9, 26, tzinfo=timezone.utc)]
        manager = SessionManager(clock=lambda: current[0])
        device = DeviceV1.create(
            device_id="device_brain",
            device_type="brain",
            platform="macos",
            client_version="0.1.0",
        )
        session = manager.create(device=device, scope={"workspace_id": "workspace_local"}, ttl_seconds=60)
        self.assertEqual(session, manager.validate(session.session_id, device.device_id))
        advanced = manager.advance_cursor(session.session_id, 42)
        self.assertEqual(42, advanced.resume_cursor)
        current[0] += timedelta(seconds=61)
        with self.assertRaises(IdentityError):
            manager.validate(session.session_id, device.device_id)
        revoked = manager.revoke(session.session_id)
        self.assertEqual("revoked", revoked.state)


if __name__ == "__main__":
    unittest.main()
