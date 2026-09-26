"""Backend-neutral Hermes coordinator with a proposal-only safety envelope."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Protocol

from .contracts import (
    HermesContractError,
    ROLES,
    SlowResultV1,
    SlowTaskV1,
)


class HermesBackend(Protocol):
    def review(self, *, task: SlowTaskV1, role: str, shared_state: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


class UnavailableHermesBackend:
    """Explicit adapter boundary; it never starts or edits the Hermes runtime."""

    def review(self, *, task: SlowTaskV1, role: str, shared_state: Mapping[str, Any]) -> Mapping[str, Any]:
        del task, role, shared_state
        return {
            "status": "UNAVAILABLE",
            "summary": "Hermes backend is not connected.",
            "findings": [],
            "recommended_actions": [],
            "policy_drafts": [],
            "feature_suggestions": [],
            "data_quality_issues": [],
            "role_trace": [],
        }


class FixtureHermesBackend:
    """Deterministic local rehearsal backend for contract tests."""

    def review(self, *, task: SlowTaskV1, role: str, shared_state: Mapping[str, Any]) -> Mapping[str, Any]:
        del shared_state
        return {
            "status": "DRAFT",
            "summary": f"Fixture review for {task.task_type} by {role}.",
            "findings": [{"code": "FIXTURE_REVIEW", "role": role, "evidence_refs": []}],
            "recommended_actions": [
                {
                    "kind": "human_review",
                    "title": "人工复核建议",
                    "risk_level": "low",
                    "can_execute": False,
                }
            ],
            "policy_drafts": [
                {
                    "lifecycle": "DRAFT",
                    "title": "Fixture policy draft",
                    "requires_human_review": True,
                    "can_execute": False,
                }
            ]
            if role == "strategy"
            else [],
            "feature_suggestions": [],
            "data_quality_issues": [],
            "role_trace": [role],
        }


def role_catalog() -> dict[str, dict[str, Any]]:
    return {
        "coordinator": {"purpose": "收敛证据并选择一个子角色", "can_delegate": True},
        "data": {"purpose": "检查数据完整性、新鲜度和冲突", "can_delegate": False},
        "live_review": {"purpose": "复核直播状态与趋势", "can_delegate": False},
        "product": {"purpose": "复核商品卡和库存风险", "can_delegate": False},
        "strategy": {"purpose": "形成观察建议和 DRAFT policy", "can_delegate": False},
    }


class HermesCoordinator:
    """Run Coordinator -> one subagent -> Coordinator-style bounded review."""

    def __init__(self, backend: HermesBackend | None = None) -> None:
        self.backend = backend or UnavailableHermesBackend()

    def run(self, task: SlowTaskV1 | Mapping[str, Any]) -> dict[str, Any]:
        request = task if isinstance(task, SlowTaskV1) else SlowTaskV1.from_mapping(task)
        roles = role_catalog()
        if set(roles) != set(ROLES):
            raise HermesContractError("role catalog must contain exactly five roles")
        shared_state: dict[str, Any] = {
            "task_id": request.task_id,
            "task_hash": request.task_hash,
            "scope": deepcopy(request.scope),
            "completed_roles": [],
        }
        trace: list[str] = []
        role = request.requested_role
        latest: Mapping[str, Any] | None = None
        for _ in range(request.max_steps):
            raw = self.backend.review(task=request, role=role, shared_state=deepcopy(shared_state))
            if not isinstance(raw, Mapping):
                raise HermesContractError("Hermes backend result must be an object")
            raw_value = dict(raw)
            raw_value["task_id"] = request.task_id
            raw_value["task_hash"] = request.task_hash
            raw_value["schema_version"] = 1
            raw_value["role_trace"] = trace + [role]
            result = SlowResultV1.from_mapping(raw_value, task=request)
            latest = result.as_dict()
            trace.append(role)
            shared_state["completed_roles"].append(role)
            if result.status != "DRAFT" or role != "coordinator" or request.max_steps < 2:
                break
            role = "data" if request.task_type in {"DAILY_REVIEW", "CAMPAIGN_REVIEW"} else (
                "live_review" if request.task_type == "LIVE_REVIEW" else "product"
            )
        if latest is None:
            raise HermesContractError("Hermes coordinator produced no result")
        latest["role_trace"] = trace
        return latest


__all__ = [
    "FixtureHermesBackend",
    "HermesBackend",
    "HermesCoordinator",
    "UnavailableHermesBackend",
    "role_catalog",
]
