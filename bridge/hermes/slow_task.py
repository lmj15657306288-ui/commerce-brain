"""Strict, proposal-only contract for the Hermes Slow Brain loop.

This module is intentionally backend-neutral.  The installed Hermes runtime
can be connected behind ``backend`` later; the contract and safety envelope do
not depend on a provider being available.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

SLOW_TASK_SCHEMA_VERSION = 1
SLOW_RESULT_SCHEMA_VERSION = 1
HERMES_ROLES = (
    "coordinator",
    "data",
    "live_review",
    "product",
    "strategy",
)
DRAFT_KINDS = (
    "review",
    "report",
    "action_card",
    "policy_draft",
    "feature_suggestion",
)
TASK_STATUSES = frozenset({"draft", "blocked", "unavailable"})
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FORBIDDEN_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "dom",
        "html",
        "password",
        "raw_html",
        "screenshot",
        "script",
        "secret",
        "session",
        "token",
    }
)
_FORBIDDEN_RESULT_KEYS = _FORBIDDEN_KEYS | frozenset(
    {
        "browser_action",
        "browser_actions",
        "command",
        "commands",
        "css_selector",
        "dom_selector",
        "execute",
        "execution",
        "shell",
        "sql",
        "write",
    }
)


class SlowTaskContractError(ValueError):
    """Raised when a SlowTask or draft violates the local contract."""


def _require_ref(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value.strip()):
        raise SlowTaskContractError(f"{field} must be a safe reference")
    return value.strip()


def _require_text(value: Any, field: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise SlowTaskContractError(f"{field} must be bounded text")
    return value.strip()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _safe_value(value: Any, *, depth: int = 0, result: bool = False) -> Any:
    if depth > 8:
        raise SlowTaskContractError("payload nesting is too deep")
    if value is None or isinstance(value, bool):
        return value
    if _finite_number(value):
        return value
    if isinstance(value, str):
        if len(value) > 500:
            raise SlowTaskContractError("payload text is too long")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key).strip()
            lowered = key.lower().replace("-", "_").replace(" ", "_")
            forbidden = _FORBIDDEN_RESULT_KEYS if result else _FORBIDDEN_KEYS
            if (
                not _SAFE_KEY.fullmatch(key)
                or lowered in forbidden
                or lowered.endswith("_token")
                or lowered.endswith("_secret")
            ):
                raise SlowTaskContractError("payload contains a forbidden field")
            output[key] = _safe_value(child, depth=depth + 1, result=result)
        return output
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            raise SlowTaskContractError("payload list is too long")
        return [_safe_value(item, depth=depth + 1, result=result) for item in value]
    raise SlowTaskContractError("payload contains an unsupported value")


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _evidence_quality(evidence: Mapping[str, Any]) -> tuple[bool, int]:
    quality = evidence.get("quality") if isinstance(evidence.get("quality"), Mapping) else {}
    fresh = quality.get("fresh")
    score = quality.get("score")
    if not isinstance(fresh, bool) or not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise SlowTaskContractError("evidence quality must contain fresh and score")
    return fresh, score


@dataclass(frozen=True, slots=True)
class SlowTaskV1:
    schema_version: int
    task_id: str
    objective: str
    requested_role: str
    context: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    max_steps: int
    created_at: str
    task_hash: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SlowTaskV1":
        if not isinstance(value, Mapping):
            raise SlowTaskContractError("SlowTask must be an object")
        allowed = {
            "schema_version",
            "task_id",
            "objective",
            "requested_role",
            "context",
            "evidence",
            "max_steps",
            "created_at",
            "task_hash",
            "execution_allowed",
        }
        unknown = set(value) - allowed
        if unknown:
            raise SlowTaskContractError("SlowTask contains unsupported fields")
        if value.get("schema_version", SLOW_TASK_SCHEMA_VERSION) != SLOW_TASK_SCHEMA_VERSION:
            raise SlowTaskContractError("SlowTask schema is unsupported")
        if value.get("execution_allowed", False) is not False:
            raise SlowTaskContractError("SlowTask execution is permanently disabled")
        task_id = _require_ref(value.get("task_id"), "task_id")
        objective = _require_text(value.get("objective"), "objective", limit=500)
        role = str(value.get("requested_role") or "coordinator").strip().lower()
        if role not in HERMES_ROLES:
            raise SlowTaskContractError("requested_role is unsupported")
        context = _safe_value(value.get("context") or {})
        if not isinstance(context, dict):
            raise SlowTaskContractError("context must be an object")
        raw_evidence = value.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence or len(raw_evidence) > 40:
            raise SlowTaskContractError("evidence must be a non-empty bounded list")
        evidence: list[dict[str, Any]] = []
        for item in raw_evidence:
            if not isinstance(item, Mapping):
                raise SlowTaskContractError("evidence items must be objects")
            row = _safe_value(item)
            if not isinstance(row, dict):
                raise SlowTaskContractError("evidence item must be an object")
            row["evidence_ref"] = _require_ref(row.get("evidence_ref"), "evidence_ref")
            _require_text(row.get("source"), "evidence.source", limit=120)
            _evidence_quality(row)
            evidence.append(row)
        max_steps = value.get("max_steps", 2)
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 1 <= max_steps <= 5:
            raise SlowTaskContractError("max_steps must be between 1 and 5")
        created_at = str(value.get("created_at") or datetime.now(timezone.utc).isoformat())
        _require_text(created_at, "created_at", limit=80)
        canonical = {
            "schema_version": SLOW_TASK_SCHEMA_VERSION,
            "task_id": task_id,
            "objective": objective,
            "requested_role": role,
            "context": context,
            "evidence": evidence,
            "max_steps": max_steps,
            "created_at": created_at,
        }
        computed_hash = _payload_hash(canonical)
        provided_hash = value.get("task_hash")
        if provided_hash is not None and provided_hash != computed_hash:
            raise SlowTaskContractError("SlowTask task hash does not match payload")
        return cls(
            schema_version=SLOW_TASK_SCHEMA_VERSION,
            task_id=task_id,
            objective=objective,
            requested_role=role,
            context=context,
            evidence=tuple(copy.deepcopy(evidence)),
            max_steps=max_steps,
            created_at=created_at,
            task_hash=computed_hash,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "objective": self.objective,
            "requested_role": self.requested_role,
            "context": copy.deepcopy(self.context),
            "evidence": copy.deepcopy(list(self.evidence)),
            "max_steps": self.max_steps,
            "created_at": self.created_at,
            "task_hash": self.task_hash,
        }


class SlowBrainBackend(Protocol):
    def generate(self, *, task: SlowTaskV1, role: str, shared_state: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


class UnavailableHermesBackend:
    """Explicit adapter boundary when the Hermes runtime is not invoked."""

    def generate(self, *, task: SlowTaskV1, role: str, shared_state: Mapping[str, Any]) -> Mapping[str, Any]:
        del task, role, shared_state
        return {"status": "unavailable", "reason": "hermes_backend_not_connected"}


class FixtureHermesBackend:
    """Deterministic local backend for contract tests and offline rehearsal."""

    def generate(self, *, task: SlowTaskV1, role: str, shared_state: Mapping[str, Any]) -> Mapping[str, Any]:
        del shared_state
        return {
            "status": "draft",
            "draft_kind": "review" if role != "strategy" else "policy_draft",
            "title": f"{role} review for {task.task_id}",
            "summary": f"Fixture-only review of: {task.objective}",
            "evidence_refs": [item["evidence_ref"] for item in task.evidence],
            "confidence": 0.7,
            "open_questions": [],
            "next_role": "data" if role == "coordinator" and task.max_steps > 1 else None,
        }


def build_role_catalog() -> dict[str, dict[str, Any]]:
    return {
        "coordinator": {
            "purpose": "按证据完整性选择下一步 Slow Brain 角色并收敛输出",
            "allowed_outputs": ["review", "report", "action_card"],
        },
        "data": {
            "purpose": "检查数据新鲜度、完整性、冲突和可比较性",
            "allowed_outputs": ["review", "report"],
        },
        "live_review": {
            "purpose": "复核直播状态、窗口指标和待确认信号",
            "allowed_outputs": ["review", "action_card"],
        },
        "product": {
            "purpose": "复核商品表现与商品侧风险",
            "allowed_outputs": ["review", "action_card", "feature_suggestion"],
        },
        "strategy": {
            "purpose": "基于已验证证据形成策略草案和下一步观察建议",
            "allowed_outputs": ["report", "policy_draft", "feature_suggestion"],
        },
    }


def _quality_gate(task: SlowTaskV1) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for evidence in task.evidence:
        fresh, score = _evidence_quality(evidence)
        if not fresh:
            errors.append(f"stale:{evidence['evidence_ref']}")
        if score < 70:
            errors.append(f"low_quality:{evidence['evidence_ref']}")
    return not errors, errors


def _sealed_result(
    *,
    task: SlowTaskV1,
    role: str,
    raw: Mapping[str, Any],
    shared_state: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SlowTaskContractError("backend result must be an object")
    safe = _safe_value(raw, result=True)
    status = str(safe.get("status") or "blocked")
    if status not in TASK_STATUSES:
        status = "blocked"
    draft_kind = str(safe.get("draft_kind") or "review")
    if draft_kind not in DRAFT_KINDS:
        status = "blocked"
        draft_kind = "review"
    if status == "draft":
        title = _require_text(safe.get("title"), "draft.title", limit=200)
        summary = _require_text(safe.get("summary"), "draft.summary", limit=1200)
    else:
        title = str(safe.get("title") or "Slow Brain 未生成草案")[:200]
        summary = str(safe.get("summary") or "当前无法形成可验证草案。")[:1200]
    refs = safe.get("evidence_refs") if isinstance(safe.get("evidence_refs"), list) else []
    allowed_refs = {item["evidence_ref"] for item in task.evidence}
    evidence_refs = [_require_ref(ref, "evidence_ref") for ref in refs if ref in allowed_refs]
    confidence = safe.get("confidence", 0.0)
    if not _finite_number(confidence) or not 0 <= float(confidence) <= 1:
        confidence = 0.0
    next_role = safe.get("next_role")
    if next_role not in HERMES_ROLES:
        next_role = None
    return {
        "schema_version": SLOW_RESULT_SCHEMA_VERSION,
        "task_id": task.task_id,
        "task_hash": task.task_hash,
        "role": role,
        "status": status,
        "draft": status == "draft",
        "draft_kind": draft_kind,
        "title": title,
        "summary": summary,
        "evidence_refs": evidence_refs,
        "confidence": round(float(confidence), 4),
        "open_questions": [
            str(item)[:300]
            for item in (safe.get("open_questions") if isinstance(safe.get("open_questions"), list) else [])
            if isinstance(item, str)
        ][:20],
        "next_role": next_role,
        "shared_state_hash": _payload_hash(shared_state),
        "safety": {
            "mode": "proposal_only",
            "execution_allowed": False,
            "can_execute": False,
            "execution_performed": False,
            "browser_action_allowed": False,
            "platform_write_attempted": False,
            "strong_conclusion_allowed": status == "draft",
        },
    }


class HermesSlowLoop:
    """Run one bounded role at a time behind a proposal-only envelope."""

    def __init__(
        self,
        backend: SlowBrainBackend | None = None,
        *,
        role_catalog: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        self.backend = backend or UnavailableHermesBackend()
        self.role_catalog = {key: dict(value) for key, value in (role_catalog or build_role_catalog()).items()}

    def run(self, task: SlowTaskV1 | Mapping[str, Any]) -> dict[str, Any]:
        request = task if isinstance(task, SlowTaskV1) else SlowTaskV1.from_mapping(task)
        if set(self.role_catalog) != set(HERMES_ROLES):
            raise SlowTaskContractError("role catalog must contain exactly five Hermes roles")
        ready, reasons = _quality_gate(request)
        if not ready:
            return {
                "schema_version": SLOW_RESULT_SCHEMA_VERSION,
                "task_id": request.task_id,
                "task_hash": request.task_hash,
                "role": request.requested_role,
                "status": "blocked",
                "draft": False,
                "draft_kind": "review",
                "title": "证据不足，禁止总结",
                "summary": "数据新鲜度或质量未达到 Slow Brain 门槛。",
                "evidence_refs": [],
                "confidence": 0.0,
                "open_questions": reasons,
                "next_role": None,
                "shared_state_hash": _payload_hash({"blocked": reasons}),
                "safety": {
                    "mode": "proposal_only",
                    "execution_allowed": False,
                    "can_execute": False,
                    "execution_performed": False,
                    "browser_action_allowed": False,
                    "platform_write_attempted": False,
                    "strong_conclusion_allowed": False,
                },
            }

        shared_state: dict[str, Any] = {
            "task_id": request.task_id,
            "task_hash": request.task_hash,
            "completed_steps": [],
            "evidence_refs": [item["evidence_ref"] for item in request.evidence],
        }
        role = request.requested_role
        latest: dict[str, Any] | None = None
        for _ in range(request.max_steps):
            raw = self.backend.generate(task=request, role=role, shared_state=copy.deepcopy(shared_state))
            result = _sealed_result(task=request, role=role, raw=raw, shared_state=shared_state)
            shared_state["completed_steps"].append(
                {
                    "role": role,
                    "status": result["status"],
                    "draft_kind": result["draft_kind"],
                }
            )
            latest = result
            next_role = result.get("next_role")
            if result["status"] != "draft" or not next_role or next_role == role:
                break
            role = next_role
        assert latest is not None
        latest["shared_state_hash"] = _payload_hash(shared_state)
        return latest


__all__ = [
    "DRAFT_KINDS",
    "HERMES_ROLES",
    "FixtureHermesBackend",
    "HermesSlowLoop",
    "SlowTaskContractError",
    "SlowTaskV1",
    "UnavailableHermesBackend",
    "build_role_catalog",
]
