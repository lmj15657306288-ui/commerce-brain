"""Pydantic HTTP and synchronization boundary schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.contracts import ScopeV1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScopeModel(StrictModel):
    organization_id: str | None = None
    brand_id: str | None = None
    category_id: str | None = None
    shop_id: str | None = None
    channel_id: str | None = None
    product_id: str | None = None
    sku_id: str | None = None

    def to_contract(self) -> ScopeV1:
        return ScopeV1.create(**self.model_dump(exclude_none=True))


class BusinessImpactModel(StrictModel):
    gmv_impact: float | None = None
    profit_impact: float | None = None
    inventory_impact: float | None = None
    customer_impact: float | None = None
    live_impact: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class TaskCreateRequest(StrictModel):
    scope: ScopeModel
    task_type: str
    priority: int = Field(ge=0, le=100)
    owner: str | None = None
    due_at: str | None = None
    business_impact: BusinessImpactModel | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=128)


class TransitionRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    assignee_id: str | None = None
    reason: str | None = Field(default=None, max_length=500)


class AlertCreateRequest(StrictModel):
    scope: ScopeModel
    priority: str
    reason_code: str
    summary: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    business_impact: BusinessImpactModel | None = None
    recommended_action: str = Field(min_length=1, max_length=500)
    dedupe_key: str
    cooldown_until: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)


class ApprovalCreateRequest(StrictModel):
    scope: ScopeModel
    proposal_id: str
    risk_level: str
    expires_at: str
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class DecisionRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    decision_note: str | None = Field(default=None, max_length=1000)


class DeviceSessionRequest(StrictModel):
    device_id: str
    device_type: str
    user_id: str | None = None
    organization_id: str
    shop_ids: list[str] = Field(default_factory=list, max_length=100)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    ttl_seconds: int = Field(default=3600, ge=1, le=86400)
    idempotency_key: str = Field(min_length=1, max_length=128)


class HeartbeatRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class WorkerRegisterRequest(StrictModel):
    worker_id: str
    worker_type: str
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    organization_id: str
    idempotency_key: str = Field(min_length=1, max_length=128)


class WorkerHeartbeatRequest(StrictModel):
    idempotency_key: str
    health_hint: str | None = None


class LeaseAcquireRequest(StrictModel):
    resource_type: str
    resource_id: str
    worker_id: str
    duration_seconds: int = Field(default=60, ge=30, le=120)
    idempotency_key: str


class LeaseRenewRequest(StrictModel):
    lease_id: str
    worker_id: str
    duration_seconds: int = Field(default=60, ge=30, le=120)
    idempotency_key: str


class LeaseReleaseRequest(StrictModel):
    lease_id: str
    worker_id: str
    idempotency_key: str


class SyncRequest(StrictModel):
    device_id: str
    actor_id: str
    last_ack_cursor: int = Field(default=0, ge=0)
    client_time: str
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    session_info: dict[str, Any] = Field(default_factory=dict)


class EventIngestRequest(StrictModel):
    event: dict[str, Any]


class PageQuery(StrictModel):
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


def query_scope(
    *,
    organization_id: str | None,
    brand_id: str | None,
    category_id: str | None,
    shop_id: str | None,
    channel_id: str | None,
) -> ScopeV1:
    return ScopeModel(
        organization_id=organization_id,
        brand_id=brand_id,
        category_id=category_id,
        shop_id=shop_id,
        channel_id=channel_id,
    ).to_contract()
