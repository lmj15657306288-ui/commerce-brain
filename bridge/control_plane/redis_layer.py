"""Redis-backed ephemeral capabilities with explicit degraded behavior."""

from __future__ import annotations

import json
import time
from threading import RLock
from typing import Any


class RedisEphemeralLayer:
    """Presence, fanout, and short-lived limits only.

    Redis failures are intentionally swallowed at this boundary. PostgreSQL
    remains the source of truth for all business state and event history.
    """

    def __init__(
        self,
        url: str,
        *,
        client: Any | None = None,
        socket_timeout: float = 2.0,
        socket_connect_timeout: float = 2.0,
    ) -> None:
        self.url = url
        self.client = client
        self._lock = RLock()
        self.last_error: str | None = None
        self._tickets: dict[str, tuple[float, dict[str, Any]]] = {}
        if socket_timeout <= 0 or socket_connect_timeout <= 0:
            raise ValueError("Redis timeouts must be positive")
        if self.client is None:
            from redis import Redis
            from redis.backoff import NoBackoff
            from redis.retry import Retry

            self.client = Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=min(socket_timeout, 1.0),
                socket_connect_timeout=min(socket_connect_timeout, 1.0),
                retry_on_timeout=False,
                retry=Retry(NoBackoff(), retries=0),
                health_check_interval=30,
            )

    @property
    def degraded(self) -> bool:
        return self.last_error is not None

    def health_check(self) -> bool:
        try:
            self.client.ping()
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = type(exc).__name__
            return False

    def publish_event(self, event: dict[str, Any], *, channel: str = "commerce_brain.events") -> bool:
        try:
            self.client.publish(
                channel,
                json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            )
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = type(exc).__name__
            return False

    def set_presence(self, key: str, value: dict[str, Any], *, ttl_seconds: int = 120) -> bool:
        try:
            self.client.setex(
                f"presence:{key}",
                ttl_seconds,
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            )
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = type(exc).__name__
            return False

    def consume_presence(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self.client.get(f"presence:{key}")
            self.last_error = None
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            self.last_error = type(exc).__name__
            return None

    def allow_rate(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Best-effort fixed-window limiter.

        If Redis is unavailable, the request is allowed and the dependency is
        reported as degraded. This protects core state from Redis outages.
        """

        redis_key = f"rate:{key}:{int(time.time()) // window_seconds}"
        try:
            count = int(self.client.incr(redis_key))
            if count == 1:
                self.client.expire(redis_key, window_seconds)
            self.last_error = None
            return count <= limit
        except Exception as exc:
            self.last_error = type(exc).__name__
            return True

    def issue_ticket(self, ticket_id: str, value: dict[str, Any], *, ttl_seconds: int) -> bool:
        try:
            created = self.client.set(
                f"ws-ticket:{ticket_id}",
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                ex=ttl_seconds,
                nx=True,
            )
            self.last_error = None
            return bool(created)
        except Exception as exc:
            self.last_error = type(exc).__name__
            return False

    def consume_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        try:
            raw = self.client.getdel(f"ws-ticket:{ticket_id}")
            self.last_error = None
            return None if raw is None else json.loads(raw)
        except Exception as exc:
            self.last_error = type(exc).__name__
            return None


class NoopEphemeralLayer:
    """Deterministic local fallback for tests and SQLite development."""

    degraded = False

    def __init__(self) -> None:
        self._lock = RLock()
        self._tickets: dict[str, tuple[float, dict[str, Any]]] = {}

    def health_check(self) -> bool:
        return True

    def publish_event(self, event: dict[str, Any], *, channel: str = "commerce_brain.events") -> bool:
        return True

    def set_presence(self, key: str, value: dict[str, Any], *, ttl_seconds: int = 120) -> bool:
        return True

    def consume_presence(self, key: str) -> dict[str, Any] | None:
        return None

    def allow_rate(self, key: str, *, limit: int, window_seconds: int) -> bool:
        return True

    def issue_ticket(self, ticket_id: str, value: dict[str, Any], *, ttl_seconds: int) -> bool:
        with self._lock:
            self._purge_tickets()
            if ticket_id in self._tickets:
                return False
            self._tickets[ticket_id] = (
                time.monotonic() + ttl_seconds,
                dict(value),
            )
            return True

    def consume_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_tickets()
            value = self._tickets.pop(ticket_id, None)
            return None if value is None else value[1]

    def _purge_tickets(self) -> None:
        now = time.monotonic()
        self._tickets = {
            ticket: value
            for ticket, value in self._tickets.items()
            if value[0] > now
        }
