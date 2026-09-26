"""Proposal-only Fast Brain tools for the local MCP server."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from decision_provider import (
    DecisionProvider,
    DecisionProviderError,
    LayaProvider,
    RulesProvider,
    decide_live_state,
)
from live_state import LiveStateSnapshot, LIVE_METRICS, SUPPORTED_WINDOWS

FAST_TOOL_NAMES = frozenset(
    {
        "fast_choice",
        "fast_binary",
        "fast_score",
        "route_task",
        "classify_live_state",
    }
)

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FORBIDDEN_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "html",
    "raw_html",
    "password",
    "script",
    "secret",
    "session",
    "token",
}
_PROVIDERS = {"laya", "rules"}
_ROUTES = {
    "fast",
    "fast_brain",
    "live",
    "live_state",
    "classification",
    "slow",
    "slow_brain",
    "analysis",
    "strategy",
    "review",
    "human",
    "human_action",
    "outcome",
}


class FastToolInputError(ValueError):
    """Raised when an MCP Fast Brain payload is not safely bounded."""


def _finite_scalar(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, bool)
        or (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
        or (isinstance(value, str) and len(value) <= 160)
    )


def _safe_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FastToolInputError("state.metrics must be an object")
    metrics: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = str(raw_key).strip()
        if not _SAFE_KEY.fullmatch(key) or key.lower() in _FORBIDDEN_KEYS or key.lower().endswith("_token"):
            raise FastToolInputError("state.metrics contains an unsupported field")
        if not _finite_scalar(item):
            raise FastToolInputError("state.metrics must contain bounded scalar values")
        metrics[key] = item
    if not metrics:
        raise FastToolInputError("state.metrics must not be empty")
    return metrics


def _safe_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FastToolInputError("state must be an object")
    metrics = value.get("metrics", value)
    normalized = {"metrics": _safe_metrics(metrics)}
    snapshot_id = value.get("snapshot_id")
    if snapshot_id is not None:
        if not isinstance(snapshot_id, str) or not 1 <= len(snapshot_id) <= 120:
            raise FastToolInputError("snapshot_id must be bounded text")
        normalized["snapshot_id"] = snapshot_id
    return normalized


def _provider(name: Any) -> DecisionProvider:
    selected = str(name or "laya").strip().lower()
    if selected not in _PROVIDERS:
        raise FastToolInputError("provider must be laya or rules")
    return LayaProvider() if selected == "laya" else RulesProvider()


def _decision(state: Any, provider: Any) -> dict[str, Any]:
    normalized = _safe_state(state)
    selected_provider = provider or "laya"
    try:
        return _provider(selected_provider).decide(normalized)
    except DecisionProviderError as exc:
        raise FastToolInputError(str(exc)) from exc


def _decision_view(result: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {
        field: result.get(field),
        "value": result.get(field),
        "choice": result.get("choice"),
        "confidence": result.get("confidence"),
        "reason_code": result.get("reason_code"),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "checkpoint": result.get("checkpoint"),
        "state_hash": result.get("state_hash"),
        "latency_ms": result.get("latency_ms"),
    }


def _live_snapshot(value: Any) -> LiveStateSnapshot:
    if not isinstance(value, Mapping):
        raise FastToolInputError("snapshot must be an object")
    metrics = _safe_metrics(value.get("metrics"))
    unknown = set(metrics) - set(LIVE_METRICS)
    if unknown:
        raise FastToolInputError("snapshot.metrics contains unsupported live metrics")
    required = {"schema_version", "snapshot_id", "captured_at", "source", "window_seconds", "evidence_hash"}
    missing = required - set(value)
    if missing:
        raise FastToolInputError(f"snapshot is missing fields: {sorted(missing)}")
    window_seconds = value["window_seconds"]
    if window_seconds not in SUPPORTED_WINDOWS:
        raise FastToolInputError("snapshot.window_seconds is unsupported")
    fields = {
        "schema_version": value["schema_version"],
        "snapshot_id": value["snapshot_id"],
        "captured_at": value["captured_at"],
        "source": value["source"],
        "window_seconds": window_seconds,
        "metrics": {key: metrics.get(key) for key in LIVE_METRICS},
        "evidence_hash": value["evidence_hash"],
    }
    if fields["schema_version"] != 1 or any(
        not isinstance(fields[key], str) or not fields[key].strip()
        for key in ("snapshot_id", "captured_at", "source", "evidence_hash")
    ):
        raise FastToolInputError("snapshot metadata is invalid")
    return LiveStateSnapshot(**fields)


def invoke_fast_tool(name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Invoke one bounded Fast Brain operation without authorizing execution."""

    args = dict(arguments or {})
    if name == "fast_choice":
        return _decision_view(_decision(args.get("state"), args.get("provider")), "choice")
    if name == "fast_binary":
        return _decision_view(_decision(args.get("state"), args.get("provider")), "binary")
    if name == "fast_score":
        return _decision_view(_decision(args.get("state"), args.get("provider")), "score")
    if name == "route_task":
        task = str(args.get("task") or "").strip().lower()
        if task not in _ROUTES:
            raise FastToolInputError("task is not a supported route")
        route = "fast_brain" if task in {"fast", "fast_brain", "live", "live_state", "classification"} else (
            "human" if task in {"human", "human_action", "outcome"} else "slow_brain"
        )
        decision = None
        if args.get("state") is not None:
            decision = _decision(args.get("state"), args.get("provider"))
            if decision["choice"] == "ESCALATE_SLOW_BRAIN":
                route = "slow_brain"
        return {
            "route": route,
            "task": task,
            "decision": decision,
            "reason_code": "ACTION_ESCALATES_TO_SLOW_BRAIN" if route == "slow_brain" and decision else "TASK_ROUTE",
        }
    if name == "classify_live_state":
        snapshot = _live_snapshot(args.get("snapshot"))
        provider_name = str(args.get("provider") or "rules").strip().lower()
        if provider_name == "rules":
            result = decide_live_state(snapshot)
        else:
            result = _decision(snapshot.as_dict(), provider_name)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "window_seconds": snapshot.window_seconds,
            "classification": _decision_view(result, "choice"),
        }
    raise FastToolInputError("unknown Fast Brain tool")


def _state_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "snapshot_id": {"type": "string", "maxLength": 120},
            "metrics": {"type": "object", "additionalProperties": True},
        },
    }


TOOL_DEFINITIONS = [
    {
        "name": "fast_choice",
        "description": "对脱敏经营状态给出一个 proposal-only Fast Brain action choice；不执行任何平台动作",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": _state_schema(),
                "provider": {"type": "string", "enum": ["laya", "rules"], "default": "laya"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "fast_binary",
        "description": "判断脱敏经营状态是否需要人工复核；只返回分类建议，不执行任何平台动作",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": _state_schema(),
                "provider": {"type": "string", "enum": ["laya", "rules"], "default": "laya"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "fast_score",
        "description": "对脱敏经营状态给出 0 到 1 的复核优先级；只返回分类建议",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": _state_schema(),
                "provider": {"type": "string", "enum": ["laya", "rules"], "default": "laya"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "route_task",
        "description": "将任务路由到 Fast Brain、Slow Brain 或人工环节；不会启动执行",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task": {"type": "string", "maxLength": 40},
                "state": _state_schema(),
                "provider": {"type": "string", "enum": ["laya", "rules"], "default": "laya"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "classify_live_state",
        "description": "按 LiveStateSnapshot fixture 合同分类直播状态；默认使用本地 rules，不执行浏览器或平台动作",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "snapshot": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "schema_version": {"type": "integer"},
                        "snapshot_id": {"type": "string"},
                        "captured_at": {"type": "string"},
                        "source": {"type": "string"},
                        "window_seconds": {"type": "integer", "enum": list(SUPPORTED_WINDOWS)},
                        "metrics": {"type": "object", "additionalProperties": True},
                        "evidence_hash": {"type": "string"},
                    },
                    "required": [
                        "schema_version",
                        "snapshot_id",
                        "captured_at",
                        "source",
                        "window_seconds",
                        "metrics",
                        "evidence_hash",
                    ],
                },
                "provider": {"type": "string", "enum": ["laya", "rules"], "default": "rules"},
            },
            "required": ["snapshot"],
        },
    },
]


__all__ = ["FAST_TOOL_NAMES", "FastToolInputError", "TOOL_DEFINITIONS", "invoke_fast_tool"]
