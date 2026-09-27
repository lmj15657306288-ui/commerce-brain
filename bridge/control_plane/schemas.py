"""Pydantic HTTP and synchronization boundary schemas."""

from __future__ import annotations

from typing import Any, Literal

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


class OwnerBusinessImpactModel(BusinessImpactModel):
    schema_version: Literal[1] = 1


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
    idempotency_key: str = Field(min_length=1, max_length=128)
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


class OwnerRealtimeTicketRequest(StrictModel):
    device_id: str = Field(min_length=1, max_length=128)
    scope: ScopeModel
    cursor: int = Field(default=0, ge=0)


class OwnerActionRequest(StrictModel):
    device_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    decision_note: str | None = Field(default=None, max_length=1000)


class OwnerInboxItemModel(StrictModel):
    item_id: str
    item_type: Literal["TASK", "ALERT", "APPROVAL"]
    owner_category: Literal["NEED_DECISION", "NEED_APPROVAL", "NEED_AWARENESS"]
    title: str
    summary: str
    why_it_matters: str
    recommended_action: str
    scope: ScopeModel
    priority: str | int
    business_impact: OwnerBusinessImpactModel | None
    status: str
    created_at: str
    updated_at: str
    freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]
    source_ref: str
    shop_name: str | None = None
    rank_score: float | None = None
    rank_reason: str | None = None


class OwnerInboxResponseModel(StrictModel):
    items: list[OwnerInboxItemModel]
    total: int
    next_offset: int | None
    limit: int
    truncated: bool


class OwnerAttentionCountsModel(StrictModel):
    need_decision: int | None
    need_approval: int | None
    need_awareness: int | None


class OwnerSummaryModel(StrictModel):
    schema_version: Literal[1] = 1
    organization_id: str
    overall_health: Literal["HEALTHY", "DEGRADED", "OFFLINE", "UNKNOWN"]
    attention_counts: OwnerAttentionCountsModel
    shop_count: int | None
    worker_health: Literal["ONLINE", "DEGRADED", "OFFLINE", "UNKNOWN"]
    system_health: Literal["HEALTHY", "DEGRADED", "OFFLINE", "UNKNOWN"]
    generated_at: str
    freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]
    counts_complete: bool


class OwnerAlertModel(StrictModel):
    alert_id: str
    scope: ScopeModel
    priority: Literal["P0", "P1", "P2", "P3"]
    status: str
    reason_code: str
    summary: str
    evidence_refs: list[str]
    business_impact: OwnerBusinessImpactModel
    recommended_action: str
    created_at: str
    updated_at: str
    cooldown_until: str | None
    shop_name: str | None = None
    freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]


class OwnerAlertPageModel(StrictModel):
    items: list[OwnerAlertModel]
    total: int
    next_offset: int | None
    limit: int
    truncated: bool


class OwnerApprovalModel(StrictModel):
    approval_id: str
    title: str
    proposal_id: str
    scope: ScopeModel
    shop_name: str | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    requested_by: str
    requested_at: str
    reason: str
    business_impact: None = None
    evidence: list[str]
    expires_at: str
    status: str
    decided_by: str | None
    decided_at: str | None
    decision_note: str | None
    freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]


class OwnerApprovalPageModel(StrictModel):
    items: list[OwnerApprovalModel]
    total: int
    next_offset: int | None
    limit: int
    truncated: bool


class OwnerWorkerModel(StrictModel):
    worker_id: str
    worker_type: str
    status: Literal["ONLINE", "DEGRADED", "OFFLINE", "UNKNOWN"]
    last_seen_at: str
    capabilities: list[str]
    freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]


class OwnerSystemHealthModel(StrictModel):
    cloud_status: Literal["HEALTHY", "DEGRADED", "OFFLINE", "UNKNOWN"]
    brain_worker_status: Literal["ONLINE", "DEGRADED", "OFFLINE", "UNKNOWN"]
    realtime_status: Literal["HEALTHY", "DEGRADED", "OFFLINE", "UNKNOWN"]
    data_freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]
    last_updated: str
    workers: list[OwnerWorkerModel]


class OwnerShopModel(StrictModel):
    shop_id: str
    shop_name: str
    status: str
    category_memberships: list[dict[str, str]]
    high_priority_alerts: int
    pending_approvals: int
    important_tasks: int
    health: Literal["ATTENTION", "UNKNOWN"]
    live_status: Literal["NOT_CONNECTED"]
    freshness: Literal["FRESH", "DELAYED", "STALE", "UNKNOWN"]
    last_updated: str | None


class OwnerShopPageModel(StrictModel):
    items: list[OwnerShopModel]
    total: int
    next_offset: int | None
    limit: int


class OwnerShopDetailModel(StrictModel):
    shop: OwnerShopModel
    attention_items: list[OwnerInboxItemModel]
    approvals: list[OwnerApprovalModel]
    business_data_status: Literal["NOT_CONNECTED"]


class OwnerLiveStatusModel(StrictModel):
    status: Literal["NOT_CONNECTED"]
    freshness: Literal["UNKNOWN"]
    last_updated: None = None
    message: str


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
