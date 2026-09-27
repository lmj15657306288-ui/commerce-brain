"""Run the local Docker Phase 2B-4 worker replay smoke test."""

from __future__ import annotations

import os
import tempfile
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from worker_client import ControlPlaneWorkerClient, UrllibWorkerTransport, WorkerStateStore


def main() -> int:
    base_url = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:18000")
    private_key_path = Path(
        os.environ.get("JWT_PRIVATE_KEY_PATH", "secrets/jwt_private_key.pem")
    )
    if not private_key_path.exists():
        raise SystemExit(
            "missing local smoke signing key; create deploy/secrets/jwt_private_key.pem "
            "and the matching jwt_public_key.pem with chmod 600"
        )
    private_key = load_pem_private_key(private_key_path.read_bytes(), password=None)
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "actor_id": "actor_owner",
            "organization_id": "org_demo",
            "roles": ["OWNER"],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "iss": os.environ.get("JWT_ISSUER", "https://issuer.example.invalid/"),
            "aud": os.environ.get("JWT_AUDIENCE", "commerce-brain-control-plane"),
            "jti": f"smoke-{int(time.time())}",
        },
        private_key,
        algorithm="EdDSA",
    )
    transport = UrllibWorkerTransport(
        base_url,
        bearer_token=token,
        allow_insecure_local=base_url.startswith("http://127.0.0.1"),
    )
    with tempfile.TemporaryDirectory() as directory:
        client = ControlPlaneWorkerClient(
            organization_id="org_demo",
            capabilities=["Laya", "Hermes", "Knowledge"],
            worker_id="worker_mac_smoke",
            state_store=WorkerStateStore(Path(directory) / "worker-state.json"),
            transport=transport,
        )
        first = client.run_once()
        baseline_cursor = first.last_ack_cursor
        transport.request(
            "POST",
            "/tasks",
            payload={
                "scope": {"organization_id": "org_demo"},
                "task_type": "phase2b4_smoke",
                "priority": 1,
                "idempotency_key": "phase2b4-smoke-task",
            },
        )
        replayed = client.poll_events()
        second = client.poll_events()
        if not any(item.get("event_type") == "task.created" for item in replayed):
            raise SystemExit("worker replay did not receive the task event")
        if second:
            raise SystemExit("same cursor produced duplicate events")
        print(
            f"smoke_ok worker={first.worker_id} "
            f"baseline_cursor={baseline_cursor} "
            f"replayed_events={len(replayed)} final_cursor={client.state.last_ack_cursor}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
