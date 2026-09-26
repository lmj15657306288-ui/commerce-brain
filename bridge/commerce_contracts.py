"""Versioned contracts for the local commerce decision loop.

The first MVP phase is proposal-only.  This module intentionally has no
platform client, browser executor, or credential handling.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

STATE_SNAPSHOT_SCHEMA_VERSION = 1
DECISION_PROPOSAL_SCHEMA_VERSION = 2
ACTION_REQUEST_SCHEMA_VERSION = 1
OUTCOME_RECORD_SCHEMA_VERSION = 1

SUPPORTED_ACTIONS = frozenset(
    {
        "OBSERVE",
        "PROMPT_HOST",
        "CHECK_PRODUCT",
        "CHECK_CAMPAIGN",
        "ESCALATE_SLOW_BRAIN",
    }
)
_HEX64 = set("0123456789abcdef")
_MAX_PROPOSAL_TTL_SECONDS = 10 * 60


class ContractValidationError(ValueError):
    """Raised when a contract cannot be safely constructed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError("NON_FINITE_VALUE", "Metrics must be finite or null.")
        return value
    return value


def _hash(value: Any) -> str:
    raw = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError("INVALID_FIELD", f"{field} must be a non-empty string.")
    return value.strip()


def _require_scope(value: Any) -> dict[str, str | None]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("INVALID_SCOPE", "scope must be an object.")
    shop_id = value.get("shop_id")
    account_id = value.get("account_id")
    if shop_id is not None and (not isinstance(shop_id, str) or not shop_id.strip()):
        raise ContractValidationError("INVALID_SCOPE", "scope.shop_id must be text or null.")
    if account_id is not None and (not isinstance(account_id, str) or not account_id.strip()):
        raise ContractValidationError("INVALID_SCOPE", "scope.account_id must be text or null.")
    if shop_id is None and account_id is None:
        raise ContractValidationError("INVALID_SCOPE", "scope must identify a shop or account.")
    return {
        "shop_id": shop_id.strip() if isinstance(shop_id, str) else None,
        "account_id": account_id.strip() if isinstance(account_id, str) else None,
    }


@dataclass(frozen=True, slots=True)
class StateSnapshotV1:
    schema_version: int
    snapshot_id: str
    captured_at: str
    source: str
    source_quality: str
    scope: dict[str, str | None]
    entity_ids: dict[str, tuple[str, ...]]
    metrics: dict[str, Any]
    evidence_hash: str

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        source: str,
        source_quality: str,
        scope: Mapping[str, Any],
        entity_ids: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
        captured_at: str | None = None,
    ) -> "StateSnapshotV1":
        normalized_scope = _require_scope(scope)
        normalized_entities: dict[str, tuple[str, ...]] = {}
        for kind, values in (entity_ids or {}).items():
            if not isinstance(values, (list, tuple)):
                raise ContractValidationError("INVALID_ENTITY_IDS", "entity_ids values must be arrays.")
            normalized_entities[str(kind)] = tuple(_require_text(item, "entity_id") for item in values)
        normalized_metrics = dict(metrics or {})
        _canonical(normalized_metrics)
        timestamp = captured_at or datetime.now(timezone.utc).isoformat()
        _require_text(snapshot_id, "snapshot_id")
        _require_text(source, "source")
        _require_text(source_quality, "source_quality")
        evidence = {
            "snapshot_id": snapshot_id,
            "captured_at": timestamp,
            "source": source,
            "source_quality": source_quality,
            "scope": normalized_scope,
            "entity_ids": normalized_entities,
            "metrics": normalized_metrics,
        }
        return cls(
            schema_version=STATE_SNAPSHOT_SCHEMA_VERSION,
            snapshot_id=snapshot_id,
            captured_at=timestamp,
            source=source,
            source_quality=source_quality,
            scope=normalized_scope,
            entity_ids=normalized_entities,
            metrics=normalized_metrics,
            evidence_hash=_hash(evidence),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StateSnapshotV1":
        if not isinstance(value, Mapping):
            raise ContractValidationError("INVALID_SNAPSHOT", "snapshot must be an object.")
        if value.get("schema_version") != STATE_SNAPSHOT_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "snapshot schema is unsupported.")
        snapshot = cls.create(
            snapshot_id=value.get("snapshot_id"),
            captured_at=value.get("captured_at"),
            source=value.get("source"),
            source_quality=value.get("source_quality"),
            scope=value.get("scope"),
            entity_ids=value.get("entity_ids"),
            metrics=value.get("metrics"),
        )
        if value.get("evidence_hash") != snapshot.evidence_hash:
            raise ContractValidationError("EVIDENCE_HASH_MISMATCH", "snapshot evidence hash is invalid.")
        return snapshot

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["entity_ids"] = {key: list(items) for key, items in self.entity_ids.items()}
        return value


@dataclass(frozen=True, slots=True)
class DecisionProposalV2:
    schema_version: int
    decision_id: str
    snapshot_id: str
    scope: dict[str, str | None]
    decision_type: str
    action: str
    value: Any
    confidence: float
    reason_code: str
    policy_version: str
    evidence_hash: str
    expires_at: str
    can_execute: bool = False

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        snapshot: StateSnapshotV1,
        decision_type: str,
        action: str,
        value: Any,
        confidence: float,
        reason_code: str,
        policy_version: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> "DecisionProposalV2":
        if action not in SUPPORTED_ACTIONS:
            raise ContractValidationError("ILLEGAL_ACTION", "action is not allowed in MVP phase.")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ContractValidationError("INVALID_CONFIDENCE", "confidence must be between 0 and 1.")
        if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= _MAX_PROPOSAL_TTL_SECONDS:
            raise ContractValidationError("INVALID_TTL", "ttl_seconds is outside the allowed range.")
        _canonical(value)
        base_time = now or datetime.now(timezone.utc)
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)
        expires = (base_time + timedelta(seconds=ttl_seconds)).isoformat()
        return cls(
            schema_version=DECISION_PROPOSAL_SCHEMA_VERSION,
            decision_id=_require_text(decision_id, "decision_id"),
            snapshot_id=_require_text(snapshot.snapshot_id, "snapshot_id"),
            scope=dict(snapshot.scope),
            decision_type=_require_text(decision_type, "decision_type"),
            action=action,
            value=value,
            confidence=float(confidence),
            reason_code=_require_text(reason_code, "reason_code"),
            policy_version=_require_text(policy_version, "policy_version"),
            evidence_hash=snapshot.evidence_hash,
            expires_at=expires,
            can_execute=False,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActionRequestV1:
    schema_version: int
    action_id: str
    decision_id: str
    action: str
    scope: dict[str, str | None]
    requested_at: str
    can_execute: bool = False

    @classmethod
    def from_proposal(cls, proposal: DecisionProposalV2, *, action_id: str) -> "ActionRequestV1":
        return cls(
            schema_version=ACTION_REQUEST_SCHEMA_VERSION,
            action_id=_require_text(action_id, "action_id"),
            decision_id=proposal.decision_id,
            action=proposal.action,
            scope=dict(proposal.scope),
            requested_at=datetime.now(timezone.utc).isoformat(),
            can_execute=False,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeRecordV1:
    schema_version: int
    decision_id: str
    action_id: str
    captured_at: str
    horizon: str
    metrics_before: dict[str, Any]
    metrics_after: dict[str, Any]
    delta: dict[str, Any]
    source: str

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        action_id: str,
        horizon: str,
        metrics_before: Mapping[str, Any],
        metrics_after: Mapping[str, Any],
        source: str,
        captured_at: str | None = None,
    ) -> "OutcomeRecordV1":
        before = dict(metrics_before)
        after = dict(metrics_after)
        _canonical(before)
        _canonical(after)
        delta: dict[str, Any] = {}
        for key in sorted(set(before) | set(after)):
            old = before.get(key)
            new = after.get(key)
            delta[key] = new - old if isinstance(old, (int, float)) and isinstance(new, (int, float)) else None
        return cls(
            schema_version=OUTCOME_RECORD_SCHEMA_VERSION,
            decision_id=_require_text(decision_id, "decision_id"),
            action_id=_require_text(action_id, "action_id"),
            captured_at=captured_at or datetime.now(timezone.utc).isoformat(),
            horizon=_require_text(horizon, "horizon"),
            metrics_before=before,
            metrics_after=after,
            delta=delta,
            source=_require_text(source, "source"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _same_scope(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return dict(left) == dict(right)


def validate_proposal_safety(
    proposal: DecisionProposalV2 | Mapping[str, Any],
    *,
    current_snapshot: StateSnapshotV1,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Return explicit safety blocks; an empty list means reviewable only."""
    candidate = proposal if isinstance(proposal, DecisionProposalV2) else _proposal_from_mapping(proposal)
    errors: list[dict[str, str]] = []
    if candidate.action not in SUPPORTED_ACTIONS:
        errors.append({"code": "ILLEGAL_ACTION", "message": "action is not allowed in MVP phase."})
    if candidate.snapshot_id != current_snapshot.snapshot_id:
        errors.append({"code": "SNAPSHOT_CHANGED", "message": "decision snapshot is no longer current."})
    if candidate.evidence_hash != current_snapshot.evidence_hash:
        errors.append({"code": "EVIDENCE_HASH_MISMATCH", "message": "decision evidence does not match snapshot."})
    if not _same_scope(candidate.scope, current_snapshot.scope):
        errors.append({"code": "SCOPE_MISMATCH", "message": "decision scope does not match snapshot scope."})
    expires_at = datetime.fromisoformat(candidate.expires_at)
    current_time = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= current_time:
        errors.append({"code": "PROPOSAL_EXPIRED", "message": "decision proposal has expired."})
    if candidate.can_execute:
        errors.append({"code": "EXECUTION_DISABLED", "message": "MVP proposals are never executable."})
    return errors


def _proposal_from_mapping(value: Mapping[str, Any]) -> DecisionProposalV2:
    if not isinstance(value, Mapping):
        raise ContractValidationError("INVALID_PROPOSAL", "proposal must be an object.")
    try:
        if value.get("schema_version") != DECISION_PROPOSAL_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "proposal schema is unsupported.")
        return DecisionProposalV2(
            schema_version=DECISION_PROPOSAL_SCHEMA_VERSION,
            decision_id=_require_text(value.get("decision_id"), "decision_id"),
            snapshot_id=_require_text(value.get("snapshot_id"), "snapshot_id"),
            scope=_require_scope(value.get("scope")),
            decision_type=_require_text(value.get("decision_type"), "decision_type"),
            action=value.get("action"),
            value=value.get("value"),
            confidence=float(value.get("confidence")),
            reason_code=_require_text(value.get("reason_code"), "reason_code"),
            policy_version=_require_text(value.get("policy_version"), "policy_version"),
            evidence_hash=value.get("evidence_hash"),
            expires_at=_require_text(value.get("expires_at"), "expires_at"),
            can_execute=value.get("can_execute", False),
        )
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("INVALID_PROPOSAL", "proposal fields are invalid.") from exc


__all__ = [
    "ACTION_REQUEST_SCHEMA_VERSION",
    "ContractValidationError",
    "DecisionProposalV2",
    "DECISION_PROPOSAL_SCHEMA_VERSION",
    "OUTCOME_RECORD_SCHEMA_VERSION",
    "OutcomeRecordV1",
    "STATE_SNAPSHOT_SCHEMA_VERSION",
    "SUPPORTED_ACTIONS",
    "StateSnapshotV1",
    "ActionRequestV1",
    "validate_proposal_safety",
]
