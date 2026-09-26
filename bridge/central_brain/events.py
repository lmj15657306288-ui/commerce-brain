"""In-memory authoritative task, alert and event state for fixture rehearsal."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping

from .contracts import (
    AlertV1,
    CentralEnvelopeV1,
    EventV1,
    TaskV1,
)


class EventStoreError(RuntimeError):
    """Raised when event ordering or idempotency rules are violated."""


@dataclass(frozen=True, slots=True)
class StoredEvent:
    cursor: int
    event: EventV1
    envelope: CentralEnvelopeV1


@dataclass(frozen=True, slots=True)
class MutationResult:
    value: Any
    envelope: CentralEnvelopeV1
    replayed: bool


class CentralState:
    """Small state authority used by Brain fixtures and local tests.

    A production deployment can replace this with a durable repository without
    changing the contracts or transport protocol.
    """

    def __init__(self) -> None:
        self.tasks: dict[str, TaskV1] = {}
        self.alerts: dict[str, AlertV1] = {}
        self._events: list[StoredEvent] = []
        self._task_idempotency: dict[str, MutationResult] = {}
        self._alert_idempotency: dict[str, MutationResult] = {}
        self._event_idempotency: dict[str, CentralEnvelopeV1] = {}
        self._aggregate_versions: dict[str, int] = {}
        self._lock = RLock()

    @property
    def cursor(self) -> int:
        with self._lock:
            return len(self._events)

    def _record(
        self,
        *,
        topic: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        origin_device_id: str,
        recipient_device_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> CentralEnvelopeV1:
        previous = self._aggregate_versions.get(aggregate_id, 0)
        event = EventV1.create(
            topic=topic,
            aggregate_id=aggregate_id,
            aggregate_version=previous + 1,
            origin_device_id=origin_device_id,
            payload=payload,
        )
        cursor = len(self._events) + 1
        envelope = CentralEnvelopeV1.create(
            message_type="event",
            sender_device_id=origin_device_id,
            recipient_device_id=recipient_device_id,
            session_id=session_id,
            event_id=event.event_id,
            idempotency_key=idempotency_key,
            cursor=cursor,
            payload={"event": event.as_dict(), "platform_write_attempted": False, "can_execute": False},
        )
        self._aggregate_versions[aggregate_id] = event.aggregate_version
        self._events.append(StoredEvent(cursor, event, envelope))
        self._event_idempotency[idempotency_key] = envelope
        return envelope

    def submit_task(
        self,
        task: TaskV1,
        *,
        origin_device_id: str,
        recipient_device_id: str,
        session_id: str,
    ) -> MutationResult:
        with self._lock:
            replay = self._task_idempotency.get(task.idempotency_key)
            if replay:
                return MutationResult(replay.value, replay.envelope, True)
            existing = self.tasks.get(task.task_id)
            if existing and existing != task:
                raise EventStoreError("task id conflicts with an existing task")
            self.tasks[task.task_id] = task
            envelope = self._record(
                topic="task.updated",
                aggregate_id=task.task_id,
                payload={"task": task.as_dict()},
                origin_device_id=origin_device_id,
                recipient_device_id=recipient_device_id,
                session_id=session_id,
                idempotency_key=task.idempotency_key,
            )
            result = MutationResult(task, envelope, False)
            self._task_idempotency[task.idempotency_key] = result
            return result

    def transition_task(
        self,
        task_id: str,
        status: str,
        *,
        origin_device_id: str,
        recipient_device_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        with self._lock:
            replay = self._task_idempotency.get(idempotency_key)
            if replay:
                return MutationResult(replay.value, replay.envelope, True)
            current = self.tasks[task_id]
            updated = current.transition(status)
            self.tasks[task_id] = updated
            envelope = self._record(
                topic="task.updated",
                aggregate_id=task_id,
                payload={"task": updated.as_dict()},
                origin_device_id=origin_device_id,
                recipient_device_id=recipient_device_id,
                session_id=session_id,
                idempotency_key=idempotency_key,
            )
            result = MutationResult(updated, envelope, False)
            self._task_idempotency[idempotency_key] = result
            return result

    def submit_alert(
        self,
        alert: AlertV1,
        *,
        origin_device_id: str,
        recipient_device_id: str,
        session_id: str,
    ) -> MutationResult:
        with self._lock:
            replay = self._alert_idempotency.get(alert.dedupe_key)
            if replay:
                return MutationResult(replay.value, replay.envelope, True)
            existing = self.alerts.get(alert.alert_id)
            if existing and existing != alert:
                raise EventStoreError("alert id conflicts with an existing alert")
            self.alerts[alert.alert_id] = alert
            envelope = self._record(
                topic="alert.updated",
                aggregate_id=alert.alert_id,
                payload={"alert": alert.as_dict()},
                origin_device_id=origin_device_id,
                recipient_device_id=recipient_device_id,
                session_id=session_id,
                idempotency_key=alert.dedupe_key,
            )
            result = MutationResult(alert, envelope, False)
            self._alert_idempotency[alert.dedupe_key] = result
            return result

    def transition_alert(
        self,
        alert_id: str,
        status: str,
        *,
        origin_device_id: str,
        recipient_device_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        with self._lock:
            replay = self._alert_idempotency.get(idempotency_key)
            if replay:
                return MutationResult(replay.value, replay.envelope, True)
            current = self.alerts[alert_id]
            updated = current.transition(status)
            self.alerts[alert_id] = updated
            envelope = self._record(
                topic="alert.updated",
                aggregate_id=alert_id,
                payload={"alert": updated.as_dict()},
                origin_device_id=origin_device_id,
                recipient_device_id=recipient_device_id,
                session_id=session_id,
                idempotency_key=idempotency_key,
            )
            result = MutationResult(updated, envelope, False)
            self._alert_idempotency[idempotency_key] = result
            return result

    def events_after(self, cursor: int) -> list[StoredEvent]:
        if not isinstance(cursor, int) or cursor < 0:
            raise EventStoreError("cursor must be non-negative")
        with self._lock:
            return list(self._events[cursor:])
