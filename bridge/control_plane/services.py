"""Control Plane services built on the Phase 2B core contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from threading import RLock
from typing import Any, Iterable

from core.contracts import (
    ActorV1,
    AlertV1,
    ApprovalRequestV1,
    BrainWorkerV1,
    ContractValidationError,
    DeviceSessionV1,
    EventEnvelopeV1,
    LeaseV1,
    ScopeV1,
    TaskV1,
)
from core.context_registry import ContextRegistry
from core.persistence import PersistenceAdapter

from .auth import RequestIdentity
from .clock import Clock, iso_now, parse_time
from .errors import (
    ControlPlaneError,
    conflict,
    forbidden,
    invalid_transition,
    not_found,
    scope_denied,
    unauthenticated,
    validation,
)
from .event_store import EventStore, StoredEvent
from .repositories import IdempotencyRepository, RecordRepository, fingerprint, paginate
from .websocket import EventBroker


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _contract_error(exc: ContractValidationError) -> ControlPlaneError:
    if exc.code in {"SCOPE_DENIED", "INVALID_SCOPE_LINEAGE"}:
        return scope_denied(str(exc))
    if exc.code == "INVALID_TRANSITION":
        return invalid_transition(str(exc))
    if exc.code in {"INVALID_TIMESTAMP", "INVALID_ENUM", "INVALID_FIELD", "INVALID_REF"}:
        return validation(str(exc), details={"contract_code": exc.code})
    return validation(str(exc), details={"contract_code": exc.code})


_PENDING_DELIVERIES: ContextVar[list[tuple[Any, Any, StoredEvent]] | None] = ContextVar(
    "control_plane_pending_deliveries",
    default=None,
)


def publish_after_commit(
    broker: EventBroker,
    event_store: EventStore,
    event: StoredEvent,
) -> None:
    pending = _PENDING_DELIVERIES.get()
    if pending is None:
        broker.publish_nowait(event)
    else:
        pending.append((broker, event_store, event))


def transactional_mutation(method):
    """Make each service mutation atomic and serialize its idempotency intent."""

    method_signature = signature(method)

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        bound = method_signature.bind_partial(self, *args, **kwargs)
        identity = bound.arguments.get("identity")
        idempotency_key = bound.arguments.get("idempotency_key")
        pending: list[tuple[Any, Any, StoredEvent]] = []
        token = _PENDING_DELIVERIES.set(pending)
        try:
            with self._lock:
                with self.adapter.transaction():
                    if idempotency_key and (identity is None or identity.authenticated):
                        self.adapter.lock_transaction_key(
                            f"{type(self).__name__}.{method.__name__}",
                            str(idempotency_key),
                        )
                    result = method(self, *args, **kwargs)
        except Exception:
            _PENDING_DELIVERIES.reset(token)
            for broker, event_store, stored in pending:
                if (
                    stored.event.event_type == "security.audit"
                    and stored.event.payload.get("result") == "DENIED"
                ):
                    try:
                        committed, replayed = event_store.append(stored.event)
                        if not replayed:
                            broker.publish_nowait(committed)
                    except Exception:
                        pass
            raise
        else:
            _PENDING_DELIVERIES.reset(token)
            for broker, _, stored in pending:
                broker.publish_nowait(stored)
            return result
        finally:
            if _PENDING_DELIVERIES.get() is pending:
                _PENDING_DELIVERIES.reset(token)

    return wrapped


@dataclass(frozen=True, slots=True)
class Mutation:
    value: dict[str, Any]
    event_cursors: tuple[int, ...] = ()
    replayed: bool = False
    deduped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "data": self.value,
            "event_cursors": list(self.event_cursors),
            "replayed": self.replayed,
            "deduped": self.deduped,
        }


class ServiceBase:
    def __init__(
        self,
        *,
        adapter: PersistenceAdapter,
        registry: ContextRegistry,
        events: EventStore,
        broker: EventBroker,
        clock: Clock,
        idempotency: IdempotencyRepository,
    ) -> None:
        self.adapter = adapter
        self.registry = registry
        self.events = events
        self.broker = broker
        self.clock = clock
        self.idempotency = idempotency
        self._lock = RLock()

    def _require_auth(self, identity: RequestIdentity) -> None:
        if not identity.authenticated:
            raise unauthenticated()
        if not identity.actor_id or not identity.organization_id:
            raise unauthenticated("authenticated identity is incomplete")

    def _actor(self, identity: RequestIdentity) -> ActorV1:
        actor = self.registry.get_actor(identity.actor_id)
        if actor is None:
            raise forbidden("actor is not registered in Context Registry")
        if identity.organization_id != actor.scope.organization_id:
            raise scope_denied("identity organization does not match actor scope")
        return actor

    def _authorize(
        self,
        identity: RequestIdentity,
        scope: ScopeV1,
        capability: str,
        *,
        operation: str,
        target_type: str,
        target_id: str,
        allow_archived: bool = False,
    ) -> ActorV1:
        self._require_auth(identity)
        actor = self._actor(identity)
        if scope.organization_id != identity.organization_id:
            self._audit_denied(
                identity,
                actor.scope,
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                reason_code="CROSS_ORGANIZATION",
                requested_scope=scope,
            )
            raise scope_denied("cross-organization access is denied")
        try:
            exists = self.registry.scope_exists(scope, include_archived=allow_archived)
            allowed = self.registry.actor_can_access(
                identity.actor_id,
                scope,
                capability,
                include_archived=allow_archived,
            )
        except TypeError:
            exists = self.registry.scope_exists(scope, include_archived=allow_archived)
            allowed = self.registry.actor_can_access(identity.actor_id, scope, capability)
        if not exists or not allowed:
            self._audit_denied(
                identity,
                actor.scope,
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                reason_code="CAPABILITY_OR_SCOPE_DENIED",
                requested_scope=scope,
            )
            raise scope_denied()
        return actor

    def _audit_denied(
        self,
        identity: RequestIdentity,
        scope: ScopeV1,
        *,
        operation: str,
        target_type: str,
        target_id: str,
        reason_code: str,
        requested_scope: ScopeV1 | None = None,
    ) -> None:
        if not identity.authenticated:
            return
        actor = self.registry.get_actor(identity.actor_id)
        if actor is None or not actor.scope.contains(scope):
            return
        payload: dict[str, Any] = {
            "actor_id": identity.actor_id,
            "device_id": identity.device_id,
            "scope": scope.as_dict(),
            "operation": operation,
            "target_type": target_type,
            "target_id": target_id,
            "timestamp": iso_now(self.clock),
            "result": "DENIED",
            "reason_code": reason_code,
            "can_execute": False,
            "platform_write_attempted": False,
        }
        if requested_scope is not None:
            payload["requested_scope"] = requested_scope.as_dict()
        try:
            self._append_event(
                identity,
                actor,
                event_type="security.audit",
                scope=scope,
                payload=payload,
                idempotency_key=_new_id("audit"),
                publish=True,
            )
        except Exception:
            # The original authorization failure remains authoritative.
            return

    def _append_event(
        self,
        identity: RequestIdentity,
        actor: ActorV1,
        *,
        event_type: str,
        scope: ScopeV1,
        payload: dict[str, Any],
        idempotency_key: str,
        publish: bool,
    ) -> StoredEvent:
        now = iso_now(self.clock)
        event = EventEnvelopeV1.create(
            event_id=_new_id("event"),
            event_type=event_type,
            scope=scope,
            actor=actor,
            source="control_plane",
            occurred_at=now,
            received_at=now,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        stored, replayed = self.events.append(event)
        if publish and not replayed:
            publish_after_commit(self.broker, self.events, stored)
        return stored

    def _append_audit(
        self,
        identity: RequestIdentity,
        actor: ActorV1,
        *,
        scope: ScopeV1,
        operation: str,
        target_type: str,
        target_id: str,
        result: str = "ACCEPTED",
        reason_code: str | None = None,
    ) -> StoredEvent:
        payload: dict[str, Any] = {
            "actor_id": identity.actor_id,
            "device_id": identity.device_id,
            "scope": scope.as_dict(),
            "operation": operation,
            "target_type": target_type,
            "target_id": target_id,
            "timestamp": iso_now(self.clock),
            "result": result,
            "can_execute": False,
            "platform_write_attempted": False,
        }
        if reason_code is not None:
            payload["reason_code"] = reason_code
        return self._append_event(
            identity,
            actor,
            event_type="security.audit",
            scope=scope,
            payload=payload,
            idempotency_key=_new_id("audit"),
            publish=True,
        )

    def _mutation(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: Any,
        value: dict[str, Any],
        events: Iterable[StoredEvent],
        replayed: bool = False,
        deduped: bool = False,
    ) -> Mutation:
        cursors = tuple(item.cursor for item in events)
        result = Mutation(
            value=value,
            event_cursors=cursors,
            replayed=replayed,
            deduped=deduped,
        )
        if not replayed:
            self.idempotency.save_result(
                kind=kind,
                key=idempotency_key,
                payload=payload,
                result=result.as_dict(),
            )
        return result

    def _replay(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: Any,
    ) -> Mutation | None:
        raw = self.idempotency.get(kind, idempotency_key)
        if raw is None:
            return None
        from_saved = raw.get("result")
        if raw.get("payload_hash") is None:
            return None
        # reserve_or_replay performs the canonical payload comparison.
        replayed = self.idempotency.reserve_or_replay(
            kind=kind,
            key=idempotency_key,
            payload=payload,
        )
        if not isinstance(replayed, dict):
            return None
        return Mutation(
            value=dict(replayed.get("data") or {}),
            event_cursors=tuple(replayed.get("event_cursors") or ()),
            replayed=True,
            deduped=bool(replayed.get("deduped", False)),
        )

    def _save_contract(self, collection: str, record_id: str, value: dict[str, Any]) -> None:
        RecordRepository(self.adapter, collection).save(record_id, value)


class TaskService(ServiceBase):
    collection = "cp_tasks"

    @transactional_mutation
    def create(
        self,
        identity: RequestIdentity,
        *,
        scope: ScopeV1,
        task_type: str,
        priority: int,
        owner: str | None,
        due_at: str | None,
        business_impact: dict[str, Any] | None,
        source_refs: list[str],
        idempotency_key: str,
    ) -> Mutation:
        payload = {
            "scope": scope.as_dict(),
            "task_type": task_type,
            "priority": priority,
            "owner": owner,
            "due_at": due_at,
            "business_impact": business_impact,
            "source_refs": source_refs,
        }
        actor = self._authorize(
            identity,
            scope,
            "task.create",
            operation="task.create",
            target_type="task",
            target_id="pending",
        )
        replay = self._replay(kind="task.create", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        if owner is not None:
            assignee = self.registry.get_actor(owner)
            if assignee is None or not self.registry.actor_can_access(owner, scope, "task.read"):
                raise scope_denied("task owner cannot access the task scope")
        task = TaskV1.create(
            task_id=_new_id("task"),
            scope=scope,
            task_type=task_type,
            priority=priority,
            status="PENDING",
            owner=owner,
            created_by=identity.actor_id,
            created_at=iso_now(self.clock),
            updated_at=iso_now(self.clock),
            due_at=due_at,
            business_impact=business_impact,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
        )
        event = self._append_event(
            identity,
            actor,
            event_type="task.created",
            scope=scope,
            payload={
                "task": task.as_dict(),
                "can_execute": False,
                "platform_write_attempted": False,
            },
            idempotency_key=f"event:{idempotency_key}",
            publish=False,
        )
        self._save_contract(self.collection, task.task_id, task.as_dict())
        audit = self._append_audit(
            identity,
            actor,
            scope=scope,
            operation="task.create",
            target_type="task",
            target_id=task.task_id,
        )
        publish_after_commit(self.broker, self.events, event)
        return self._mutation(
            kind="task.create",
            idempotency_key=idempotency_key,
            payload=payload,
            value=task.as_dict(),
            events=(event, audit),
        )

    def get(self, identity: RequestIdentity, task_id: str) -> dict[str, Any]:
        raw = RecordRepository(self.adapter, self.collection).get(task_id)
        if raw is None:
            raise not_found("task was not found")
        try:
            task = TaskV1.from_mapping(raw)
        except ContractValidationError as exc:
            raise _contract_error(exc) from exc
        self._authorize(
            identity,
            task.scope,
            "task.read",
            operation="task.read",
            target_type="task",
            target_id=task_id,
            allow_archived=True,
        )
        return task.as_dict()

    def list(
        self,
        identity: RequestIdentity,
        *,
        scope: ScopeV1,
        status: str | None,
        created_after: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        self._authorize(
            identity,
            scope,
            "task.read",
            operation="task.list",
            target_type="task",
            target_id="collection",
            allow_archived=True,
        )
        created_after_time = None
        if created_after is not None:
            try:
                created_after_time = parse_time(created_after)
            except ValueError as exc:
                raise validation("created_after must be an ISO-8601 timestamp") from exc
        tasks: list[dict[str, Any]] = []
        for raw in RecordRepository(self.adapter, self.collection).list():
            try:
                task = TaskV1.from_mapping(raw)
            except ContractValidationError:
                continue
            if status is not None and task.status != status.upper():
                continue
            if created_after_time is not None and parse_time(task.created_at) <= created_after_time:
                continue
            if not scope.contains(task.scope):
                continue
            try:
                self._authorize(
                    identity,
                    task.scope,
                    "task.read",
                    operation="task.read",
                    target_type="task",
                    target_id=task.task_id,
                    allow_archived=True,
                )
            except ControlPlaneError:
                continue
            tasks.append(task.as_dict())
        tasks.sort(key=lambda item: item["updated_at"], reverse=True)
        return paginate(tasks, limit=limit, offset=offset)

    @transactional_mutation
    def transition(
        self,
        identity: RequestIdentity,
        *,
        task_id: str,
        next_status: str,
        idempotency_key: str,
        assignee_id: str | None = None,
        reason: str | None = None,
    ) -> Mutation:
        payload = {
            "task_id": task_id,
            "next_status": next_status,
            "assignee_id": assignee_id,
            "reason": reason,
        }
        self._require_auth(identity)
        repo = RecordRepository(self.adapter, self.collection)
        raw = repo.get(task_id)
        if raw is None:
            raise not_found("task was not found")
        task = TaskV1.from_mapping(raw)
        capability = "task.assign" if next_status.upper() == "ASSIGNED" else "task.update"
        actor = self._authorize(
            identity,
            task.scope,
            capability,
            operation=f"task.{next_status.lower()}",
            target_type="task",
            target_id=task_id,
        )
        replay = self._replay(kind="task.transition", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("task.transition", task_id)
        raw = repo.get(task_id)
        if raw is None:
            raise not_found("task was not found")
        task = TaskV1.from_mapping(raw)
        actor = self._authorize(
            identity,
            task.scope,
            capability,
            operation=f"task.{next_status.lower()}",
            target_type="task",
            target_id=task_id,
        )
        if next_status.upper() == "ASSIGNED":
            if not assignee_id:
                raise validation("assignee_id is required when assigning a task")
            assignee = self.registry.get_actor(assignee_id)
            if assignee is None or not self.registry.actor_can_access(assignee_id, task.scope, "task.read"):
                raise scope_denied("assignee cannot access the task scope")
        try:
            transitioned = task.transition(next_status)
        except ContractValidationError as exc:
            raise _contract_error(exc) from exc
        updated = TaskV1.create(
            task_id=task.task_id,
            scope=task.scope,
            task_type=task.task_type,
            priority=task.priority,
            status=transitioned.status,
            owner=assignee_id if next_status.upper() == "ASSIGNED" else task.owner,
            created_by=task.created_by,
            created_at=task.created_at,
            updated_at=iso_now(self.clock),
            due_at=task.due_at,
            business_impact=task.business_impact,
            source_refs=list(task.source_refs),
            idempotency_key=task.idempotency_key,
        )
        event = self._append_event(
            identity,
            actor,
            event_type="task.updated",
            scope=task.scope,
            payload={
                "task": updated.as_dict(),
                "transition": {"from": task.status, "to": updated.status, "reason": reason},
                "can_execute": False,
                "platform_write_attempted": False,
            },
            idempotency_key=f"event:{idempotency_key}",
            publish=False,
        )
        repo.save(task_id, updated.as_dict())
        audit = self._append_audit(
            identity,
            actor,
            scope=task.scope,
            operation=f"task.{next_status.lower()}",
            target_type="task",
            target_id=task_id,
        )
        publish_after_commit(self.broker, self.events, event)
        return self._mutation(
            kind="task.transition",
            idempotency_key=idempotency_key,
            payload=payload,
            value=updated.as_dict(),
            events=(event, audit),
        )


class AlertService(ServiceBase):
    collection = "cp_alerts"

    @transactional_mutation
    def create(
        self,
        identity: RequestIdentity,
        *,
        scope: ScopeV1,
        priority: str,
        reason_code: str,
        summary: str,
        evidence_refs: list[str],
        business_impact: dict[str, Any] | None,
        recommended_action: str,
        dedupe_key: str,
        cooldown_until: str | None,
        idempotency_key: str,
    ) -> Mutation:
        payload = {
            "scope": scope.as_dict(),
            "priority": priority,
            "reason_code": reason_code,
            "summary": summary,
            "evidence_refs": evidence_refs,
            "business_impact": business_impact,
            "recommended_action": recommended_action,
            "dedupe_key": dedupe_key,
            "cooldown_until": cooldown_until,
        }
        actor = self._authorize(
            identity,
            scope,
            "alert.create",
            operation="alert.create",
            target_type="alert",
            target_id="pending",
        )
        replay = self._replay(kind="alert.create", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key(
            "alert.dedupe",
            fingerprint(
                {
                    "scope": scope.as_dict(),
                    "reason_code": reason_code,
                    "dedupe_key": dedupe_key,
                }
            ),
        )
        now = self.clock.now()
        for raw in RecordRepository(self.adapter, self.collection).list():
            existing = AlertV1.from_mapping(raw)
            if (
                existing.scope.matches(scope)
                and existing.reason_code == reason_code
                and existing.dedupe_key == dedupe_key
                and existing.cooldown_until is not None
                and parse_time(existing.cooldown_until) > now
                and existing.status not in {"RESOLVED", "EXPIRED", "CANCELLED"}
            ):
                return self._mutation(
                    kind="alert.create",
                    idempotency_key=idempotency_key,
                    payload=payload,
                    value=existing.as_dict(),
                    events=(),
                    deduped=True,
                )
        alert = AlertV1.create(
            alert_id=_new_id("alert"),
            scope=scope,
            priority=priority,
            reason_code=reason_code,
            summary=summary,
            evidence_refs=evidence_refs,
            business_impact=business_impact,
            recommended_action=recommended_action,
            dedupe_key=dedupe_key,
            cooldown_until=cooldown_until,
            created_at=iso_now(self.clock),
            updated_at=iso_now(self.clock),
        )
        event = self._append_event(
            identity,
            actor,
            event_type="alert.created",
            scope=scope,
            payload={"alert": alert.as_dict(), "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=False,
        )
        RecordRepository(self.adapter, self.collection).save(alert.alert_id, alert.as_dict())
        audit = self._append_audit(
            identity,
            actor,
            scope=scope,
            operation="alert.create",
            target_type="alert",
            target_id=alert.alert_id,
        )
        publish_after_commit(self.broker, self.events, event)
        return self._mutation(
            kind="alert.create",
            idempotency_key=idempotency_key,
            payload=payload,
            value=alert.as_dict(),
            events=(event, audit),
        )

    def get(self, identity: RequestIdentity, alert_id: str) -> dict[str, Any]:
        raw = RecordRepository(self.adapter, self.collection).get(alert_id)
        if raw is None:
            raise not_found("alert was not found")
        alert = AlertV1.from_mapping(raw)
        self._authorize(
            identity,
            alert.scope,
            "alert.read",
            operation="alert.read",
            target_type="alert",
            target_id=alert_id,
            allow_archived=True,
        )
        return alert.as_dict()

    def list(
        self,
        identity: RequestIdentity,
        *,
        scope: ScopeV1,
        status: str | None,
        priority: str | None,
        created_after: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        self._authorize(
            identity,
            scope,
            "alert.read",
            operation="alert.list",
            target_type="alert",
            target_id="collection",
            allow_archived=True,
        )
        created_after_time = None
        if created_after is not None:
            try:
                created_after_time = parse_time(created_after)
            except ValueError as exc:
                raise validation("created_after must be an ISO-8601 timestamp") from exc
        alerts: list[dict[str, Any]] = []
        for raw in RecordRepository(self.adapter, self.collection).list():
            alert = AlertV1.from_mapping(raw)
            if status and alert.status != status.upper():
                continue
            if priority and alert.priority != priority.upper():
                continue
            if created_after_time is not None and parse_time(alert.created_at) <= created_after_time:
                continue
            if not scope.contains(alert.scope):
                continue
            try:
                self._authorize(
                    identity,
                    alert.scope,
                    "alert.read",
                    operation="alert.read",
                    target_type="alert",
                    target_id=alert.alert_id,
                    allow_archived=True,
                )
            except ControlPlaneError:
                continue
            alerts.append(alert.as_dict())
        alerts.sort(key=lambda item: item["updated_at"], reverse=True)
        return paginate(alerts, limit=limit, offset=offset)

    @transactional_mutation
    def transition(
        self,
        identity: RequestIdentity,
        *,
        alert_id: str,
        next_status: str,
        idempotency_key: str,
    ) -> Mutation:
        payload = {"alert_id": alert_id, "next_status": next_status}
        self._require_auth(identity)
        repo = RecordRepository(self.adapter, self.collection)
        raw = repo.get(alert_id)
        if raw is None:
            raise not_found("alert was not found")
        alert = AlertV1.from_mapping(raw)
        actor = self._authorize(
            identity,
            alert.scope,
            "alert.update",
            operation=f"alert.{next_status.lower()}",
            target_type="alert",
            target_id=alert_id,
        )
        replay = self._replay(kind="alert.transition", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("alert.transition", alert_id)
        raw = repo.get(alert_id)
        if raw is None:
            raise not_found("alert was not found")
        alert = AlertV1.from_mapping(raw)
        actor = self._authorize(
            identity,
            alert.scope,
            "alert.update",
            operation=f"alert.{next_status.lower()}",
            target_type="alert",
            target_id=alert_id,
        )
        try:
            transitioned = alert.transition(next_status)
        except ContractValidationError as exc:
            raise _contract_error(exc) from exc
        updated = AlertV1.create(
            alert_id=alert.alert_id,
            scope=alert.scope,
            priority=alert.priority,
            status=transitioned.status,
            reason_code=alert.reason_code,
            summary=alert.summary,
            evidence_refs=list(alert.evidence_refs),
            business_impact=alert.business_impact,
            recommended_action=alert.recommended_action,
            created_at=alert.created_at,
            updated_at=iso_now(self.clock),
            dedupe_key=alert.dedupe_key,
            cooldown_until=alert.cooldown_until,
        )
        event = self._append_event(
            identity,
            actor,
            event_type="alert.updated",
            scope=alert.scope,
            payload={"alert": updated.as_dict(), "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=False,
        )
        repo.save(alert_id, updated.as_dict())
        audit = self._append_audit(
            identity,
            actor,
            scope=alert.scope,
            operation=f"alert.{next_status.lower()}",
            target_type="alert",
            target_id=alert_id,
        )
        publish_after_commit(self.broker, self.events, event)
        return self._mutation(
            kind="alert.transition",
            idempotency_key=idempotency_key,
            payload=payload,
            value=updated.as_dict(),
            events=(event, audit),
        )


class ApprovalService(ServiceBase):
    collection = "cp_approvals"

    @transactional_mutation
    def create(
        self,
        identity: RequestIdentity,
        *,
        scope: ScopeV1,
        proposal_id: str,
        risk_level: str,
        expires_at: str,
        reason: str,
        idempotency_key: str,
    ) -> Mutation:
        payload = {
            "scope": scope.as_dict(),
            "proposal_id": proposal_id,
            "risk_level": risk_level,
            "expires_at": expires_at,
            "reason": reason,
        }
        actor = self._authorize(
            identity,
            scope,
            "approval.create",
            operation="approval.create",
            target_type="approval",
            target_id="pending",
        )
        replay = self._replay(kind="approval.create", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        approval = ApprovalRequestV1.create(
            approval_id=_new_id("approval"),
            scope=scope,
            proposal_id=proposal_id,
            risk_level=risk_level,
            requested_by=identity.actor_id,
            requested_at=iso_now(self.clock),
            expires_at=expires_at,
            reason=reason,
        )
        event = self._append_event(
            identity,
            actor,
            event_type="approval.created",
            scope=scope,
            payload={
                "approval": approval.as_dict(),
                "approval_is_execution": False,
                "can_execute": False,
                "platform_write_attempted": False,
            },
            idempotency_key=f"event:{idempotency_key}",
            publish=False,
        )
        RecordRepository(self.adapter, self.collection).save(approval.approval_id, approval.as_dict())
        audit = self._append_audit(
            identity,
            actor,
            scope=scope,
            operation="approval.create",
            target_type="approval",
            target_id=approval.approval_id,
        )
        publish_after_commit(self.broker, self.events, event)
        return self._mutation(
            kind="approval.create",
            idempotency_key=idempotency_key,
            payload=payload,
            value=approval.as_dict(),
            events=(event, audit),
        )

    def get(self, identity: RequestIdentity, approval_id: str) -> dict[str, Any]:
        raw = RecordRepository(self.adapter, self.collection).get(approval_id)
        if raw is None:
            raise not_found("approval was not found")
        approval = ApprovalRequestV1.from_mapping(raw)
        self._authorize(
            identity,
            approval.scope,
            "approval.read",
            operation="approval.read",
            target_type="approval",
            target_id=approval_id,
            allow_archived=True,
        )
        return approval.as_dict()

    def list(
        self,
        identity: RequestIdentity,
        *,
        scope: ScopeV1,
        status: str | None,
        created_after: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        self._authorize(
            identity,
            scope,
            "approval.read",
            operation="approval.list",
            target_type="approval",
            target_id="collection",
            allow_archived=True,
        )
        created_after_time = None
        if created_after is not None:
            try:
                created_after_time = parse_time(created_after)
            except ValueError as exc:
                raise validation("created_after must be an ISO-8601 timestamp") from exc
        values: list[dict[str, Any]] = []
        for raw in RecordRepository(self.adapter, self.collection).list():
            approval = ApprovalRequestV1.from_mapping(raw)
            if status and approval.status != status.upper():
                continue
            if created_after_time is not None and parse_time(approval.requested_at) <= created_after_time:
                continue
            if not scope.contains(approval.scope):
                continue
            try:
                self._authorize(
                    identity,
                    approval.scope,
                    "approval.read",
                    operation="approval.read",
                    target_type="approval",
                    target_id=approval.approval_id,
                    allow_archived=True,
                )
            except ControlPlaneError:
                continue
            values.append(approval.as_dict())
        values.sort(key=lambda item: item["requested_at"], reverse=True)
        return paginate(values, limit=limit, offset=offset)

    @transactional_mutation
    def decide(
        self,
        identity: RequestIdentity,
        *,
        approval_id: str,
        next_status: str,
        idempotency_key: str,
        decision_note: str | None,
    ) -> Mutation:
        payload = {
            "approval_id": approval_id,
            "next_status": next_status,
            "decision_note": decision_note,
        }
        self._require_auth(identity)
        repo = RecordRepository(self.adapter, self.collection)
        raw = repo.get(approval_id)
        if raw is None:
            raise not_found("approval was not found")
        approval = ApprovalRequestV1.from_mapping(raw)
        actor = self._authorize(
            identity,
            approval.scope,
            "approval.decide",
            operation=f"approval.{next_status.lower()}",
            target_type="approval",
            target_id=approval_id,
        )
        replay = self._replay(kind="approval.transition", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("approval.transition", approval_id)
        raw = repo.get(approval_id)
        if raw is None:
            raise not_found("approval was not found")
        approval = ApprovalRequestV1.from_mapping(raw)
        actor = self._authorize(
            identity,
            approval.scope,
            "approval.decide",
            operation=f"approval.{next_status.lower()}",
            target_type="approval",
            target_id=approval_id,
        )
        if approval.status != "PENDING":
            raise invalid_transition(f"approval cannot transition from {approval.status} to {next_status.upper()}")
        next_status = next_status.upper()
        allowed = {"APPROVED", "REJECTED", "REVISION_REQUESTED", "CANCELLED", "EXPIRED"}
        if next_status not in allowed:
            raise validation("unsupported approval decision")
        decided_at = iso_now(self.clock)
        updated = ApprovalRequestV1.create(
            approval_id=approval.approval_id,
            scope=approval.scope,
            proposal_id=approval.proposal_id,
            risk_level=approval.risk_level,
            requested_by=approval.requested_by,
            requested_at=approval.requested_at,
            expires_at=approval.expires_at,
            reason=approval.reason,
            status=next_status,
            decided_by=identity.actor_id,
            decided_at=decided_at,
            decision_note=decision_note,
        )
        event = self._append_event(
            identity,
            actor,
            event_type="approval.updated",
            scope=approval.scope,
            payload={
                "approval": updated.as_dict(),
                "approval_is_execution": False,
                "can_execute": False,
                "platform_write_attempted": False,
            },
            idempotency_key=f"event:{idempotency_key}",
            publish=False,
        )
        repo.save(approval_id, updated.as_dict())
        audit = self._append_audit(
            identity,
            actor,
            scope=approval.scope,
            operation=f"approval.{next_status.lower()}",
            target_type="approval",
            target_id=approval_id,
        )
        publish_after_commit(self.broker, self.events, event)
        return self._mutation(
            kind="approval.transition",
            idempotency_key=idempotency_key,
            payload=payload,
            value=updated.as_dict(),
            events=(event, audit),
        )

    def expire_due(self, identity: RequestIdentity) -> int:
        count = 0
        for raw in RecordRepository(self.adapter, self.collection).list():
            approval = ApprovalRequestV1.from_mapping(raw)
            if approval.status == "PENDING" and parse_time(approval.expires_at) <= self.clock.now():
                self.decide(
                    identity,
                    approval_id=approval.approval_id,
                    next_status="EXPIRED",
                    idempotency_key=_new_id("expire"),
                    decision_note="server expiration",
                )
                count += 1
        return count


class DeviceSessionService(ServiceBase):
    collection = "cp_device_sessions"

    @transactional_mutation
    def register(
        self,
        identity: RequestIdentity,
        *,
        device_id: str,
        device_type: str,
        user_id: str | None,
        organization_id: str,
        shop_ids: list[str],
        capabilities: list[str],
        ttl_seconds: int,
        idempotency_key: str,
    ) -> Mutation:
        payload = {
            "device_id": device_id,
            "device_type": device_type,
            "user_id": user_id,
            "organization_id": organization_id,
            "shop_ids": shop_ids,
            "capabilities": capabilities,
            "ttl_seconds": ttl_seconds,
        }
        scope = ScopeV1.create(organization_id=organization_id)
        actor = self._authorize(
            identity,
            scope,
            "device.session",
            operation="device.session.open",
            target_type="device",
            target_id=device_id,
        )
        for shop_id in shop_ids:
            self._authorize(
                identity,
                ScopeV1.create(organization_id=organization_id, shop_id=shop_id),
                "device.session",
                operation="device.session.open",
                target_type="device",
                target_id=device_id,
            )
        replay = self._replay(kind="device.session", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("device.identity", device_id)
        existing = self.registry.get_device(device_id)
        if existing is not None and existing.status == "REVOKED":
            raise conflict("revoked device cannot be reopened", code="FORBIDDEN")
        now = self.clock.now()
        device = DeviceSessionV1.create(
            device_id=device_id,
            device_type=device_type,
            user_id=user_id or identity.actor_id,
            organization_id=organization_id,
            shop_ids=shop_ids,
            capabilities=capabilities,
            status="ACTIVE",
            connected_at=iso_now(self.clock),
            last_seen_at=iso_now(self.clock),
        )
        self.registry.register(device, upsert=True)
        session_id = _new_id("session")
        value = {
            "session_id": session_id,
            "issued_at": iso_now(self.clock),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "ack_cursor": 0,
            "device": device.as_dict(),
        }
        RecordRepository(self.adapter, self.collection).save(
            device_id,
            value,
            indexes={"organization_id": organization_id, "actor_id": device.user_id},
        )
        event = self._append_event(
            identity,
            actor,
            event_type="device.session.opened",
            scope=scope,
            payload={"device_session": value, "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=False,
        )
        audit = self._append_audit(
            identity,
            actor,
            scope=scope,
            operation="device.session.open",
            target_type="device",
            target_id=device_id,
        )
        publish_after_commit(self.broker, self.events, event)
        return self._mutation(
            kind="device.session",
            idempotency_key=idempotency_key,
            payload=payload,
            value=value,
            events=(event, audit),
        )

    def _get(self, device_id: str) -> dict[str, Any]:
        raw = RecordRepository(self.adapter, self.collection).get(device_id)
        if raw is None:
            raise not_found("device session was not found")
        return raw

    def get(self, identity: RequestIdentity, device_id: str) -> dict[str, Any]:
        raw = self._get(device_id)
        device = DeviceSessionV1.from_mapping(raw["device"])
        self._authorize(
            identity,
            ScopeV1.create(organization_id=device.organization_id),
            "device.heartbeat",
            operation="device.session.read",
            target_type="device",
            target_id=device_id,
            allow_archived=True,
        )
        return raw

    @transactional_mutation
    def heartbeat(self, identity: RequestIdentity, device_id: str, *, idempotency_key: str) -> Mutation:
        payload = {"device_id": device_id}
        self._require_auth(identity)
        raw = self._get(device_id)
        current = DeviceSessionV1.from_mapping(raw["device"])
        if current.status == "REVOKED":
            raise forbidden("revoked device cannot heartbeat")
        actor = self._authorize(
            identity,
            ScopeV1.create(organization_id=current.organization_id),
            "device.heartbeat",
            operation="device.heartbeat",
            target_type="device",
            target_id=device_id,
        )
        replay = self._replay(kind="device.heartbeat", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("device.identity", device_id)
        raw = self._get(device_id)
        current = DeviceSessionV1.from_mapping(raw["device"])
        if current.status == "REVOKED":
            raise forbidden("revoked device cannot heartbeat")
        updated_device = DeviceSessionV1.create(
            device_id=current.device_id,
            device_type=current.device_type,
            user_id=current.user_id,
            organization_id=current.organization_id,
            shop_ids=list(current.shop_ids),
            capabilities=list(current.capabilities),
            status="ACTIVE",
            connected_at=current.connected_at,
            last_seen_at=iso_now(self.clock),
        )
        raw["device"] = updated_device.as_dict()
        RecordRepository(self.adapter, self.collection).save(
            device_id,
            raw,
            indexes={"organization_id": current.organization_id, "actor_id": current.user_id},
        )
        self.registry.register(updated_device, upsert=True)
        event = self._append_event(
            identity,
            actor,
            event_type="device.session.heartbeat",
            scope=ScopeV1.create(organization_id=current.organization_id),
            payload={"device_session": raw, "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=True,
        )
        audit = self._append_audit(
            identity,
            actor,
            scope=ScopeV1.create(organization_id=current.organization_id),
            operation="device.heartbeat",
            target_type="device",
            target_id=device_id,
        )
        return self._mutation(
            kind="device.heartbeat",
            idempotency_key=idempotency_key,
            payload=payload,
            value=raw,
            events=(event, audit),
        )

    def disconnect(self, identity: RequestIdentity, device_id: str, *, idempotency_key: str) -> Mutation:
        return self._set_status(identity, device_id, "DISCONNECTED", idempotency_key)

    def revoke(self, identity: RequestIdentity, device_id: str, *, idempotency_key: str) -> Mutation:
        return self._set_status(identity, device_id, "REVOKED", idempotency_key)

    def expire_due(self, identity: RequestIdentity) -> int:
        count = 0
        repo = RecordRepository(self.adapter, self.collection)
        for raw in repo.list():
            if raw.get("device", {}).get("status") != "ACTIVE":
                continue
            if parse_time(raw["expires_at"]) <= self.clock.now():
                self._set_status(identity, raw["device"]["device_id"], "EXPIRED", _new_id("expire"))
                count += 1
        return count

    @transactional_mutation
    def _set_status(self, identity: RequestIdentity, device_id: str, status: str, idempotency_key: str) -> Mutation:
        payload = {"device_id": device_id, "status": status}
        self._require_auth(identity)
        raw = self._get(device_id)
        current = DeviceSessionV1.from_mapping(raw["device"])
        actor = self._authorize(
            identity,
            ScopeV1.create(organization_id=current.organization_id),
            "device.heartbeat",
            operation=f"device.{status.lower()}",
            target_type="device",
            target_id=device_id,
        )
        replay = self._replay(kind=f"device.{status.lower()}", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("device.identity", device_id)
        raw = self._get(device_id)
        current = DeviceSessionV1.from_mapping(raw["device"])
        updated = DeviceSessionV1.create(
            device_id=current.device_id,
            device_type=current.device_type,
            user_id=current.user_id,
            organization_id=current.organization_id,
            shop_ids=list(current.shop_ids),
            capabilities=list(current.capabilities),
            status=status,
            connected_at=current.connected_at,
            last_seen_at=current.last_seen_at,
        )
        raw["device"] = updated.as_dict()
        RecordRepository(self.adapter, self.collection).save(
            device_id,
            raw,
            indexes={"organization_id": current.organization_id, "actor_id": current.user_id},
        )
        self.registry.register(updated, upsert=True)
        event = self._append_event(
            identity,
            actor,
            event_type="device.session.updated",
            scope=ScopeV1.create(organization_id=current.organization_id),
            payload={"device_session": raw, "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=True,
        )
        audit = self._append_audit(
            identity,
            actor,
            scope=ScopeV1.create(organization_id=current.organization_id),
            operation=f"device.{status.lower()}",
            target_type="device",
            target_id=device_id,
        )
        return self._mutation(
            kind=f"device.{status.lower()}",
            idempotency_key=idempotency_key,
            payload=payload,
            value=raw,
            events=(event, audit),
        )

    def ensure_active(self, identity: RequestIdentity, device_id: str) -> dict[str, Any]:
        raw = self._get(device_id)
        device = DeviceSessionV1.from_mapping(raw["device"])
        if parse_time(raw["expires_at"]) <= self.clock.now():
            if device.status == "ACTIVE":
                device = DeviceSessionV1.create(
                    device_id=device.device_id,
                    device_type=device.device_type,
                    user_id=device.user_id,
                    organization_id=device.organization_id,
                    shop_ids=list(device.shop_ids),
                    capabilities=list(device.capabilities),
                    status="EXPIRED",
                    connected_at=device.connected_at,
                    last_seen_at=device.last_seen_at,
                )
                raw["device"] = device.as_dict()
                RecordRepository(self.adapter, self.collection).save(
                    device_id,
                    raw,
                    indexes={"organization_id": device.organization_id, "actor_id": device.user_id},
                )
        if device.status != "ACTIVE":
            raise forbidden("device session is not active")
        if identity.device_id is not None and identity.device_id != device_id:
            raise forbidden("identity device does not match the session")
        return raw


class WorkerService(ServiceBase):
    collection = "cp_workers"
    degraded_after_seconds = 30
    offline_after_seconds = 120

    @transactional_mutation
    def register(
        self,
        identity: RequestIdentity,
        *,
        worker_id: str,
        worker_type: str,
        capabilities: list[str],
        organization_id: str,
        idempotency_key: str,
    ) -> Mutation:
        payload = {
            "worker_id": worker_id,
            "worker_type": worker_type,
            "capabilities": capabilities,
            "organization_id": organization_id,
        }
        scope = ScopeV1.create(organization_id=organization_id)
        actor = self._authorize(
            identity,
            scope,
            "worker.heartbeat",
            operation="worker.register",
            target_type="worker",
            target_id=worker_id,
        )
        replay = self._replay(kind="worker.register", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("worker.identity", worker_id)
        worker = BrainWorkerV1.create(
            worker_id=worker_id,
            worker_type=worker_type,
            capabilities=capabilities,
            status="ONLINE",
            heartbeat_at=iso_now(self.clock),
            last_seen_at=iso_now(self.clock),
        )
        value = {"organization_id": organization_id, "worker": worker.as_dict()}
        RecordRepository(self.adapter, self.collection).save(
            worker_id,
            value,
            indexes={"organization_id": organization_id},
        )
        self.registry.register(worker, upsert=True)
        event = self._append_event(
            identity,
            actor,
            event_type="worker.registered",
            scope=scope,
            payload={"worker": value, "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=True,
        )
        audit = self._append_audit(
            identity,
            actor,
            scope=scope,
            operation="worker.register",
            target_type="worker",
            target_id=worker_id,
        )
        return self._mutation(
            kind="worker.register",
            idempotency_key=idempotency_key,
            payload=payload,
            value=value,
            events=(event, audit),
        )

    @transactional_mutation
    def heartbeat(self, identity: RequestIdentity, worker_id: str, *, idempotency_key: str) -> Mutation:
        payload = {"worker_id": worker_id}
        self._require_auth(identity)
        raw = RecordRepository(self.adapter, self.collection).get(worker_id)
        if raw is None:
            raise not_found("worker was not found")
        scope = ScopeV1.create(organization_id=raw["organization_id"])
        actor = self._authorize(
            identity,
            scope,
            "worker.heartbeat",
            operation="worker.heartbeat",
            target_type="worker",
            target_id=worker_id,
        )
        replay = self._replay(kind="worker.heartbeat", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("worker.identity", worker_id)
        raw = RecordRepository(self.adapter, self.collection).get(worker_id)
        if raw is None:
            raise not_found("worker was not found")
        current = BrainWorkerV1.from_mapping(raw["worker"])
        updated = BrainWorkerV1.create(
            worker_id=current.worker_id,
            worker_type=current.worker_type,
            capabilities=list(current.capabilities),
            status="ONLINE",
            heartbeat_at=iso_now(self.clock),
            last_seen_at=iso_now(self.clock),
        )
        raw["worker"] = updated.as_dict()
        RecordRepository(self.adapter, self.collection).save(worker_id, raw, indexes={"organization_id": raw["organization_id"]})
        self.registry.register(updated, upsert=True)
        event = self._append_event(
            identity,
            actor,
            event_type="worker.heartbeat",
            scope=scope,
            payload={"worker": raw, "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=True,
        )
        audit = self._append_audit(
            identity,
            actor,
            scope=scope,
            operation="worker.heartbeat",
            target_type="worker",
            target_id=worker_id,
        )
        return self._mutation(
            kind="worker.heartbeat",
            idempotency_key=idempotency_key,
            payload=payload,
            value=raw,
            events=(event, audit),
        )

    def list(self, identity: RequestIdentity, *, organization_id: str) -> list[dict[str, Any]]:
        scope = ScopeV1.create(organization_id=organization_id)
        self._authorize(
            identity,
            scope,
            "worker.read",
            operation="worker.list",
            target_type="worker",
            target_id="collection",
            allow_archived=True,
        )
        now = self.clock.now()
        output = []
        for raw in RecordRepository(self.adapter, self.collection).list():
            if raw.get("organization_id") != organization_id:
                continue
            worker = BrainWorkerV1.from_mapping(raw["worker"])
            delta = (now - parse_time(worker.last_seen_at)).total_seconds()
            if worker.status == "OFFLINE" or delta > self.offline_after_seconds:
                status = "OFFLINE"
            elif worker.status == "DEGRADED" or delta > self.degraded_after_seconds:
                status = "DEGRADED"
            else:
                status = "ONLINE"
            effective = BrainWorkerV1.create(
                worker_id=worker.worker_id,
                worker_type=worker.worker_type,
                capabilities=list(worker.capabilities),
                status=status,
                heartbeat_at=worker.heartbeat_at,
                last_seen_at=worker.last_seen_at,
            )
            output.append({"organization_id": organization_id, "worker": effective.as_dict()})
        return output


class LeaseService(ServiceBase):
    collection = "cp_leases"

    def _active_for(self, resource_type: str, resource_id: str) -> dict[str, Any] | None:
        for raw in RecordRepository(self.adapter, self.collection).list():
            if raw.get("resource_type") != resource_type or raw.get("resource_id") != resource_id:
                continue
            if raw.get("released_at") is not None:
                continue
            lease = LeaseV1.from_mapping(raw["lease"])
            if lease.is_active(self.clock.now()):
                return raw
        return None

    @transactional_mutation
    def acquire(
        self,
        identity: RequestIdentity,
        *,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        duration_seconds: int,
        idempotency_key: str,
    ) -> Mutation:
        payload = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "worker_id": worker_id,
            "duration_seconds": duration_seconds,
        }
        self._require_auth(identity)
        scope = ScopeV1.create(organization_id=identity.organization_id)
        actor = self._authorize(
            identity,
            scope,
            "lease.acquire",
            operation="lease.acquire",
            target_type="lease",
            target_id=resource_id,
        )
        replay = self._replay(kind="lease.acquire", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key(
            "lease.resource",
            f"{resource_type}:{resource_id}",
        )
        existing = self._active_for(resource_type, resource_id)
        if existing is not None:
            raise conflict("resource has an active lease", code="LEASE_CONFLICT")
        issued = self.clock.now()
        lease = LeaseV1.create(
            lease_id=_new_id("lease"),
            resource_type=resource_type,
            resource_id=resource_id,
            worker_id=worker_id,
            issued_at=issued.isoformat(),
            expires_at=(issued + timedelta(seconds=duration_seconds)).isoformat(),
            idempotency_key=idempotency_key,
        )
        value = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "worker_id": worker_id,
            "lease": lease.as_dict(),
            "released_at": None,
        }
        RecordRepository(self.adapter, self.collection).save(lease.lease_id, value, indexes={"organization_id": identity.organization_id})
        event = self._append_event(
            identity,
            actor,
            event_type="lease.acquired",
            scope=scope,
            payload={"lease": value, "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=True,
        )
        audit = self._append_audit(
            identity,
            actor,
            scope=scope,
            operation="lease.acquire",
            target_type="lease",
            target_id=lease.lease_id,
        )
        return self._mutation(
            kind="lease.acquire",
            idempotency_key=idempotency_key,
            payload=payload,
            value=value,
            events=(event, audit),
        )

    @transactional_mutation
    def renew(
        self,
        identity: RequestIdentity,
        *,
        lease_id: str,
        worker_id: str,
        duration_seconds: int,
        idempotency_key: str,
    ) -> Mutation:
        payload = {"lease_id": lease_id, "worker_id": worker_id, "duration_seconds": duration_seconds}
        self._require_auth(identity)
        raw = RecordRepository(self.adapter, self.collection).get(lease_id)
        if raw is None:
            raise not_found("lease was not found")
        if raw.get("organization_id") != identity.organization_id:
            raise scope_denied("lease belongs to another organization")
        actor = self._authorize(
            identity,
            ScopeV1.create(organization_id=identity.organization_id),
            "lease.renew",
            operation="lease.renew",
            target_type="lease",
            target_id=lease_id,
        )
        replay = self._replay(kind="lease.renew", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("lease.identity", lease_id)
        raw = RecordRepository(self.adapter, self.collection).get(lease_id)
        if raw is None:
            raise not_found("lease was not found")
        if raw.get("organization_id") != identity.organization_id:
            raise scope_denied("lease belongs to another organization")
        lease = LeaseV1.from_mapping(raw["lease"])
        if raw.get("released_at") is not None or not lease.is_active(self.clock.now()):
            raise conflict("lease is stale or expired", code="LEASE_CONFLICT")
        if lease.worker_id != worker_id:
            raise conflict("lease worker does not match", code="LEASE_CONFLICT")
        now = self.clock.now()
        renewed = LeaseV1.create(
            lease_id=lease.lease_id,
            resource_type=lease.resource_type,
            resource_id=lease.resource_id,
            worker_id=lease.worker_id,
            issued_at=lease.issued_at,
            expires_at=(now + timedelta(seconds=duration_seconds)).isoformat(),
            idempotency_key=lease.idempotency_key,
        )
        raw["lease"] = renewed.as_dict()
        RecordRepository(self.adapter, self.collection).save(lease_id, raw, indexes={"organization_id": identity.organization_id})
        event = self._append_event(
            identity,
            actor,
            event_type="lease.renewed",
            scope=ScopeV1.create(organization_id=identity.organization_id),
            payload={"lease": raw, "can_execute": False, "platform_write_attempted": False},
            idempotency_key=f"event:{idempotency_key}",
            publish=True,
        )
        audit = self._append_audit(
            identity,
            actor,
            scope=ScopeV1.create(organization_id=identity.organization_id),
            operation="lease.renew",
            target_type="lease",
            target_id=lease_id,
        )
        return self._mutation(
            kind="lease.renew",
            idempotency_key=idempotency_key,
            payload=payload,
            value=raw,
            events=(event, audit),
        )

    @transactional_mutation
    def release(
        self,
        identity: RequestIdentity,
        *,
        lease_id: str,
        worker_id: str,
        idempotency_key: str,
    ) -> Mutation:
        payload = {"lease_id": lease_id, "worker_id": worker_id}
        self._require_auth(identity)
        raw = RecordRepository(self.adapter, self.collection).get(lease_id)
        if raw is None:
            raise not_found("lease was not found")
        if raw.get("organization_id") != identity.organization_id:
            raise scope_denied("lease belongs to another organization")
        actor = self._authorize(
            identity,
            ScopeV1.create(organization_id=identity.organization_id),
            "lease.release",
            operation="lease.release",
            target_type="lease",
            target_id=lease_id,
        )
        replay = self._replay(kind="lease.release", idempotency_key=idempotency_key, payload=payload)
        if replay is not None:
            return replay
        self.adapter.lock_transaction_key("lease.identity", lease_id)
        raw = RecordRepository(self.adapter, self.collection).get(lease_id)
        if raw is None:
            raise not_found("lease was not found")
        if raw.get("organization_id") != identity.organization_id:
            raise scope_denied("lease belongs to another organization")
        lease = LeaseV1.from_mapping(raw["lease"])
        if lease.worker_id != worker_id:
            raise conflict("lease worker does not match", code="LEASE_CONFLICT")
        if raw.get("released_at") is None:
            raw["released_at"] = iso_now(self.clock)
            RecordRepository(self.adapter, self.collection).save(
                lease_id,
                raw,
                indexes={"organization_id": identity.organization_id},
            )
            event = self._append_event(
                identity,
                actor,
                event_type="lease.released",
                scope=ScopeV1.create(organization_id=identity.organization_id),
                payload={"lease": raw, "can_execute": False, "platform_write_attempted": False},
                idempotency_key=f"event:{idempotency_key}",
                publish=True,
            )
            audit = self._append_audit(
                identity,
                actor,
                scope=ScopeV1.create(organization_id=identity.organization_id),
                operation="lease.release",
                target_type="lease",
                target_id=lease_id,
            )
            events = (event, audit)
        else:
            events = ()
        return self._mutation(
            kind="lease.release",
            idempotency_key=idempotency_key,
            payload=payload,
            value=raw,
            events=events,
        )
