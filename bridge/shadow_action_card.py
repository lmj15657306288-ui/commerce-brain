"""Presentation contract for proposal-only shadow Action Cards."""

from __future__ import annotations

from typing import Any, Mapping


def build_shadow_action_card(record: Mapping[str, Any]) -> dict[str, Any]:
    proposal = record.get("decision_proposal") if isinstance(record.get("decision_proposal"), Mapping) else {}
    snapshot = record.get("snapshot") if isinstance(record.get("snapshot"), Mapping) else {}
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), Mapping) else {}
    action = str(proposal.get("action") or "OBSERVE")
    labels = {
        "OBSERVE": "继续观察",
        "PROMPT_HOST": "提醒主播",
        "CHECK_PRODUCT": "检查商品",
        "CHECK_CAMPAIGN": "检查投放",
    }
    risk = "high" if action in {"CHECK_PRODUCT", "CHECK_CAMPAIGN"} else "low"
    return {
        "shadow_id": str(record.get("shadow_id") or ""),
        "problem": str(proposal.get("reason_code") or "暂无明确问题"),
        "metrics": {
            key: metrics.get(key)
            for key in ("online_users", "roi", "orders", "gmv", "ad_spend")
            if key in metrics
        },
        "recommendation": labels.get(action, "人工复核"),
        "risk_level": risk,
        "observe_minutes": 2 if action == "OBSERVE" else 5,
        "evidence_hash": str(proposal.get("evidence_hash") or ""),
        "can_execute": False,
        "platform_write_attempted": False,
        "human_action": record.get("human_action"),
    }


__all__ = ["build_shadow_action_card"]
