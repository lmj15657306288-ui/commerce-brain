"""Device batch synchronization and cursor replay services."""

from __future__ import annotations

from typing import Any

from core.contracts import EventEnvelopeV1, ScopeV1

from .auth import RequestIdentity
from .clock import Clock, iso_now
from .errors import ControlPlaneError, conflict, scope_denied, validation
from .event_store import EventStore, StoredEvent
from .repositories import RecordRepository
from .services import DeviceSessionService, ServiceBase
from .websocket import EventBroker


class SyncService(ServiceBase):
    def __init__(
        self,
        *,
        device_sessions: DeviceSessionService,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.device_sessions = device_sessions

    def _visible(self, identity: RequestIdentity, event: EventEnvelopeV1) -> bool:
        try:
            return self.registry.actor_can_access(
                identity.actor_id,
                event.scope,
                "event.read",
                include_archived=True,
            )
        except TypeError:
            return self.registry.actor_can_access(identity.actor_id, event.scope, "event.read")
        except Exception:
            return False

    def sync(
        self,
        identity: RequestIdentity,
        *,
        device_id: str,
        actor_id: str,
        last_ack_cursor: int,
        client_time: str,
        events: list[dict[str, Any]],
        capabilities: list[str],
        session_info: dict[str, Any],
        limit: int = 100,
    ) -> dict[str, Any]:
        self._require_auth(identity)
        if identity.device_id != device_id:
            raise scope_denied("sync device does not match authenticated identity")
        if identity.actor_id != actor_id:
            raise scope_denied("sync actor does not match authenticated identity")
        session = self.device_sessions.ensure_active(identity, device_id)
        device = session["device"]
        scope = ScopeV1.create(organization_id=device["organization_id"])
        actor = self._authorize(
            identity,
            scope,
            "sync.write",
            operation="sync.batch",
            target_type="device",
            target_id=device_id,
        )
        if not isinstance(last_ack_cursor, int) or last_ack_cursor < 0:
            raise conflict("last_ack_cursor must be non-negative", code="STALE_CURSOR")
        latest = self.events.latest_cursor
        if last_ack_cursor > latest:
            raise conflict("last_ack_cursor is ahead of server history", code="STALE_CURSOR")
        acknowledged: list[str] = []
        received_events: list[StoredEvent] = []
        for raw in events:
            try:
                event = EventEnvelopeV1.from_mapping(raw)
            except Exception as exc:
                raise validation("sync event failed contract validation") from exc
            if event.actor.actor_id != identity.actor_id:
                raise scope_denied("sync event actor does not match authenticated identity")
            if event.scope.organization_id != identity.organization_id:
                raise scope_denied("sync event crosses organization boundary")
            if not self.registry.actor_can_access(
                identity.actor_id,
                event.scope,
                "event.write",
            ):
                raise scope_denied("sync event scope is not authorized")
            stored, replayed = self.events.append(event)
            received_events.append(stored)
            acknowledged.append(event.event_id)
            if not replayed:
                self.broker.publish_nowait(stored)
                self._append_audit(
                    identity,
                    actor,
                    scope=scope,
                    operation="sync.event.ingest",
                    target_type="event",
                    target_id=event.event_id,
                )
        replay = self.events.get_events_after(
            last_ack_cursor,
            limit=min(limit, 500),
            visible=lambda event: self._visible(identity, event),
        )
        if last_ack_cursor > int(session.get("ack_cursor", 0)):
            session["ack_cursor"] = last_ack_cursor
            RecordRepository(self.adapter, self.device_sessions.collection).save(
                device_id,
                session,
                indexes={"organization_id": device["organization_id"], "actor_id": device.get("user_id")},
            )
        return {
            "server_time": iso_now(self.clock),
            "latest_cursor": self.events.latest_cursor,
            "events_after_cursor": [item.as_dict() for item in replay],
            "acknowledged_event_ids": acknowledged,
            "device_status": device["status"],
            "sync_status": "OK",
            "received_event_cursors": [item.cursor for item in received_events],
            "client_time_accepted_for_ordering": False,
            "capabilities": sorted(set(capabilities)),
            "session_info_present": bool(session_info),
            "can_execute": False,
        }

    def ingest_event(
        self,
        identity: RequestIdentity,
        *,
        raw_event: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_auth(identity)
        try:
            event = EventEnvelopeV1.from_mapping(raw_event)
        except Exception as exc:
            raise validation("event failed contract validation") from exc
        if event.actor.actor_id != identity.actor_id:
            raise scope_denied("event actor does not match authenticated identity")
        if not self.registry.actor_can_access(identity.actor_id, event.scope, "event.write"):
            raise scope_denied("event scope is not authorized for event.write")
        if not self._visible(identity, event):
            raise scope_denied("event scope is not authorized")
        actor = self._actor(identity)
        stored, replayed = self.events.append(event)
        if not replayed:
            self.broker.publish_nowait(stored)
            self._append_audit(
                identity,
                actor,
                scope=event.scope,
                operation="event.ingest",
                target_type="event",
                target_id=event.event_id,
            )
        return {
            "event": stored.as_dict(),
            "replayed": replayed,
            "delivery": "at-least-once",
        }

    def list_events(
        self,
        identity: RequestIdentity,
        *,
        cursor: int,
        scope: ScopeV1,
        limit: int,
    ) -> dict[str, Any]:
        self._authorize(
            identity,
            scope,
            "event.read",
            operation="event.list",
            target_type="event",
            target_id="history",
            allow_archived=True,
        )
        events = self.events.get_events_after(
            cursor,
            scope=scope,
            limit=limit,
            visible=lambda event: self._visible(identity, event),
        )
        return {
            "cursor": cursor,
            "latest_cursor": self.events.latest_cursor,
            "events": [item.as_dict() for item in events],
            "next_cursor": events[-1].cursor if events else cursor,
            "delivery": "at-least-once",
            "source_of_truth": "event_store",
        }
