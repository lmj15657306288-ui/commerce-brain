"""Proposal-only Fast Brain providers for the Commerce Decision Center."""

from __future__ import annotations

import hashlib
import json
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

from commerce_contracts import SUPPORTED_ACTIONS
from live_state import LiveStateSnapshot

DECISION_PROVIDER_SCHEMA_VERSION = 1
_ALLOWED_FIELDS = {
    "schema_version",
    "choice",
    "binary",
    "score",
    "confidence",
    "reason_code",
    "provider",
    "model",
    "checkpoint",
    "state_hash",
    "latency_ms",
}
_FORBIDDEN_FIELDS = {
    "shell",
    "python",
    "javascript",
    "css_selector",
    "dom_selector",
    "script",
    "code",
}


class DecisionProviderError(ValueError):
    """Raised when a provider result is unsafe or malformed."""


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _finite(value: Any, *, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def validate_decision_result(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DecisionProviderError("decision result must be an object")
    unknown = set(value) - _ALLOWED_FIELDS
    if unknown:
        raise DecisionProviderError(f"unsupported decision fields: {sorted(unknown)}")
    if any(field in value for field in _FORBIDDEN_FIELDS):
        raise DecisionProviderError("executable fields are forbidden")
    if value.get("schema_version") != DECISION_PROVIDER_SCHEMA_VERSION:
        raise DecisionProviderError("unsupported decision schema")
    if value.get("choice") not in SUPPORTED_ACTIONS:
        raise DecisionProviderError("choice is not supported")
    if value.get("binary") is not None and not isinstance(value.get("binary"), bool):
        raise DecisionProviderError("binary must be boolean or null")
    if value.get("score") is not None and not _finite(value.get("score"), minimum=0, maximum=1):
        raise DecisionProviderError("score must be between 0 and 1 or null")
    if not _finite(value.get("confidence"), minimum=0, maximum=1):
        raise DecisionProviderError("confidence must be between 0 and 1")
    for field in ("reason_code", "provider", "model", "checkpoint", "state_hash"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise DecisionProviderError(f"{field} must be non-empty text")
    if not isinstance(value.get("latency_ms"), int) or value["latency_ms"] < 0:
        raise DecisionProviderError("latency_ms must be a non-negative integer")
    return dict(value)


def _metric(state: Mapping[str, Any], key: str) -> Any:
    metrics = state.get("metrics") if isinstance(state.get("metrics"), Mapping) else state
    return metrics.get(key)


class DecisionProvider(ABC):
    """Small provider contract used by shadow decisions and local simulation."""

    provider_name = "unknown"

    @abstractmethod
    def decide(self, state: Mapping[str, Any], questions: list[str] | None = None) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class RulesProvider(DecisionProvider):
    provider_name: str = "rules"
    model: str = "deterministic-rules-v1"

    def decide(self, state: Mapping[str, Any], questions: list[str] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        roi = _metric(state, "roi")
        orders = _metric(state, "orders")
        inventory_days = _metric(state, "inventory_days")
        if roi is None or orders is None:
            choice, reason, score = "OBSERVE", "MISSING_METRIC", None
            binary = None
            confidence = 0.55
        elif isinstance(inventory_days, (int, float)) and inventory_days < 3:
            choice, reason, score = "CHECK_PRODUCT", "INVENTORY_RISK", 0.9
            binary = True
            confidence = 0.88
        elif isinstance(roi, (int, float)) and roi < 1:
            choice, reason, score = "CHECK_CAMPAIGN", "ROI_BELOW_REFERENCE", 0.85
            binary = True
            confidence = 0.86
        else:
            choice, reason, score = "OBSERVE", "NO_ACTIONABLE_RISK", 0.75
            binary = False
            confidence = 0.82
        result = {
            "schema_version": DECISION_PROVIDER_SCHEMA_VERSION,
            "choice": choice,
            "binary": binary,
            "score": score,
            "confidence": confidence,
            "reason_code": reason,
            "provider": self.provider_name,
            "model": self.model,
            "checkpoint": "rules",
            "state_hash": _canonical_hash(state),
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        }
        return validate_decision_result(result)


@dataclass(frozen=True)
class LayaProvider(DecisionProvider):
    """Local Laya adapter seam with a deterministic fallback.

    A future Laya runtime can replace ``fallback`` behind this contract.  The
    current implementation never makes a network request or claims a loaded
    checkpoint when one is not supplied.
    """

    provider_name: str = "laya"
    model: str = "laya-local-adapter-v1"
    checkpoint: str = "rules-fallback"
    fallback: DecisionProvider = RulesProvider()

    def decide(self, state: Mapping[str, Any], questions: list[str] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        base = self.fallback.decide(state, questions)
        result = {
            **base,
            "provider": self.provider_name,
            "model": self.model,
            "checkpoint": self.checkpoint,
            "state_hash": _canonical_hash(state),
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        }
        return validate_decision_result(result)


def decide_live_state(snapshot: LiveStateSnapshot) -> dict[str, Any]:
    """Map live fixture signals to the MVP read-only action space."""
    metrics = snapshot.metrics
    if metrics.get("online_users") is None or metrics.get("product_click_rate") is None:
        choice, reason, confidence = "OBSERVE", "LIVE_METRIC_MISSING", 0.55
    elif metrics.get("leave_rate") is not None and metrics["leave_rate"] >= 0.35:
        choice, reason, confidence = "PROMPT_HOST", "LEAVE_RATE_HIGH", 0.86
    elif metrics.get("product_click_rate") is not None and metrics["product_click_rate"] < 0.03:
        choice, reason, confidence = "CHECK_PRODUCT", "PRODUCT_CLICK_RATE_LOW", 0.82
    elif metrics.get("roi") is not None and metrics["roi"] < 1:
        choice, reason, confidence = "CHECK_CAMPAIGN", "LIVE_ROI_LOW", 0.84
    else:
        choice, reason, confidence = "OBSERVE", "LIVE_STATE_STABLE", 0.78
    return validate_decision_result({
        "schema_version": DECISION_PROVIDER_SCHEMA_VERSION,
        "choice": choice,
        "binary": choice != "OBSERVE",
        "score": confidence,
        "confidence": confidence,
        "reason_code": reason,
        "provider": "rules",
        "model": "live-rules-v1",
        "checkpoint": "live-fixture",
        "state_hash": snapshot.evidence_hash,
        "latency_ms": 0,
    })


__all__ = [
    "DECISION_PROVIDER_SCHEMA_VERSION",
    "DecisionProvider",
    "DecisionProviderError",
    "LayaProvider",
    "RulesProvider",
    "validate_decision_result",
    "decide_live_state",
]
