"""Outbound HTTPS/sync client for a Mac Brain Worker."""

from __future__ import annotations

import json
import random
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .state import WorkerState, WorkerStateStore


class WorkerTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class WorkerTransportError(RuntimeError):
    pass


class UrllibWorkerTransport:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str,
        timeout_seconds: float = 15.0,
        allow_insecure_local: bool = False,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("worker Control Plane URL must include http(s) scheme and host")
        if parsed.scheme != "https" and not allow_insecure_local:
            raise ValueError("worker production transport requires HTTPS")
        if not bearer_token.strip():
            raise ValueError("worker bearer token is required")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Do not include Authorization or response bodies in the client error.
            raise WorkerTransportError(f"Control Plane returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise WorkerTransportError("Control Plane request failed") from exc


@dataclass(frozen=True)
class WorkerRunResult:
    worker_id: str
    heartbeat_status: str
    events_received: int
    last_ack_cursor: int
    server_time: str | None


class ControlPlaneWorkerClient:
    def __init__(
        self,
        *,
        organization_id: str,
        capabilities: list[str],
        state_store: WorkerStateStore,
        transport: WorkerTransport,
        worker_id: str | None = None,
        worker_type: str = "MAC",
        device_id: str | None = None,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        self.organization_id = organization_id
        self.capabilities = list(dict.fromkeys(capabilities))
        self.worker_type = worker_type
        self.device_id = device_id
        self.state_store = state_store
        self.transport = transport
        if worker_id or state_store.path.exists():
            self.state = state_store.load(worker_id=worker_id)
        else:
            self.state = state_store.load(
                worker_id=f"worker_mac_{uuid.uuid4().hex[:16]}"
            )
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max

    def _registration_key(self) -> str:
        if self.state.registration_idempotency_key is None:
            self.state.registration_idempotency_key = f"worker-register:{self.state.worker_id}"
            self.state_store.save(self.state)
        return self.state.registration_idempotency_key

    def register(self) -> dict[str, Any]:
        return self.transport.request(
            "POST",
            "/workers/register",
            payload={
                "worker_id": self.state.worker_id,
                "worker_type": self.worker_type,
                "capabilities": self.capabilities,
                "organization_id": self.organization_id,
                "idempotency_key": self._registration_key(),
            },
        )

    def heartbeat(self) -> dict[str, Any]:
        return self.transport.request(
            "POST",
            f"/workers/{self.state.worker_id}/heartbeat",
            payload={
                "idempotency_key": f"worker-heartbeat:{self.state.worker_id}:{int(time.time())}",
            },
        )

    def poll_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        response = self.transport.request(
            "GET",
            "/events",
            query={
                "cursor": self.state.last_ack_cursor,
                "limit": limit,
                "organization_id": self.organization_id,
            },
        )
        events = list(response.get("events") or [])
        if events:
            self.state.last_ack_cursor = int(events[-1]["cursor"])
        self.state.last_server_time = response.get("server_time")
        self.state_store.save(self.state)
        return events

    def run_once(self, *, event_limit: int = 100) -> WorkerRunResult:
        self.register()
        heartbeat = self.heartbeat()
        events = self.poll_events(limit=event_limit)
        return WorkerRunResult(
            worker_id=self.state.worker_id,
            heartbeat_status=str((heartbeat.get("data") or {}).get("worker", {}).get("status", "ONLINE")),
            events_received=len(events),
            last_ack_cursor=self.state.last_ack_cursor,
            server_time=self.state.last_server_time,
        )

    def run_forever(
        self,
        *,
        stop_event: threading.Event | None = None,
        event_limit: int = 100,
        interval_seconds: float = 20.0,
        on_result: Any | None = None,
        on_error: Any | None = None,
    ) -> None:
        """Keep the outbound worker session alive with bounded reconnect backoff."""

        if interval_seconds <= 0:
            raise ValueError("worker interval must be positive")
        stop = stop_event or threading.Event()
        failures = 0
        while not stop.is_set():
            try:
                result = self.run_once(event_limit=event_limit)
                failures = 0
                if on_result is not None:
                    on_result(result)
                if stop.wait(interval_seconds):
                    return
            except WorkerTransportError as exc:
                if on_error is not None:
                    on_error(exc)
                delay = self.backoff_delays(attempts=failures + 1)[-1]
                failures += 1
                if stop.wait(delay):
                    return

    def backoff_delays(self, *, attempts: int) -> list[float]:
        delays: list[float] = []
        for index in range(max(0, attempts)):
            base = min(self.backoff_max, self.backoff_initial * (2**index))
            delays.append(round(base * (0.9 + random.random() * 0.2), 3))
        return delays
