"""Local shadow decision loop for the first Commerce Decision Center phase."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from commerce_contracts import (
    DecisionProposalV2,
    StateSnapshotV1,
    validate_proposal_safety,
)
from decision_provider import DecisionProvider, validate_decision_result

SHADOW_SCHEMA_VERSION = 1


def _stable_id(snapshot: StateSnapshotV1, provider: str) -> str:
    raw = f"{snapshot.snapshot_id}|{snapshot.evidence_hash}|{provider}"
    return f"shadow-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


class ShadowDecisionStore:
    """Small append-only local JSON store for proposal-only shadow records."""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "shadow" / "decisions.json"
        self._lock = RLock()

    def _load(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = value.get("decisions") if isinstance(value, dict) else None
        return [row for row in rows[-100:] if isinstance(row, dict)] if isinstance(rows, list) else []

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = self._load()
            rows.append(copy.deepcopy(record))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"schema_version": SHADOW_SCHEMA_VERSION, "decisions": rows[-100:]},
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        return copy.deepcopy(record)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._load()
        rows.reverse()
        return copy.deepcopy(rows)

    def update_human_action(self, shadow_id: str, action: str, reason: str = "") -> dict[str, Any]:
        if action not in {"confirmed", "ignored"}:
            raise ValueError("human action must be confirmed or ignored")
        with self._lock:
            rows = self._load()
            target = next((row for row in rows if row.get("shadow_id") == shadow_id), None)
            if target is None:
                raise ValueError("shadow decision was not found")
            target["human_action"] = {
                "action": action,
                "reason": str(reason or "").strip()[:300],
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            target["status"] = "human_confirmed" if action == "confirmed" else "human_ignored"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"schema_version": SHADOW_SCHEMA_VERSION, "decisions": rows[-100:]},
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return copy.deepcopy(target)

    def add_outcome(
        self,
        shadow_id: str,
        *,
        horizon: str,
        metrics_after: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        if horizon not in {"30s", "2m", "5m", "30m"}:
            raise ValueError("unsupported outcome horizon")
        if source not in {"fixture", "manual"}:
            raise ValueError("outcome source must be fixture or manual")
        with self._lock:
            rows = self._load()
            target = next((row for row in rows if row.get("shadow_id") == shadow_id), None)
            if target is None:
                raise ValueError("shadow decision was not found")
            proposal = target.get("decision_proposal") if isinstance(target.get("decision_proposal"), dict) else {}
            before = target.get("snapshot", {}).get("metrics", {})
            after = dict(metrics_after)
            delta = {}
            for key in sorted(set(before) | set(after)):
                old, new = before.get(key), after.get(key)
                delta[key] = round(new - old, 6) if isinstance(old, (int, float)) and isinstance(new, (int, float)) else None
            target["outcome"] = {
                "decision_id": proposal.get("decision_id") or target.get("shadow_id"),
                "action_id": target.get("shadow_id"),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "horizon": horizon,
                "metrics_before": dict(before),
                "metrics_after": after,
                "delta": delta,
                "source": source,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"schema_version": SHADOW_SCHEMA_VERSION, "decisions": rows[-100:]},
                           ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return copy.deepcopy(target)


def run_shadow_decision(
    snapshot: StateSnapshotV1,
    provider: DecisionProvider,
    *,
    data_dir: str | Path | None = None,
    questions: Iterable[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one local provider decision and persist only its audit envelope."""
    provider_result = validate_decision_result(
        provider.decide(snapshot.as_dict(), list(questions or []))
    )
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    decision_id = _stable_id(snapshot, provider_result["provider"])
    proposal = DecisionProposalV2.create(
        decision_id=decision_id,
        snapshot=snapshot,
        decision_type="fast_brain_shadow",
        action=provider_result["choice"],
        value={
            "binary": provider_result["binary"],
            "score": provider_result["score"],
        },
        confidence=provider_result["confidence"],
        reason_code=provider_result["reason_code"],
        policy_version="commerce-mvp-v1",
        ttl_seconds=300,
        now=now,
    )
    safety_errors = validate_proposal_safety(
        proposal,
        current_snapshot=snapshot,
        now=now,
    )
    record = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "shadow_id": decision_id,
        "created_at": now.isoformat(),
        "snapshot": snapshot.as_dict(),
        "provider": {
            "provider": provider_result["provider"],
            "model": provider_result["model"],
            "checkpoint": provider_result["checkpoint"],
            "latency_ms": provider_result["latency_ms"],
            "state_hash": provider_result["state_hash"],
        },
        "decision_proposal": proposal.as_dict(),
        "safety": {
            "passed": not safety_errors,
            "errors": safety_errors,
        },
        "status": "ready_for_shadow_review" if not safety_errors else "blocked",
        "shadow_mode": True,
        "platform_write_attempted": False,
        "can_execute": False,
        "human_action": None,
        "outcome": None,
    }
    if data_dir is not None:
        ShadowDecisionStore(data_dir).append(record)
    return record


__all__ = ["SHADOW_SCHEMA_VERSION", "ShadowDecisionStore", "run_shadow_decision"]
