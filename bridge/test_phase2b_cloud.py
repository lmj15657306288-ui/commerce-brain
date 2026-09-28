from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from control_plane import (
    FailClosedAuthProvider,
    JWTAuthProvider,
    NoopEphemeralLayer,
    RedisEphemeralLayer,
    RequestIdentity,
    create_app,
)
from core.persistence import PersistenceError
from worker_client import (
    ControlPlaneWorkerClient,
    WorkerStateStore,
    WorkerTransportError,
)


class JWTAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        self.public_pem = self.private_key.public_key().public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )

    def token(self, **overrides: object) -> str:
        now = 1_790_500_000
        claims = {
            "actor_id": "actor_owner",
            "organization_id": "org_demo",
            "roles": ["OWNER"],
            "iat": now,
            "exp": now + 3600,
            "iss": "https://issuer.example",
            "aud": "commerce-brain",
            "jti": "jwt_demo",
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="EdDSA")

    def provider(self, **kwargs: object) -> JWTAuthProvider:
        return JWTAuthProvider(
            public_key=self.public_pem.decode("ascii"),
            issuer="https://issuer.example",
            audience="commerce-brain",
            **kwargs,
        )

    def test_valid_and_invalid_jwt_fail_closed(self) -> None:
        with mock.patch("control_plane.auth.time.time", return_value=1_790_500_100):
            identity = self.provider().authenticate(
                SimpleNamespace(headers={"authorization": f"Bearer {self.token()}"})
            )
        self.assertTrue(identity.authenticated)
        self.assertEqual("actor_owner", identity.actor_id)
        self.assertEqual(("OWNER",), identity.roles)

        bad_signature = jwt.encode(
            {**{"actor_id": "actor_owner"}, "iss": "https://issuer.example"},
            Ed25519PrivateKey.generate(),
            algorithm="EdDSA",
        )
        invalid = self.provider().authenticate(
            SimpleNamespace(headers={"authorization": f"Bearer {bad_signature}"})
        )
        self.assertFalse(invalid.authenticated)

    def test_expiry_issuer_audience_and_revoked_device_fail(self) -> None:
        with mock.patch("control_plane.auth.time.time", return_value=1_790_500_100):
            for overrides in (
                {"exp": 1_790_499_000},
                {"iss": "https://wrong.example"},
                {"aud": "wrong-audience"},
            ):
                identity = self.provider().authenticate(
                    SimpleNamespace(
                        headers={"authorization": f"Bearer {self.token(**overrides)}"}
                    )
                )
                self.assertFalse(identity.authenticated)

            revoked = self.provider(
                device_validator=lambda actor, organization, device: False,
            ).authenticate(
                SimpleNamespace(
                    headers={
                        "authorization": f"Bearer {self.token(device_id='device_revoked')}"
                    }
                )
            )
            self.assertFalse(revoked.authenticated)

    def test_declared_role_must_match_active_assignment_when_configured(self) -> None:
        provider = self.provider(
            role_validator=lambda actor, organization, roles: (
                actor == "actor_owner"
                and organization == "org_demo"
                and "OWNER" in roles
            ),
        )
        with mock.patch("control_plane.auth.time.time", return_value=1_790_500_100):
            valid = provider.authenticate(
                SimpleNamespace(headers={"authorization": f"Bearer {self.token()}"})
            )
            wrong_role = provider.authenticate(
                SimpleNamespace(
                    headers={
                        "authorization": f"Bearer {self.token(roles=['VIEWER'])}"
                    }
                )
            )
        self.assertTrue(valid.authenticated)
        self.assertFalse(wrong_role.authenticated)


class ReadinessTests(unittest.TestCase):
    def test_default_app_is_not_production_ready(self) -> None:
        with TestClient(create_app()) as client:
            self.assertEqual(200, client.get("/health").status_code)
            response = client.get("/readiness")
            self.assertEqual(503, response.status_code)
            self.assertFalse(response.json()["ready"])

    def test_configured_auth_and_local_adapter_are_ready_for_local_production(self) -> None:
        provider = JWTAuthProvider(
            public_key="test-key",
            issuer="issuer",
            audience="audience",
        )
        with TestClient(
            create_app(auth_provider=provider, ephemeral=NoopEphemeralLayer())
        ) as client:
            self.assertEqual(200, client.get("/readiness").status_code)


class RedisDegradedTests(unittest.TestCase):
    class BrokenRedis:
        def ping(self):
            raise OSError("redis unavailable")

        def publish(self, *args, **kwargs):
            raise OSError("redis unavailable")

        def incr(self, *args, **kwargs):
            raise OSError("redis unavailable")

    def test_redis_outage_does_not_block_ephemeral_safe_fallback(self) -> None:
        layer = RedisEphemeralLayer("redis://unavailable", client=self.BrokenRedis())
        self.assertFalse(layer.health_check())
        self.assertFalse(layer.publish_event({"event_id": "event_1"}))
        self.assertTrue(layer.allow_rate("client", limit=1, window_seconds=60))
        self.assertTrue(layer.degraded)

    def test_redis_timeout_configuration_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            RedisEphemeralLayer("redis://unavailable", socket_timeout=0)
        with self.assertRaises(ValueError):
            RedisEphemeralLayer("redis://unavailable", socket_connect_timeout=0)


class WorkerClientTests(unittest.TestCase):
    class FakeTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []

        def request(self, method, path, *, payload=None, query=None):
            self.calls.append((method, path, dict(payload or query or {})))
            if path == "/events":
                return {
                    "events": [{"cursor": 7, "event_id": "event_7"}],
                    "server_time": "2026-09-27T12:00:00+00:00",
                }
            if path.endswith("/heartbeat"):
                return {"data": {"worker": {"status": "ONLINE"}}}
            return {"data": {"worker": {"status": "ONLINE"}}}

    def test_cursor_persists_and_reconnect_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkerStateStore(Path(directory) / "worker-state.json")
            transport = self.FakeTransport()
            client = ControlPlaneWorkerClient(
                organization_id="org_demo",
                capabilities=["Laya", "Knowledge"],
                state_store=store,
                transport=transport,
            )
            result = client.run_once()
            self.assertEqual("ONLINE", result.heartbeat_status)
            self.assertEqual(7, result.last_ack_cursor)
            resumed = ControlPlaneWorkerClient(
                organization_id="org_demo",
                capabilities=["Laya"],
                state_store=store,
                transport=transport,
                worker_id=result.worker_id,
            )
            self.assertEqual(7, resumed.state.last_ack_cursor)
            self.assertLessEqual(max(resumed.backoff_delays(attempts=8)), 60.0 * 1.1)

    def test_run_forever_retries_transport_failure_with_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkerStateStore(Path(directory) / "worker-state.json")
            transport = self.FakeTransport()
            client = ControlPlaneWorkerClient(
                organization_id="org_demo",
                capabilities=["Laya"],
                state_store=store,
                transport=transport,
                backoff_initial=0.001,
                backoff_max=0.001,
            )
            original = client.run_once
            calls = {"count": 0}

            def flaky_run_once(*, event_limit: int = 100):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise WorkerTransportError("offline")
                result = original(event_limit=event_limit)
                stop.set()
                return result

            stop = threading.Event()
            client.run_once = flaky_run_once  # type: ignore[method-assign]
            errors: list[str] = []
            client.run_forever(
                stop_event=stop,
                interval_seconds=0.001,
                on_error=lambda error: errors.append(str(error)),
            )
            self.assertEqual(2, calls["count"])
            self.assertEqual(["offline"], errors)


class PostgresIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        __import__("os").environ.get("TEST_DATABASE_URL"),
        "set TEST_DATABASE_URL to run PostgreSQL integration tests",
    )
    def test_fresh_migration_and_restart_keep_state(self) -> None:
        from core.postgres_persistence import PostgresPersistenceAdapter

        url = __import__("os").environ["TEST_DATABASE_URL"]
        first = PostgresPersistenceAdapter(url)
        try:
            first.save_record(
                "integration",
                "record_1",
                {"organization_id": "org_demo", "value": "persisted"},
                indexes={"organization_id": "org_demo"},
                upsert=True,
            )
            self.assertEqual(1, first.schema_version)
            self.assertEqual("persisted", first.get_record("integration", "record_1")["value"])
        finally:
            first.close()
        second = PostgresPersistenceAdapter(url)
        try:
            self.assertEqual("persisted", second.get_record("integration", "record_1")["value"])
        finally:
            second.close()


class DatabaseFailureContractTests(unittest.TestCase):
    def test_persistence_error_is_not_an_success_signal(self) -> None:
        self.assertTrue(issubclass(PersistenceError, RuntimeError))
        self.assertFalse(FailClosedAuthProvider().configured)


if __name__ == "__main__":
    unittest.main()
