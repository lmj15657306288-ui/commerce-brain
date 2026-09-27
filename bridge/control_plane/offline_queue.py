"""Durable outbox abstraction backed by the Phase 2B persistence port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable

from core.contracts import EventEnvelopeV1
from core.persistence import PersistenceAdapter, PersistenceConflict

from .errors import conflict, validation
from .repositories import RecordRepository, fingerprint


@dataclass(frozen=True, slots=True)
class OutboxItem:
    message_id: str
    idempotency_key: str
    destination: str
    payload: dict[str, Any]
    created_at: str
    attempt_count: int
    state: str
    last_error_code: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "idempotency_key": self.idempotency_key,
            "destination": self.destination,
            "payload": self.payload,
            "created_at": self.created_at,
            "attempt_count": self.attempt_count,
            "state": self.state,
            "last_error_code": self.last_error_code,
        }


class DurableOutbox:
    """At-least-once local queue with bounded retry state.

    The queue does not claim exactly-once network delivery. The server
    EventStore and business idempotency keys provide duplicate safety.
    """

    def __init__(
        self,
        adapter: PersistenceAdapter,
        *,
        clock: Callable[[], datetime] | None = None,
        max_attempts: int = 3,
    ) -> None:
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._records = RecordRepository(adapter, "cp_offline_outbox")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_attempts = max_attempts
        self._lock = RLock()

    def enqueue(
        self,
        event: EventEnvelopeV1 | dict[str, Any],
        *,
        destination: str,
    ) -> OutboxItem:
        parsed = event if isinstance(event, EventEnvelopeV1) else EventEnvelopeV1.from_mapping(event)
        if not destination:
            raise validation("outbox destination is required")
        value = {
            "message_id": parsed.event_id,
            "idempotency_key": parsed.idempotency_key,
            "destination": destination,
            "payload": parsed.as_dict(),
            "created_at": self._clock().astimezone(timezone.utc).isoformat(),
            "attempt_count": 0,
            "state": "PENDING",
            "last_error_code": None,
        }
        with self._lock:
            existing = self._records.get(parsed.event_id)
            if existing is not None:
                if fingerprint(existing.get("payload")) != fingerprint(parsed.as_dict()):
                    raise conflict(
                        "outbox message_id was reused with a different event",
                        code="IDEMPOTENCY_CONFLICT",
                    )
                return self._from_mapping(existing)
            try:
                self._records.save(parsed.event_id, value, upsert=False)
            except PersistenceConflict as exc:
                raise conflict(str(exc), code="IDEMPOTENCY_CONFLICT") from exc
            return self._from_mapping(value)

    def claim(self, *, limit: int = 100) -> list[OutboxItem]:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise validation("outbox limit must be between 1 and 500")
        with self._lock:
            pending = [
                self._from_mapping(item)
                for item in self._records.list()
                if item.get("state") == "PENDING"
            ][:limit]
            claimed: list[OutboxItem] = []
            for item in pending:
                value = item.as_dict()
                value["state"] = "IN_FLIGHT"
                self._records.save(item.message_id, value)
                claimed.append(self._from_mapping(value))
            return claimed

    def acknowledge(self, message_id: str) -> bool:
        with self._lock:
            value = self._records.get(message_id)
            if value is None:
                return False
            value["state"] = "ACKED"
            value["last_error_code"] = None
            self._records.save(message_id, value)
            return True

    def retry(self, message_id: str, *, error_code: str) -> OutboxItem:
        with self._lock:
            value = self._records.get(message_id)
            if value is None:
                raise validation("outbox message was not found")
            attempts = int(value.get("attempt_count", 0)) + 1
            value["attempt_count"] = attempts
            value["last_error_code"] = str(error_code)[:80]
            value["state"] = "DEAD_LETTER" if attempts >= self._max_attempts else "PENDING"
            self._records.save(message_id, value)
            return self._from_mapping(value)

    def recover_in_flight(self) -> int:
        count = 0
        with self._lock:
            for value in self._records.list():
                if value.get("state") != "IN_FLIGHT":
                    continue
                value["state"] = "PENDING"
                self._records.save(value["message_id"], value)
                count += 1
        return count

    def list(self, *, state: str | None = None) -> list[OutboxItem]:
        with self._lock:
            values = self._records.list()
            if state is not None:
                values = [item for item in values if item.get("state") == state]
            return [self._from_mapping(item) for item in values]

    @staticmethod
    def _from_mapping(value: dict[str, Any]) -> OutboxItem:
        return OutboxItem(
            message_id=value["message_id"],
            idempotency_key=value["idempotency_key"],
            destination=value["destination"],
            payload=dict(value["payload"]),
            created_at=value["created_at"],
            attempt_count=int(value.get("attempt_count", 0)),
            state=value["state"],
            last_error_code=value.get("last_error_code"),
        )
