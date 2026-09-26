"""Proposal-only Fast Brain providers for the Commerce Decision Center."""

from __future__ import annotations

import hashlib
import json
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from commerce_contracts import SUPPORTED_ACTIONS
from laya_client import LayaClient, LayaClientError
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
_LAYA_ACTION_QUESTION = "next_action"
_LAYA_BINARY_QUESTION = "actionable"
_LAYA_SCORE_QUESTION = "priority"
_LAYA_SCORE_LEVELS = (
    "no immediate operational concern",
    "low priority review",
    "operator review recommended",
    "high priority operator review",
)


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


def _normalized_laya_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep bounded scalar features; never send raw page material."""
    metrics = state.get("metrics") if isinstance(state.get("metrics"), Mapping) else state
    if not isinstance(metrics, Mapping):
        raise DecisionProviderError("state metrics must be an object")
    normalized: dict[str, Any] = {}
    for key, value in metrics.items():
        name = str(key).strip()
        if not name or len(name) > 80:
            continue
        if value is None or isinstance(value, bool):
            normalized[name] = value
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            normalized[name] = value
        elif isinstance(value, str) and len(value) <= 160:
            normalized[name] = value
    if not normalized:
        raise DecisionProviderError("state has no normalized features")
    return normalized


def _laya_questions() -> dict[str, dict[str, Any]]:
    return {
        _LAYA_ACTION_QUESTION: {
            "type": "choice",
            "instructions": "Choose the safest next operational action. Do not execute anything.",
            "criteria": {
                "OBSERVE": "Continue monitoring with no operator action.",
                "PROMPT_HOST": "Host messaging or live presentation needs operator review.",
                "CHECK_PRODUCT": "Product or card performance needs operator review.",
                "CHECK_CAMPAIGN": "Paid traffic performance needs operator review.",
                "ESCALATE_SLOW_BRAIN": "The situation is ambiguous or complex; request slow analysis.",
            },
        },
        _LAYA_BINARY_QUESTION: {
            "type": "noul",
            "instructions": "Is an operator review needed now? This is a proposal-only classification.",
        },
        _LAYA_SCORE_QUESTION: {
            "type": "score",
            "instructions": "How urgent is the safest operator review?",
            "criteria": list(_LAYA_SCORE_LEVELS),
        },
    }


def _answer_confidence(answer: Mapping[str, Any]) -> float:
    for key in ("answer_confidence", "confidence"):
        value = answer.get(key)
        if _finite(value, minimum=0, maximum=1):
            return float(value)
    raise DecisionProviderError("Laya answer confidence is invalid")


def _map_laya_result(
    response: Mapping[str, Any],
    *,
    state_hash: str,
    latency_ms: int,
    model: str,
    min_confidence: float,
) -> dict[str, Any]:
    answers = response.get("answers")
    if not isinstance(answers, Mapping):
        raise DecisionProviderError("Laya response is missing answers")
    action_answer = answers.get(_LAYA_ACTION_QUESTION)
    if not isinstance(action_answer, Mapping):
        raise DecisionProviderError("Laya response is missing next_action")
    choice = action_answer.get("choice")
    if choice not in SUPPORTED_ACTIONS:
        raise DecisionProviderError("Laya returned an unsupported action")
    confidence = _answer_confidence(action_answer)
    if confidence < min_confidence:
        raise DecisionProviderError("Laya confidence is below the local threshold")

    binary: bool | None = None
    binary_answer = answers.get(_LAYA_BINARY_QUESTION)
    if isinstance(binary_answer, Mapping) and _finite(binary_answer.get("noul"), minimum=0, maximum=1):
        binary = float(binary_answer["noul"]) >= 0.5
    if binary is None:
        binary = choice != "OBSERVE"

    score: float | None = None
    score_answer = answers.get(_LAYA_SCORE_QUESTION)
    if isinstance(score_answer, Mapping) and _finite(
        score_answer.get("score"),
        minimum=0,
        maximum=len(_LAYA_SCORE_LEVELS) - 1,
    ):
        score = round(float(score_answer["score"]) / (len(_LAYA_SCORE_LEVELS) - 1), 4)

    routing = response.get("routing")
    checkpoint = routing.get("model") if isinstance(routing, Mapping) else None
    return validate_decision_result(
        {
            "schema_version": DECISION_PROVIDER_SCHEMA_VERSION,
            "choice": choice,
            "binary": binary,
            "score": score,
            "confidence": confidence,
            "reason_code": f"LAYA_{choice}",
            "provider": "laya",
            "model": str(response.get("model") or model),
            "checkpoint": str(checkpoint or model),
            "state_hash": state_hash,
            "latency_ms": max(0, int(latency_ms)),
        }
    )


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
    """Call local Laya, then fail closed to deterministic Rules."""

    client: LayaClient = field(default_factory=LayaClient)
    fallback: DecisionProvider = field(default_factory=RulesProvider)
    timeout_ms: int = 3000
    min_confidence: float = 0.25
    provider_name: str = "laya"
    model: str = "typed-decisions"

    def decide(self, state: Mapping[str, Any], questions: list[str] | None = None) -> dict[str, Any]:
        if not isinstance(self.timeout_ms, int) or self.timeout_ms < 1:
            raise DecisionProviderError("timeout_ms must be a positive integer")
        if not _finite(self.min_confidence, minimum=0, maximum=1):
            raise DecisionProviderError("min_confidence must be between 0 and 1")
        try:
            previous_timeout = self.client.timeout_seconds
            self.client.timeout_seconds = min(self.timeout_ms / 1000.0, 60.0)
            normalized_state = _normalized_laya_state(state)
            response = self.client.systemone(
                state=normalized_state,
                questions=_laya_questions(),
                model=self.model,
            )
            return _map_laya_result(
                response,
                state_hash=_canonical_hash(state),
                latency_ms=self.client.last_latency_ms,
                model=self.model,
                min_confidence=float(self.min_confidence),
            )
        except (DecisionProviderError, LayaClientError, KeyError, TypeError, ValueError):
            base = self.fallback.decide(state, questions)
            return validate_decision_result(
                {
                    **base,
                    "provider": "rules",
                    "model": getattr(self.fallback, "model", "deterministic-rules-v1"),
                    "checkpoint": "fallback_from_laya",
                    "state_hash": _canonical_hash(state),
                }
            )
        finally:
            if "previous_timeout" in locals():
                self.client.timeout_seconds = previous_timeout


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
