"""Owner-facing read models composed from existing Control Plane truth."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from core.contracts import DeviceSessionV1, ScopeV1

from .auth import RequestIdentity
from .errors import ControlPlaneError, scope_denied
from .services import parse_time

if TYPE_CHECKING:
    from .app import ControlPlane


_TERMINAL_TASKS = {"DONE", "CANCELLED", "EXPIRED"}
_ACTIVE_ALERTS = {"OPEN", "ACKNOWLEDGED"}
_HIGH_ALERT_PRIORITIES = {"P0", "P1"}
_RECORD_LIMIT = 5000
_PAGE_SIZE = 500


def _impact_available(value: dict[str, Any] | None) -> bool:
    return bool(
        value
        and any(
            value.get(field) is not None
            for field in (
                "gmv_impact",
                "profit_impact",
                "inventory_impact",
                "customer_impact",
                "live_impact",
            )
        )
    )


def _freshness(updated_at: str | None, now: datetime) -> str:
    if not updated_at:
        return "UNKNOWN"
    try:
        age = (now - parse_time(updated_at)).total_seconds()
    except (TypeError, ValueError):
        return "UNKNOWN"
    if age < 0:
        return "UNKNOWN"
    if age < 15 * 60:
        return "FRESH"
    if age < 60 * 60:
        return "DELAYED"
    return "STALE"


def _scope_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key != "schema_version"}


def _task_priority(value: int) -> int:
    return 4 if value >= 75 else 3 if value >= 50 else 2 if value >= 25 else 1


def _rank(item: dict[str, Any]) -> tuple[int, int, str, str, str]:
    priority = item["priority"]
    if item["item_type"] == "ALERT":
        priority_rank = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}[str(priority)]
    elif item["item_type"] == "APPROVAL":
        priority_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}[str(priority)]
    else:
        priority_rank = _task_priority(int(priority))
    return (
        -priority_rank,
        -int(_impact_available(item.get("business_impact"))),
        item["created_at"],
        item["item_type"],
        item["item_id"],
    )


class OwnerReadModels:
    """Aggregation-only facade; all underlying reads retain service RBAC."""

    def __init__(self, control: ControlPlane) -> None:
        self.control = control

    def _require_identity(self, identity: RequestIdentity) -> None:
        self.control.tasks._require_auth(identity)
        self.control.tasks._actor(identity)

    def _scopes(self, identity: RequestIdentity, capability: str) -> list[ScopeV1]:
        self._require_identity(identity)
        scopes = [
            scope
            for scope in self.control.registry.list_actor_scopes(
                identity.actor_id,
                capability,
            )
            if scope.organization_id == identity.organization_id
            and self.control.registry.actor_can_access(
                identity.actor_id,
                scope,
                capability,
                include_archived=True,
            )
        ]
        if not scopes:
            raise scope_denied()
        unique = {scope.canonical_json(): scope for scope in scopes}
        return [unique[key] for key in sorted(unique)]

    def _collect(
        self,
        identity: RequestIdentity,
        *,
        capability: str,
        list_method: Callable[..., tuple[list[dict[str, Any]], int | None]],
        id_field: str,
        status: str | None = None,
        priority: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        records: dict[str, dict[str, Any]] = {}
        truncated = False
        for scope in self._scopes(identity, capability):
            offset = 0
            while len(records) < _RECORD_LIMIT:
                options: dict[str, Any] = {
                    "scope": scope,
                    "status": status,
                    "created_after": None,
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                }
                if capability == "alert.read":
                    options["priority"] = priority
                items, next_offset = list_method(identity, **options)
                for item in items:
                    records.setdefault(item[id_field], item)
                if next_offset is None:
                    break
                offset = next_offset
            if len(records) >= _RECORD_LIMIT:
                truncated = True
                break
        return list(records.values()), truncated

    def _shops(self, identity: RequestIdentity) -> list[Any]:
        self._require_identity(identity)
        output = []
        for shop in self.control.registry.list_shops(identity.organization_id):
            scope = ScopeV1.create(
                organization_id=identity.organization_id,
                shop_id=shop.shop_id,
            )
            if self.control.registry.actor_can_access(
                identity.actor_id,
                scope,
                "context.read",
                include_archived=True,
            ):
                output.append(shop)
        return sorted(output, key=lambda item: (item.name.casefold(), item.shop_id))

    def _shop_names(self, identity: RequestIdentity) -> dict[str, str]:
        return {shop.shop_id: shop.name for shop in self._shops(identity)}

    def tasks(self, identity: RequestIdentity) -> tuple[list[dict[str, Any]], bool]:
        return self._collect(
            identity,
            capability="task.read",
            list_method=self.control.tasks.list,
            id_field="task_id",
        )

    def alerts(
        self,
        identity: RequestIdentity,
        *,
        status: str | None = None,
        priority: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        items, truncated = self._collect(
            identity,
            capability="alert.read",
            list_method=self.control.alerts.list,
            id_field="alert_id",
            status=status,
            priority=priority,
        )
        names = self._shop_names(identity)
        now = self.control.clock.now()
        items = [self._alert_view(item, names, now=now) for item in items]
        items.sort(
            key=lambda item: (
                {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[item["priority"]],
                -parse_time(item["created_at"]).timestamp(),
                item["alert_id"],
            )
        )
        return items, truncated

    def alert(self, identity: RequestIdentity, alert_id: str) -> dict[str, Any]:
        item = self.control.alerts.get(identity, alert_id)
        return self._alert_view(
            item,
            self._shop_names(identity),
            now=self.control.clock.now(),
        )

    @staticmethod
    def _alert_view(
        item: dict[str, Any],
        names: dict[str, str],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        scope = _scope_dict(item["scope"])
        return {
            "alert_id": item["alert_id"],
            "scope": scope,
            "priority": item["priority"],
            "status": item["status"],
            "reason_code": item["reason_code"],
            "summary": item["summary"],
            "evidence_refs": list(item.get("evidence_refs") or []),
            "business_impact": dict(item.get("business_impact") or {}),
            "recommended_action": item["recommended_action"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
            "cooldown_until": item.get("cooldown_until"),
            "shop_name": names.get(scope.get("shop_id")),
            "freshness": _freshness(item.get("updated_at"), now),
        }

    def _require_mobile_device(
        self,
        identity: RequestIdentity,
        device_id: str,
    ) -> None:
        raw = self.control.devices.ensure_active(identity, device_id)
        device = DeviceSessionV1.from_mapping(raw["device"])
        if (
            device.device_type != "MOBILE"
            or device.organization_id != identity.organization_id
            or device.user_id != identity.actor_id
        ):
            raise scope_denied("active Owner Mobile device session is required")

    def transition_alert(
        self,
        identity: RequestIdentity,
        *,
        alert_id: str,
        next_status: str,
        device_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_mobile_device(identity, device_id)
        return self.control.alerts.transition(
            identity,
            alert_id=alert_id,
            next_status=next_status,
            idempotency_key=idempotency_key,
        ).as_dict()

    def _approval_view(
        self,
        item: dict[str, Any],
        names: dict[str, str],
    ) -> dict[str, Any]:
        scope = _scope_dict(item["scope"])
        return {
            "approval_id": item["approval_id"],
            "title": "Proposal review",
            "proposal_id": item["proposal_id"],
            "scope": scope,
            "shop_name": names.get(scope.get("shop_id")),
            "risk_level": item["risk_level"],
            "requested_by": item["requested_by"],
            "requested_at": item["requested_at"],
            "reason": item["reason"],
            "business_impact": None,
            "evidence": [],
            "expires_at": item["expires_at"],
            "status": item["status"],
            "decided_by": item.get("decided_by"),
            "decided_at": item.get("decided_at"),
            "decision_note": item.get("decision_note"),
            "freshness": _freshness(
                item.get("requested_at"),
                self.control.clock.now(),
            ),
        }

    def approvals(
        self,
        identity: RequestIdentity,
        *,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        items, truncated = self._collect(
            identity,
            capability="approval.read",
            list_method=self.control.approvals.list,
            id_field="approval_id",
            status=status,
        )
        names = self._shop_names(identity)
        items = [self._approval_view(item, names) for item in items]
        items.sort(
            key=lambda item: (
                {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[item["risk_level"]],
                item["requested_at"],
                item["approval_id"],
            )
        )
        return items, truncated

    def approval(
        self,
        identity: RequestIdentity,
        approval_id: str,
    ) -> dict[str, Any]:
        item = self.control.approvals.get(identity, approval_id)
        return self._approval_view(item, self._shop_names(identity))

    def decide_approval(
        self,
        identity: RequestIdentity,
        *,
        approval_id: str,
        next_status: str,
        device_id: str,
        idempotency_key: str,
        decision_note: str | None,
    ) -> dict[str, Any]:
        self._require_mobile_device(identity, device_id)
        return self.control.approvals.decide(
            identity,
            approval_id=approval_id,
            next_status=next_status,
            idempotency_key=idempotency_key,
            decision_note=decision_note,
        ).as_dict()

    def inbox_items(self, identity: RequestIdentity) -> tuple[list[dict[str, Any]], bool]:
        tasks, tasks_truncated = self.tasks(identity)
        alerts, alerts_truncated = self.alerts(identity, status="OPEN")
        approvals, approvals_truncated = self.approvals(identity, status="PENDING")
        names = self._shop_names(identity)
        now = self.control.clock.now()
        items: list[dict[str, Any]] = []

        for task in tasks:
            if task.get("owner") != identity.actor_id or task["status"] in _TERMINAL_TASKS:
                continue
            scope = task["scope"]
            items.append(
                {
                    "item_id": task["task_id"],
                    "item_type": "TASK",
                    "owner_category": "NEED_DECISION",
                    "title": task["task_type"].replace("_", " ").strip().title(),
                    "summary": f"Owner-assigned task is {task['status'].lower().replace('_', ' ')}.",
                    "why_it_matters": "This task is explicitly assigned to you.",
                    "recommended_action": "Review the task and decide the next step.",
                    "scope": _scope_dict(scope),
                    "priority": task["priority"],
                    "business_impact": task.get("business_impact"),
                    "status": task["status"],
                    "created_at": task["created_at"],
                    "updated_at": task["updated_at"],
                    "freshness": _freshness(task.get("updated_at"), now),
                    "source_ref": task["task_id"],
                    "shop_name": names.get(scope.get("shop_id")),
                    "rank_score": None,
                    "rank_reason": None,
                }
            )

        for alert in alerts:
            if alert["priority"] not in _HIGH_ALERT_PRIORITIES:
                continue
            scope = alert["scope"]
            items.append(
                {
                    "item_id": alert["alert_id"],
                    "item_type": "ALERT",
                    "owner_category": "NEED_AWARENESS",
                    "title": alert["summary"],
                    "summary": alert["summary"],
                    "why_it_matters": (
                        "Impact not quantified yet."
                        if not _impact_available(alert.get("business_impact"))
                        else "This high-priority alert may affect the shop."
                    ),
                    "recommended_action": alert["recommended_action"],
                    "scope": _scope_dict(scope),
                    "priority": alert["priority"],
                    "business_impact": alert.get("business_impact"),
                    "status": alert["status"],
                    "created_at": alert["created_at"],
                    "updated_at": alert["updated_at"],
                    "freshness": alert["freshness"],
                    "source_ref": alert["alert_id"],
                    "shop_name": alert.get("shop_name"),
                    "rank_score": None,
                    "rank_reason": None,
                }
            )

        for approval in approvals:
            scope = approval["scope"]
            items.append(
                {
                    "item_id": approval["approval_id"],
                    "item_type": "APPROVAL",
                    "owner_category": "NEED_APPROVAL",
                    "title": approval["title"],
                    "summary": approval["reason"],
                    "why_it_matters": "A proposal is waiting for an owner decision.",
                    "recommended_action": "Review the proposal and approve, reject, or request revision.",
                    "scope": _scope_dict(scope),
                    "priority": approval["risk_level"],
                    "business_impact": None,
                    "status": approval["status"],
                    "created_at": approval["requested_at"],
                    "updated_at": approval["requested_at"],
                    "freshness": approval["freshness"],
                    "source_ref": approval["approval_id"],
                    "shop_name": approval["shop_name"],
                    "rank_score": None,
                    "rank_reason": None,
                }
            )

        items.sort(key=_rank)
        return items, tasks_truncated or alerts_truncated or approvals_truncated

    @staticmethod
    def paginate(
        items: list[dict[str, Any]],
        *,
        limit: int,
        offset: int,
        truncated: bool = False,
    ) -> dict[str, Any]:
        page = items[offset : offset + limit]
        next_offset = offset + len(page)
        more = next_offset < len(items)
        return {
            "items": page,
            "total": len(items),
            "next_offset": next_offset if more or truncated else None,
            "limit": limit,
            "truncated": truncated,
        }

    def shops(self, identity: RequestIdentity) -> list[dict[str, Any]]:
        visible = self._shops(identity)
        alert_items, _ = self.alerts(identity)
        approval_items, _ = self.approvals(identity)
        task_items, _ = self.tasks(identity)
        return [
            self._shop_model(
                identity,
                shop,
                alerts=alert_items,
                approvals=approval_items,
                tasks=task_items,
            )
            for shop in visible
        ]

    def _shop_model(
        self,
        identity: RequestIdentity,
        shop: Any,
        *,
        alerts: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        shop_alerts = [
            item
            for item in alerts
            if item["scope"].get("shop_id") == shop.shop_id
            and item["status"] in _ACTIVE_ALERTS
        ]
        shop_approvals = [
            item
            for item in approvals
            if item["scope"].get("shop_id") == shop.shop_id
            and item["status"] == "PENDING"
        ]
        shop_tasks = [
            item
            for item in tasks
            if item["scope"].get("shop_id") == shop.shop_id
            and item["status"] not in _TERMINAL_TASKS
            and item["priority"] >= 75
        ]
        updated = (
            [item["updated_at"] for item in shop_alerts]
            + [item["requested_at"] for item in shop_approvals]
            + [item["updated_at"] for item in shop_tasks]
        )
        last_updated = max(updated, key=parse_time) if updated else None
        memberships = []
        for membership in self.control.registry.get_shop_categories(shop.shop_id):
            if not membership.is_formal:
                continue
            category = self.control.registry.get_category(membership.category_id)
            memberships.append(
                {
                    "category_id": membership.category_id,
                    "name": category.name if category else membership.category_id,
                    "status": membership.status,
                    "source": membership.source,
                }
            )
        high_alert_count = sum(
            item["priority"] in _HIGH_ALERT_PRIORITIES for item in shop_alerts
        )
        return {
            "shop_id": shop.shop_id,
            "shop_name": shop.name,
            "status": shop.status,
            "category_memberships": memberships,
            "high_priority_alerts": high_alert_count,
            "pending_approvals": len(shop_approvals),
            "important_tasks": len(shop_tasks),
            "health": "ATTENTION" if high_alert_count else "UNKNOWN",
            "live_status": "NOT_CONNECTED",
            "freshness": _freshness(last_updated, self.control.clock.now()),
            "last_updated": last_updated,
        }

    def shop_detail(self, identity: RequestIdentity, shop_id: str) -> dict[str, Any]:
        self._require_identity(identity)
        scope = ScopeV1.create(
            organization_id=identity.organization_id,
            shop_id=shop_id,
        )
        self.control.tasks._authorize(
            identity,
            scope,
            "context.read",
            operation="owner.shop.read",
            target_type="shop",
            target_id=shop_id,
            allow_archived=True,
        )
        shop = self.control.registry.get_shop(shop_id)
        if shop is None:
            raise ControlPlaneError("NOT_FOUND", "shop was not found", 404)
        shop_model = next(
            item for item in self.shops(identity) if item["shop_id"] == shop_id
        )
        inbox, _ = self.inbox_items(identity)
        approvals, _ = self.approvals(identity, status="PENDING")
        return {
            "shop": shop_model,
            "attention_items": [
                item for item in inbox if item["scope"].get("shop_id") == shop_id
            ],
            "approvals": [
                item for item in approvals if item["scope"].get("shop_id") == shop_id
            ],
            "business_data_status": "NOT_CONNECTED",
        }

    def workers(self, identity: RequestIdentity) -> tuple[list[dict[str, Any]], bool]:
        self._require_identity(identity)
        org_scope = ScopeV1.create(organization_id=identity.organization_id)
        if not self.control.registry.actor_can_access(
            identity.actor_id,
            org_scope,
            "worker.read",
            include_archived=True,
        ):
            return [], False
        workers = self.control.workers.list(
            identity,
            organization_id=identity.organization_id,
        )
        now = self.control.clock.now()
        output = []
        for raw in workers:
            worker = raw["worker"]
            output.append(
                {
                    "worker_id": worker["worker_id"],
                    "worker_type": worker["worker_type"],
                    "status": worker["status"],
                    "last_seen_at": worker["last_seen_at"],
                    "capabilities": worker["capabilities"],
                    "freshness": _freshness(worker["last_seen_at"], now),
                }
            )
        return output, True

    @staticmethod
    def _worker_status(workers: list[dict[str, Any]], authorized: bool) -> str:
        if not authorized or not workers:
            return "UNKNOWN"
        statuses = {item["status"] for item in workers}
        if statuses == {"ONLINE"}:
            return "ONLINE"
        if statuses == {"OFFLINE"}:
            return "OFFLINE"
        if "UNKNOWN" in statuses:
            return "UNKNOWN"
        return "DEGRADED"

    def system_health(
        self,
        identity: RequestIdentity,
        *,
        redis_ok: bool,
    ) -> dict[str, Any]:
        self._require_identity(identity)
        adapter = self.control.adapter
        try:
            cloud_ok = bool(getattr(adapter, "health_check", lambda: True)())
            schema_version = adapter.schema_version if cloud_ok else None
            expected = getattr(adapter, "MIGRATION_VERSION", schema_version)
            cloud_ok = cloud_ok and schema_version == expected
        except Exception:
            cloud_ok = False
        now = self.control.clock.now()
        if not cloud_ok:
            return {
                "cloud_status": "OFFLINE",
                "brain_worker_status": "UNKNOWN",
                "realtime_status": "DEGRADED" if not redis_ok else "HEALTHY",
                "data_freshness": "UNKNOWN",
                "last_updated": now.isoformat(),
                "workers": [],
            }
        workers, authorized = self.workers(identity)
        worker_status = self._worker_status(workers, authorized)
        newest_worker_seen = max(
            (item["last_seen_at"] for item in workers),
            key=parse_time,
            default=None,
        )
        return {
            "cloud_status": "HEALTHY",
            "brain_worker_status": worker_status,
            "realtime_status": "HEALTHY" if redis_ok else "DEGRADED",
            "data_freshness": _freshness(newest_worker_seen, now),
            "last_updated": now.isoformat(),
            "workers": workers,
        }

    def summary(self, identity: RequestIdentity, *, redis_ok: bool) -> dict[str, Any]:
        health = self.system_health(identity, redis_ok=redis_ok)
        if health["cloud_status"] == "OFFLINE":
            return {
                "schema_version": 1,
                "organization_id": identity.organization_id,
                "overall_health": "OFFLINE",
                "attention_counts": {
                    "need_decision": None,
                    "need_approval": None,
                    "need_awareness": None,
                },
                "shop_count": None,
                "worker_health": "UNKNOWN",
                "system_health": "OFFLINE",
                "generated_at": self.control.clock.now().isoformat(),
                "freshness": "UNKNOWN",
                "counts_complete": False,
            }
        inbox, truncated = self.inbox_items(identity)
        counts = {
            "need_decision": sum(
                item["owner_category"] == "NEED_DECISION" for item in inbox
            ),
            "need_approval": sum(
                item["owner_category"] == "NEED_APPROVAL" for item in inbox
            ),
            "need_awareness": sum(
                item["owner_category"] == "NEED_AWARENESS" for item in inbox
            ),
        }
        overall = health["cloud_status"]
        if overall != "OFFLINE":
            if (
                health["realtime_status"] == "DEGRADED"
                or health["brain_worker_status"] in {"DEGRADED", "OFFLINE"}
                or counts["need_awareness"] > 0
            ):
                overall = "DEGRADED"
            elif health["brain_worker_status"] != "ONLINE":
                overall = "UNKNOWN"
            else:
                overall = "HEALTHY"
        return {
            "schema_version": 1,
            "organization_id": identity.organization_id,
            "overall_health": overall,
            "attention_counts": counts,
            "shop_count": len(self._shops(identity)),
            "worker_health": health["brain_worker_status"],
            "system_health": health["cloud_status"],
            "generated_at": self.control.clock.now().isoformat(),
            "freshness": health["data_freshness"],
            "counts_complete": not truncated,
        }

    def live_status(self, identity: RequestIdentity) -> dict[str, Any]:
        self._scopes(identity, "context.read")
        return {
            "status": "NOT_CONNECTED",
            "freshness": "UNKNOWN",
            "last_updated": None,
            "message": "Live intelligence is not connected yet.",
        }
