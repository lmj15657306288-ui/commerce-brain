"""Local FastAPI Control Plane skeleton for Phase 2B-3."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import psycopg
from starlette.websockets import WebSocketState

from core.context_registry import ContextRegistry
from core.contracts import ContractValidationError, ScopeV1
from core.persistence import InMemoryPersistenceAdapter, PersistenceAdapter, PersistenceError

from .auth import AuthProvider, FailClosedAuthProvider, RequestIdentity
from .clock import Clock, SystemClock
from .errors import ControlPlaneError, database_unavailable, unauthenticated, validation
from .event_store import EventStore
from .repositories import IdempotencyRepository
from .redis_layer import NoopEphemeralLayer
from .schemas import (
    AlertCreateRequest,
    ApprovalCreateRequest,
    DecisionRequest,
    DeviceSessionRequest,
    EventIngestRequest,
    HeartbeatRequest,
    LeaseAcquireRequest,
    LeaseReleaseRequest,
    LeaseRenewRequest,
    ScopeModel,
    SyncRequest,
    TaskCreateRequest,
    TransitionRequest,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    query_scope,
)
from .services import (
    AlertService,
    ApprovalService,
    DeviceSessionService,
    LeaseService,
    TaskService,
    WorkerService,
)
from .sync import SyncService
from .websocket import EventBroker


_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


@dataclass
class ControlPlane:
    adapter: PersistenceAdapter
    registry: ContextRegistry
    auth_provider: AuthProvider
    clock: Clock
    events: EventStore
    broker: EventBroker
    idempotency: IdempotencyRepository
    tasks: TaskService
    alerts: AlertService
    approvals: ApprovalService
    devices: DeviceSessionService
    workers: WorkerService
    leases: LeaseService
    sync: SyncService
    ephemeral: Any

    @classmethod
    def build(
        cls,
        *,
        adapter: PersistenceAdapter | None = None,
        registry: ContextRegistry | None = None,
        auth_provider: AuthProvider | None = None,
        clock: Clock | None = None,
        broker: EventBroker | None = None,
        ephemeral: Any | None = None,
    ) -> "ControlPlane":
        selected_adapter = adapter or InMemoryPersistenceAdapter()
        selected_registry = registry or ContextRegistry(selected_adapter)
        selected_clock = clock or SystemClock()
        selected_ephemeral = ephemeral or NoopEphemeralLayer()
        selected_broker = broker or EventBroker(ephemeral=selected_ephemeral)
        events = EventStore(selected_adapter)
        idempotency = IdempotencyRepository(selected_adapter)
        common = {
            "adapter": selected_adapter,
            "registry": selected_registry,
            "events": events,
            "broker": selected_broker,
            "clock": selected_clock,
            "idempotency": idempotency,
        }
        devices = DeviceSessionService(**common)
        return cls(
            adapter=selected_adapter,
            registry=selected_registry,
            auth_provider=auth_provider or FailClosedAuthProvider(),
            clock=selected_clock,
            events=events,
            broker=selected_broker,
            idempotency=idempotency,
            tasks=TaskService(**common),
            alerts=AlertService(**common),
            approvals=ApprovalService(**common),
            devices=devices,
            workers=WorkerService(**common),
            leases=LeaseService(**common),
            sync=SyncService(device_sessions=devices, **common),
            ephemeral=selected_ephemeral,
        )


def _request_id(request: Request) -> str:
    candidate = str(request.headers.get("x-request-id", "")).strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else f"req_{__import__('uuid').uuid4().hex[:20]}"


def _scope_for_identity(
    identity: RequestIdentity,
    *,
    organization_id: str | None,
    brand_id: str | None,
    category_id: str | None,
    shop_id: str | None,
    channel_id: str | None,
) -> ScopeV1:
    if not identity.authenticated or not identity.organization_id:
        raise unauthenticated()
    return query_scope(
        organization_id=organization_id or identity.organization_id,
        brand_id=brand_id,
        category_id=category_id,
        shop_id=shop_id,
        channel_id=channel_id,
    )


def _scope_model(scope: ScopeV1) -> dict[str, Any]:
    return scope.as_dict()


def create_app(
    *,
    adapter: PersistenceAdapter | None = None,
    registry: ContextRegistry | None = None,
    auth_provider: AuthProvider | None = None,
    clock: Clock | None = None,
    broker: EventBroker | None = None,
    ephemeral: Any | None = None,
    allowed_origins: list[str] | None = None,
    rate_limit_enabled: bool = False,
    rate_limit_per_minute: int = 120,
) -> FastAPI:
    control = ControlPlane.build(
        adapter=adapter,
        registry=registry,
        auth_provider=auth_provider,
        clock=clock,
        broker=broker,
        ephemeral=ephemeral,
    )
    app = FastAPI(title="Commerce Brain Central Control Plane", version="0.1.0")
    app.state.control_plane = control
    normalized_origins = [
        origin.strip()
        for origin in (allowed_origins or [])
        if origin.strip() and origin.strip() != "*"
    ]
    if normalized_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=normalized_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = _request_id(request)
        if rate_limit_enabled and request.url.path not in {"/health", "/readiness"}:
            limited_paths = (
                request.url.path == "/sync"
                or request.url.path == "/events"
                or request.url.path.startswith("/tasks")
                or request.url.path.startswith("/alerts")
                or request.url.path.startswith("/approvals")
                or request.url.path.startswith("/devices")
                or request.url.path.startswith("/workers")
                or request.url.path.startswith("/leases")
            )
            if limited_paths:
                client_host = request.client.host if request.client else "unknown"
                try:
                    allowed = await asyncio.wait_for(
                        asyncio.to_thread(
                            control.ephemeral.allow_rate,
                            f"{client_host}:{request.method}:{request.url.path}",
                            limit=rate_limit_per_minute,
                            window_seconds=60,
                        ),
                        timeout=2.0,
                    )
                except Exception:
                    # Rate limiting is ephemeral; a Redis outage must not block
                    # PostgreSQL-backed business state mutations.
                    allowed = True
                if not allowed:
                    response = JSONResponse(
                        status_code=429,
                        content={
                            "error_code": "RATE_LIMITED",
                            "message": "request rate limit exceeded",
                            "request_id": request.state.request_id,
                            "details": None,
                        },
                    )
                    response.headers["X-Request-ID"] = request.state.request_id
                    return response
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ControlPlaneError)
    async def control_plane_error_handler(request: Request, exc: ControlPlaneError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.code,
                "message": exc.message,
                "request_id": getattr(request.state, "request_id", "req_unknown"),
                "details": exc.details,
            },
        )

    @app.exception_handler(ContractValidationError)
    async def contract_error_handler(request: Request, exc: ContractValidationError):
        error = validation(str(exc), details={"contract_code": exc.code})
        return await control_plane_error_handler(request, error)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        error = validation("request validation failed", details={"fields": exc.errors()})
        return await control_plane_error_handler(request, error)

    @app.exception_handler(PersistenceError)
    async def persistence_error_handler(request: Request, exc: PersistenceError):
        return await control_plane_error_handler(request, database_unavailable())

    @app.exception_handler(psycopg.Error)
    async def postgres_error_handler(request: Request, exc: psycopg.Error):
        return await control_plane_error_handler(request, database_unavailable())

    def cp(request: Request) -> ControlPlane:
        return request.app.state.control_plane

    def identity(request: Request, service: ControlPlane = Depends(cp)) -> RequestIdentity:
        return service.auth_provider.authenticate(request)

    def list_params(
        organization_id: str | None = Query(default=None),
        brand_id: str | None = Query(default=None),
        category_id: str | None = Query(default=None),
        shop_id: str | None = Query(default=None),
        channel_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        priority: str | None = Query(default=None),
        created_after: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return {
            "organization_id": organization_id,
            "brand_id": brand_id,
            "category_id": category_id,
            "shop_id": shop_id,
            "channel_id": channel_id,
            "status": status,
            "priority": priority,
            "created_after": created_after,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/health")
    async def health(service: ControlPlane = Depends(cp)):
        database_ok = getattr(service.adapter, "health_check", lambda: True)()
        try:
            redis_ok = await asyncio.wait_for(
                asyncio.to_thread(service.ephemeral.health_check),
                timeout=2.0,
            )
        except Exception:
            redis_ok = False
        schema_version = service.adapter.schema_version if database_ok else None
        supported_schema_version = getattr(
            service.adapter,
            "MIGRATION_VERSION",
            schema_version,
        )
        migration_ok = schema_version == supported_schema_version
        return {
            "status": "ok" if database_ok and migration_ok else "degraded",
            "service": "control_plane",
            "latest_cursor": service.events.latest_cursor if database_ok else None,
            "execution_enabled": False,
            "websocket_is_source_of_truth": False,
            "event_store_is_recovery_source": True,
            "dependencies": {
                "postgresql_or_local_adapter": "ok" if database_ok else "unhealthy",
                "migration": "current" if migration_ok else "outdated",
                "schema_version": schema_version,
                "redis": "ok" if redis_ok else "degraded",
                "auth_configured": bool(getattr(service.auth_provider, "configured", False)),
            },
        }

    @app.get("/readiness")
    async def readiness(service: ControlPlane = Depends(cp)):
        database_ok = getattr(service.adapter, "health_check", lambda: True)()
        try:
            redis_ok = await asyncio.wait_for(
                asyncio.to_thread(service.ephemeral.health_check),
                timeout=2.0,
            )
        except Exception:
            redis_ok = False
        auth_configured = bool(getattr(service.auth_provider, "configured", False))
        schema_version = service.adapter.schema_version if database_ok else None
        supported_schema_version = getattr(
            service.adapter,
            "MIGRATION_VERSION",
            schema_version,
        )
        migration_ok = schema_version == supported_schema_version
        ready = database_ok and migration_ok and auth_configured
        body = {
            "ready": ready,
            "status": "ready" if ready else "unhealthy",
            "dependencies": {
                "postgresql_or_local_adapter": "ok" if database_ok else "unhealthy",
                "migration": "current" if migration_ok else "outdated",
                "schema_version": schema_version,
                "redis": "ok" if redis_ok else "degraded",
                "auth": "configured" if auth_configured else "not_configured",
            },
            "redis_is_optional_for_truth": True,
            "execution_enabled": False,
        }
        return JSONResponse(status_code=200 if ready else 503, content=body)
    @app.get("/context/shops/{shop_id}")
    async def get_shop(
        shop_id: str,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        service.tasks._require_auth(current)
        scope = ScopeV1.create(organization_id=current.organization_id, shop_id=shop_id)
        service.tasks._authorize(
            current,
            scope,
            "context.read",
            operation="context.shop.read",
            target_type="shop",
            target_id=shop_id,
            allow_archived=True,
        )
        shop = service.registry.get_shop(shop_id)
        if shop is None:
            raise ControlPlaneError("NOT_FOUND", "shop was not found", 404)
        return shop.as_dict()

    @app.get("/context/shops/{shop_id}/categories")
    async def get_shop_categories(
        shop_id: str,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        service.tasks._require_auth(current)
        scope = ScopeV1.create(organization_id=current.organization_id, shop_id=shop_id)
        service.tasks._authorize(
            current,
            scope,
            "context.read",
            operation="context.shop.categories.read",
            target_type="shop",
            target_id=shop_id,
            allow_archived=True,
        )
        return {
            "shop_id": shop_id,
            "categories": [
                item.as_dict()
                for item in service.registry.get_shop_categories(shop_id)
            ],
        }

    @app.get("/tasks")
    async def list_tasks(
        params: dict[str, Any] = Depends(list_params),
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        scope = _scope_for_identity(current, **{key: params[key] for key in ("organization_id", "brand_id", "category_id", "shop_id", "channel_id")})
        items, next_offset = service.tasks.list(
            current,
            scope=scope,
            status=params["status"],
            created_after=params["created_after"],
            limit=params["limit"],
            offset=params["offset"],
        )
        return {"items": items, "next_offset": next_offset, "limit": params["limit"]}

    @app.post("/tasks")
    async def create_task(
        body: TaskCreateRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        result = service.tasks.create(
            current,
            scope=body.scope.to_contract(),
            task_type=body.task_type,
            priority=body.priority,
            owner=body.owner,
            due_at=body.due_at,
            business_impact=None if body.business_impact is None else body.business_impact.model_dump(),
            source_refs=body.source_refs,
            idempotency_key=body.idempotency_key,
        )
        return result.as_dict()

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: str, current: RequestIdentity = Depends(identity), service: ControlPlane = Depends(cp)):
        return service.tasks.get(current, task_id)

    def task_transition(status: str):
        async def handler(
            task_id: str,
            body: TransitionRequest,
            current: RequestIdentity = Depends(identity),
            service: ControlPlane = Depends(cp),
        ):
            result = service.tasks.transition(
                current,
                task_id=task_id,
                next_status=status,
                idempotency_key=body.idempotency_key,
                assignee_id=body.assignee_id,
                reason=body.reason,
            )
            return result.as_dict()

        return handler

    app.add_api_route("/tasks/{task_id}/assign", task_transition("ASSIGNED"), methods=["POST"])
    app.add_api_route("/tasks/{task_id}/start", task_transition("IN_PROGRESS"), methods=["POST"])
    app.add_api_route("/tasks/{task_id}/block", task_transition("BLOCKED"), methods=["POST"])
    app.add_api_route("/tasks/{task_id}/complete", task_transition("DONE"), methods=["POST"])
    app.add_api_route("/tasks/{task_id}/cancel", task_transition("CANCELLED"), methods=["POST"])

    @app.get("/alerts")
    async def list_alerts(
        params: dict[str, Any] = Depends(list_params),
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        scope = _scope_for_identity(current, **{key: params[key] for key in ("organization_id", "brand_id", "category_id", "shop_id", "channel_id")})
        items, next_offset = service.alerts.list(
            current,
            scope=scope,
            status=params["status"],
            priority=params["priority"],
            created_after=params["created_after"],
            limit=params["limit"],
            offset=params["offset"],
        )
        return {"items": items, "next_offset": next_offset, "limit": params["limit"]}

    @app.post("/alerts")
    async def create_alert(
        body: AlertCreateRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        result = service.alerts.create(
            current,
            scope=body.scope.to_contract(),
            priority=body.priority,
            reason_code=body.reason_code,
            summary=body.summary,
            evidence_refs=body.evidence_refs,
            business_impact=None if body.business_impact is None else body.business_impact.model_dump(),
            recommended_action=body.recommended_action,
            dedupe_key=body.dedupe_key,
            cooldown_until=body.cooldown_until,
            idempotency_key=body.idempotency_key,
        )
        return result.as_dict()

    @app.get("/alerts/{alert_id}")
    async def get_alert(alert_id: str, current: RequestIdentity = Depends(identity), service: ControlPlane = Depends(cp)):
        return service.alerts.get(current, alert_id)

    def alert_transition(status: str):
        async def handler(
            alert_id: str,
            body: TransitionRequest,
            current: RequestIdentity = Depends(identity),
            service: ControlPlane = Depends(cp),
        ):
            result = service.alerts.transition(
                current,
                alert_id=alert_id,
                next_status=status,
                idempotency_key=body.idempotency_key,
            )
            return result.as_dict()

        return handler

    app.add_api_route("/alerts/{alert_id}/ack", alert_transition("ACKNOWLEDGED"), methods=["POST"])
    app.add_api_route("/alerts/{alert_id}/resolve", alert_transition("RESOLVED"), methods=["POST"])
    app.add_api_route("/alerts/{alert_id}/expire", alert_transition("EXPIRED"), methods=["POST"])

    @app.get("/approvals")
    async def list_approvals(
        params: dict[str, Any] = Depends(list_params),
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        scope = _scope_for_identity(current, **{key: params[key] for key in ("organization_id", "brand_id", "category_id", "shop_id", "channel_id")})
        items, next_offset = service.approvals.list(
            current,
            scope=scope,
            status=params["status"],
            created_after=params["created_after"],
            limit=params["limit"],
            offset=params["offset"],
        )
        return {"items": items, "next_offset": next_offset, "limit": params["limit"]}

    @app.post("/approvals")
    async def create_approval(
        body: ApprovalCreateRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        result = service.approvals.create(
            current,
            scope=body.scope.to_contract(),
            proposal_id=body.proposal_id,
            risk_level=body.risk_level,
            expires_at=body.expires_at,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
        )
        return result.as_dict()

    @app.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str, current: RequestIdentity = Depends(identity), service: ControlPlane = Depends(cp)):
        return service.approvals.get(current, approval_id)

    def approval_decision(status: str):
        async def handler(
            approval_id: str,
            body: DecisionRequest,
            current: RequestIdentity = Depends(identity),
            service: ControlPlane = Depends(cp),
        ):
            result = service.approvals.decide(
                current,
                approval_id=approval_id,
                next_status=status,
                idempotency_key=body.idempotency_key,
                decision_note=body.decision_note,
            )
            return result.as_dict()

        return handler

    app.add_api_route("/approvals/{approval_id}/approve", approval_decision("APPROVED"), methods=["POST"])
    app.add_api_route("/approvals/{approval_id}/reject", approval_decision("REJECTED"), methods=["POST"])
    app.add_api_route("/approvals/{approval_id}/request-revision", approval_decision("REVISION_REQUESTED"), methods=["POST"])
    app.add_api_route("/approvals/{approval_id}/cancel", approval_decision("CANCELLED"), methods=["POST"])
    app.add_api_route("/approvals/{approval_id}/expire", approval_decision("EXPIRED"), methods=["POST"])

    @app.post("/devices/session")
    async def open_device_session(
        body: DeviceSessionRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        result = service.devices.register(
            current,
            device_id=body.device_id,
            device_type=body.device_type,
            user_id=body.user_id,
            organization_id=body.organization_id,
            shop_ids=body.shop_ids,
            capabilities=body.capabilities,
            ttl_seconds=body.ttl_seconds,
            idempotency_key=body.idempotency_key,
        )
        return result.as_dict()

    @app.get("/devices/{device_id}")
    async def get_device_session(
        device_id: str,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.devices.get(current, device_id)

    @app.post("/devices/{device_id}/heartbeat")
    async def heartbeat_device(
        device_id: str,
        body: HeartbeatRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.devices.heartbeat(current, device_id, idempotency_key=body.idempotency_key).as_dict()

    @app.post("/devices/{device_id}/disconnect")
    async def disconnect_device(
        device_id: str,
        body: HeartbeatRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.devices.disconnect(current, device_id, idempotency_key=body.idempotency_key).as_dict()

    @app.post("/devices/{device_id}/revoke")
    async def revoke_device(
        device_id: str,
        body: HeartbeatRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.devices.revoke(current, device_id, idempotency_key=body.idempotency_key).as_dict()

    @app.post("/workers/register")
    async def register_worker(
        body: WorkerRegisterRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.workers.register(
            current,
            worker_id=body.worker_id,
            worker_type=body.worker_type,
            capabilities=body.capabilities,
            organization_id=body.organization_id,
            idempotency_key=body.idempotency_key,
        ).as_dict()

    @app.post("/workers/{worker_id}/heartbeat")
    async def heartbeat_worker(
        worker_id: str,
        body: WorkerHeartbeatRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.workers.heartbeat(current, worker_id, idempotency_key=body.idempotency_key).as_dict()

    @app.get("/workers")
    async def list_workers(
        organization_id: str | None = Query(default=None),
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return {
            "items": service.workers.list(
                current,
                organization_id=organization_id or current.organization_id,
            )
        }

    @app.post("/leases/acquire")
    async def acquire_lease(
        body: LeaseAcquireRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.leases.acquire(
            current,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            worker_id=body.worker_id,
            duration_seconds=body.duration_seconds,
            idempotency_key=body.idempotency_key,
        ).as_dict()

    @app.post("/leases/renew")
    async def renew_lease(
        body: LeaseRenewRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.leases.renew(
            current,
            lease_id=body.lease_id,
            worker_id=body.worker_id,
            duration_seconds=body.duration_seconds,
            idempotency_key=body.idempotency_key,
        ).as_dict()

    @app.post("/leases/release")
    async def release_lease(
        body: LeaseReleaseRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.leases.release(
            current,
            lease_id=body.lease_id,
            worker_id=body.worker_id,
            idempotency_key=body.idempotency_key,
        ).as_dict()

    @app.post("/sync")
    async def sync(
        body: SyncRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.sync.sync(
            current,
            device_id=body.device_id,
            actor_id=body.actor_id,
            idempotency_key=body.idempotency_key,
            last_ack_cursor=body.last_ack_cursor,
            client_time=body.client_time,
            events=body.events,
            capabilities=body.capabilities,
            session_info=body.session_info,
        )

    @app.post("/events")
    async def ingest_event(
        body: EventIngestRequest,
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        return service.sync.ingest_event(current, raw_event=body.event)

    @app.get("/events")
    async def list_events(
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        organization_id: str | None = Query(default=None),
        brand_id: str | None = Query(default=None),
        category_id: str | None = Query(default=None),
        shop_id: str | None = Query(default=None),
        channel_id: str | None = Query(default=None),
        current: RequestIdentity = Depends(identity),
        service: ControlPlane = Depends(cp),
    ):
        scope = _scope_for_identity(
            current,
            organization_id=organization_id,
            brand_id=brand_id,
            category_id=category_id,
            shop_id=shop_id,
            channel_id=channel_id,
        )
        return service.sync.list_events(current, cursor=cursor, scope=scope, limit=limit)

    @app.websocket("/ws/events")
    async def websocket_events(websocket: WebSocket):
        service: ControlPlane = websocket.app.state.control_plane
        origin = websocket.headers.get("origin")
        if origin and origin not in normalized_origins:
            await websocket.close(code=4403, reason="ORIGIN_DENIED")
            return
        current = service.auth_provider.authenticate_websocket(websocket)
        if not current.authenticated:
            await websocket.close(code=4401, reason="UNAUTHENTICATED")
            return
        device_id = websocket.query_params.get("device_id") or current.device_id
        if not device_id or (current.device_id is not None and current.device_id != device_id):
            await websocket.close(code=4403, reason="FORBIDDEN")
            return
        try:
            service.devices.ensure_active(current, device_id)
            scope = _scope_for_identity(
                current,
                organization_id=websocket.query_params.get("organization_id"),
                brand_id=websocket.query_params.get("brand_id"),
                category_id=websocket.query_params.get("category_id"),
                shop_id=websocket.query_params.get("shop_id"),
                channel_id=websocket.query_params.get("channel_id"),
            )
            service.tasks._authorize(
                current,
                scope,
                "event.read",
                operation="event.subscribe",
                target_type="websocket",
                target_id=device_id,
                allow_archived=True,
            )
            cursor = int(websocket.query_params.get("cursor", "0"))
            queue_size = min(max(int(websocket.query_params.get("queue_size", "100")), 1), 500)
            replay = service.events.get_events_after(
                cursor,
                scope=scope,
                limit=500,
                visible=lambda event: service.sync._visible(current, event),
            )
        except (ValueError, ControlPlaneError):
            await websocket.close(code=4403, reason="SCOPE_DENIED")
            return

        await websocket.accept()

        async def close_slow(reason: str) -> None:
            if websocket.application_state == WebSocketState.CONNECTED:
                await websocket.close(code=1013, reason=reason)

        subscription = await service.broker.subscribe(
            client_id=device_id,
            scope=scope,
            max_queue=queue_size,
            visible=lambda event: service.sync._visible(current, event),
            close_callback=close_slow,
        )
        for event in replay:
            subscription.offer(event)

        async def sender() -> None:
            while subscription.closed_reason is None:
                message = await subscription.queue.get()
                await websocket.send_json(message)

        sender_task = asyncio.create_task(sender())
        try:
            while True:
                incoming = await websocket.receive_json()
                message_type = incoming.get("type")
                if message_type == "resume":
                    resume_cursor = incoming.get("cursor")
                    if not isinstance(resume_cursor, int):
                        raise ValueError("cursor must be an integer")
                    replay = service.events.get_events_after(
                        resume_cursor,
                        scope=scope,
                        limit=500,
                        visible=lambda event: service.sync._visible(current, event),
                    )
                    for event in replay:
                        subscription.offer(event)
                elif message_type == "heartbeat":
                    await websocket.send_json(
                        {
                            "type": "heartbeat_ack",
                            "server_time": service.clock.now().isoformat(),
                            "can_execute": False,
                        }
                    )
                elif message_type == "ack":
                    continue
                else:
                    await websocket.send_json({"type": "error", "error_code": "VALIDATION_ERROR"})
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            sender_task.cancel()
            await service.broker.unsubscribe(device_id)

    return app
