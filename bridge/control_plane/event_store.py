"""Append-only Event Store with server cursors and idempotent ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable

from core.contracts import EventEnvelopeV1, ScopeV1
from core.persistence import PersistenceAdapter

from .errors import conflict
from .repositories import RecordRepository


@dataclass(frozen=True, slots=True)
class StoredEvent:
    cursor: int
    event: EventEnvelopeV1

    def as_dict(self) -> dict:
        result = self.event.as_dict()
        result["cursor"] = self.cursor
        return result


class EventStore:
    """Durable local event log.

    Events are never updated or deleted. Corrections are represented by a new
    event that references the earlier event in its payload.
    """

    def __init__(self, adapter: PersistenceAdapter) -> None:
        self._records = RecordRepository(adapter, "cp_events")
        self._lock = RLock()

    def _all(self) -> list[StoredEvent]:
        values = []
        for raw in self._records.list():
            event_raw = raw.get("event")
            cursor = raw.get("cursor")
            if not isinstance(event_raw, dict) or not isinstance(cursor, int):
                continue
            values.append(StoredEvent(cursor, EventEnvelopeV1.from_mapping(event_raw)))
        return sorted(values, key=lambda item: item.cursor)

    @property
    def latest_cursor(self) -> int:
        with self._lock:
            events = self._all()
            return events[-1].cursor if events else 0

    def append(self, event: EventEnvelopeV1) -> tuple[StoredEvent, bool]:
        with self._lock:
            for stored in self._all():
                if stored.event.idempotency_key == event.idempotency_key:
                    if stored.event.as_dict() == event.as_dict():
                        return stored, True
                    raise conflict(
                        "event idempotency key was reused with a different event",
                        code="IDEMPOTENCY_CONFLICT",
                    )
                if stored.event.event_id == event.event_id:
                    if stored.event.as_dict() == event.as_dict():
                        return stored, True
                    raise conflict("event_id already belongs to another event")
            cursor = self.latest_cursor + 1
            stored = StoredEvent(cursor, event)
            self._records.save(
                f"event_{cursor:020d}_{event.event_id}",
                {"cursor": cursor, "event": event.as_dict()},
                upsert=False,
            )
            return stored, False

    def get_events_after(
        self,
        cursor: int,
        *,
        scope: ScopeV1 | None = None,
        limit: int = 100,
        visible: Callable[[EventEnvelopeV1], bool] | None = None,
    ) -> list[StoredEvent]:
        if not isinstance(cursor, int) or cursor < 0:
            raise conflict("cursor must be a non-negative integer", code="STALE_CURSOR")
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise conflict("event limit must be between 1 and 500", code="VALIDATION_ERROR")
        with self._lock:
            latest = self.latest_cursor
            if cursor > latest:
                raise conflict("cursor is ahead of the server history", code="STALE_CURSOR")
            output: list[StoredEvent] = []
            for stored in self._all():
                if stored.cursor <= cursor:
                    continue
                if scope is not None and not scope.contains(stored.event.scope):
                    continue
                if visible is not None and not visible(stored.event):
                    continue
                output.append(stored)
                if len(output) >= limit:
                    break
            return output

    def get_event(self, event_id: str) -> StoredEvent | None:
        with self._lock:
            return next((item for item in self._all() if item.event.event_id == event_id), None)
