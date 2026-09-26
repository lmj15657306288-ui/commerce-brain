"""Strict Hermes Slow Loop contracts.

The contract is backend-neutral. It accepts only bounded, already-sanitized
Commerce Brain summaries and can only describe drafts for human review.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA_VERSION = 1
TASK_TYPES = frozenset({"DAILY_REVIEW", "LIVE_REVIEW", "PRODUCT_REVIEW", "CAMPAIGN_REVIEW"})
ROLES = ("coordinator", "data", "live_review", "product", "strategy")
RESULT_STATUSES = frozenset({"DRAFT", "BLOCKED", "UNAVAILABLE"})
POLICY_LIFECYCLE = ("DRAFT", "REVIEWED", "SHADOW", "APPROVED", "ACTIVE", "RETIRED")
_SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FORBIDDEN = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "browser_action",
        "browser_actions",
        "command",
        "cookie",
        "cookies",
        "css_selector",
        "dom",
        "dom_selector",
        "execute",
        "execution",
        "html",
        "password",
        "raw_html",
        "screenshot",
        "script",
        "secret",
        "session",
        "shell",
        "sql",
        "token",
        "write",
    }
)


class HermesContractError(ValueError):
    """Raised when a Hermes contract is invalid or unsafe."""


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise HermesContractError(f"{field} must be bounded text")
    return value.strip()


def _ref(value: Any, field: str) -> str:
    value = _text(value, field, 128)
    if not _SAFE_REF.fullmatch(value):
        raise HermesContractError(f"{field} must be a safe reference")
    return value


def _safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise HermesContractError("payload nesting is too deep")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise HermesContractError("payload numbers must be finite")
        return value
    if isinstance(value, str):
        if len(value) > 1000:
            raise HermesContractError("payload text is too long")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key).strip()
            lowered = key.lower().replace("-", "_").replace(" ", "_")
            if (
                not _SAFE_KEY.fullmatch(key)
                or lowered in _FORBIDDEN
                or lowered.endswith("_token")
                or lowered.endswith("_secret")
            ):
                raise HermesContractError("payload contains a forbidden field")
            if lowered in {"can_execute", "execution_allowed", "platform_write_attempted"} and child is not False:
                raise HermesContractError("execution flags must remain false")
            output[key] = _safe(child, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            raise HermesContractError("payload list is too long")
        return [_safe(item, depth=depth + 1) for item in value]
    raise HermesContractError("payload contains an unsupported value")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class SlowTaskV1:
    schema_version: int
    task_id: str
    task_type: str
    scope: dict[str, str]
    period: dict[str, str]
    inputs: dict[str, Any]
    requested_role: str
    max_steps: int
    created_at: str
    task_hash: str
    execution_allowed: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SlowTaskV1":
        if not isinstance(value, Mapping):
            raise HermesContractError("SlowTask must be an object")
        allowed = {
            "schema_version",
            "task_id",
            "task_type",
            "scope",
            "period",
            "inputs",
            "requested_role",
            "max_steps",
            "created_at",
            "task_hash",
            "execution_allowed",
        }
        if set(value) - allowed:
            raise HermesContractError("SlowTask contains unsupported fields")
        if value.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise HermesContractError("SlowTask schema is unsupported")
        if value.get("execution_allowed", False) is not False:
            raise HermesContractError("SlowTask execution is permanently disabled")
        task_id = _ref(value.get("task_id"), "task_id")
        task_type = _text(value.get("task_type"), "task_type", 40).upper()
        if task_type not in TASK_TYPES:
            raise HermesContractError("task_type is unsupported")
        scope_raw = value.get("scope")
        if not isinstance(scope_raw, Mapping) or not scope_raw:
            raise HermesContractError("scope must be a non-empty object")
        scope = {str(key): _ref(item, f"scope.{key}") for key, item in scope_raw.items()}
        period_raw = value.get("period")
        if not isinstance(period_raw, Mapping):
            raise HermesContractError("period must be an object")
        if set(period_raw) != {"from", "to"}:
            raise HermesContractError("period must contain only from and to")
        period = {
            "from": _text(period_raw.get("from"), "period.from", 80),
            "to": _text(period_raw.get("to"), "period.to", 80),
        }
        inputs = _safe(value.get("inputs"))
        if not isinstance(inputs, dict):
            raise HermesContractError("inputs must be an object")
        role = _text(value.get("requested_role", "coordinator"), "requested_role", 32).lower()
        if role not in ROLES:
            raise HermesContractError("requested_role is unsupported")
        max_steps = value.get("max_steps", 2)
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 1 <= max_steps <= 5:
            raise HermesContractError("max_steps must be between 1 and 5")
        created_at = _text(value.get("created_at") or _now(), "created_at", 80)
        canonical = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "task_type": task_type,
            "scope": scope,
            "period": period,
            "inputs": inputs,
            "requested_role": role,
            "max_steps": max_steps,
            "created_at": created_at,
        }
        provided_hash = value.get("task_hash")
        computed_hash = _hash(canonical)
        if provided_hash is not None and provided_hash != computed_hash:
            raise HermesContractError("SlowTask task hash does not match payload")
        return cls(
            schema_version=SCHEMA_VERSION,
            task_id=task_id,
            task_type=task_type,
            scope=scope,
            period=period,
            inputs=inputs,
            requested_role=role,
            max_steps=max_steps,
            created_at=created_at,
            task_hash=computed_hash,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "scope": copy.deepcopy(self.scope),
            "period": copy.deepcopy(self.period),
            "inputs": copy.deepcopy(self.inputs),
            "requested_role": self.requested_role,
            "max_steps": self.max_steps,
            "created_at": self.created_at,
            "task_hash": self.task_hash,
            "execution_allowed": False,
        }


@dataclass(frozen=True, slots=True)
class SlowResultV1:
    schema_version: int
    task_id: str
    task_hash: str
    status: str
    summary: str
    findings: tuple[dict[str, Any], ...]
    recommended_actions: tuple[dict[str, Any], ...]
    policy_drafts: tuple[dict[str, Any], ...]
    feature_suggestions: tuple[dict[str, Any], ...]
    data_quality_issues: tuple[dict[str, Any], ...]
    role_trace: tuple[str, ...]
    safety: dict[str, bool | str]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, task: SlowTaskV1) -> "SlowResultV1":
        if not isinstance(value, Mapping):
            raise HermesContractError("SlowResult must be an object")
        allowed = {
            "schema_version",
            "task_id",
            "task_hash",
            "status",
            "summary",
            "findings",
            "recommended_actions",
            "policy_drafts",
            "feature_suggestions",
            "data_quality_issues",
            "role_trace",
        }
        if set(value) - allowed:
            raise HermesContractError("SlowResult contains unsupported fields")
        if value.get("schema_version") != SCHEMA_VERSION or value.get("task_id") != task.task_id:
            raise HermesContractError("SlowResult identity does not match task")
        if value.get("task_hash") != task.task_hash:
            raise HermesContractError("SlowResult task hash does not match task")
        status = _text(value.get("status"), "status", 20).upper()
        if status not in RESULT_STATUSES:
            raise HermesContractError("status is unsupported")
        summary = _text(value.get("summary") or "未形成可验证结论", "summary", 2000)
        rows: dict[str, tuple[dict[str, Any], ...]] = {}
        for field in (
            "findings",
            "recommended_actions",
            "policy_drafts",
            "feature_suggestions",
            "data_quality_issues",
        ):
            raw_rows = value.get(field, [])
            if not isinstance(raw_rows, list) or len(raw_rows) > 50:
                raise HermesContractError(f"{field} must be a bounded array")
            normalized = tuple(item for item in (_safe(row) for row in raw_rows) if isinstance(item, dict))
            rows[field] = normalized
        raw_trace = value.get("role_trace", [])
        if not isinstance(raw_trace, list) or any(item not in ROLES for item in raw_trace):
            raise HermesContractError("role_trace is invalid")
        safety = {
            "mode": "proposal_only",
            "execution_allowed": False,
            "can_execute": False,
            "execution_performed": False,
            "platform_write_attempted": False,
            "browser_action_allowed": False,
        }
        if status != "DRAFT" and any(rows[field] for field in ("recommended_actions", "policy_drafts", "feature_suggestions")):
            raise HermesContractError("blocked or unavailable result cannot contain actionable drafts")
        return cls(
            schema_version=SCHEMA_VERSION,
            task_id=task.task_id,
            task_hash=task.task_hash,
            status=status,
            summary=summary,
            findings=rows["findings"],
            recommended_actions=rows["recommended_actions"],
            policy_drafts=rows["policy_drafts"],
            feature_suggestions=rows["feature_suggestions"],
            data_quality_issues=rows["data_quality_issues"],
            role_trace=tuple(raw_trace),
            safety=safety,
        )

    def as_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_hash": self.task_hash,
            "status": self.status,
            "summary": self.summary,
            "findings": copy.deepcopy(list(self.findings)),
            "recommended_actions": copy.deepcopy(list(self.recommended_actions)),
            "policy_drafts": copy.deepcopy(list(self.policy_drafts)),
            "feature_suggestions": copy.deepcopy(list(self.feature_suggestions)),
            "data_quality_issues": copy.deepcopy(list(self.data_quality_issues)),
            "role_trace": list(self.role_trace),
            "safety": copy.deepcopy(self.safety),
        }
        return result


def build_task(*, task_id: str, task_type: str, scope: Mapping[str, str], period: Mapping[str, str], inputs: Mapping[str, Any], requested_role: str = "coordinator", max_steps: int = 2) -> SlowTaskV1:
    return SlowTaskV1.from_mapping(
        {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "task_type": task_type,
            "scope": dict(scope),
            "period": dict(period),
            "inputs": dict(inputs),
            "requested_role": requested_role,
            "max_steps": max_steps,
        }
    )


__all__ = [
    "POLICY_LIFECYCLE",
    "RESULT_STATUSES",
    "ROLES",
    "SCHEMA_VERSION",
    "SlowResultV1",
    "SlowTaskV1",
    "HermesContractError",
    "build_task",
]
