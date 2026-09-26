"""Whitelisted Hermes read/write tool declarations.

These are capability contracts, not platform executors. Write tools create
local review artifacts only and can never activate a policy or submit a
platform request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import HermesContractError, POLICY_LIFECYCLE

READ_TOOLS = (
    "get_shop_summary",
    "get_live_session_summary",
    "get_product_card",
    "get_campaign_summary",
    "get_decision_history",
    "get_outcome_history",
    "get_data_quality_report",
    "get_experiment_history",
)
WRITE_TOOLS = (
    "create_review_report",
    "create_action_card",
    "create_policy_draft",
    "create_feature_suggestion",
)
FORBIDDEN_TOOLS = (
    "set_budget",
    "set_roi",
    "pause_campaign",
    "create_campaign",
    "edit_product",
    "change_price",
    "delete_product",
    "submit_order",
)


@dataclass(frozen=True, slots=True)
class LocalDraft:
    kind: str
    payload: dict[str, Any]
    lifecycle: str = "DRAFT"
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": dict(self.payload),
            "lifecycle": self.lifecycle,
            "can_execute": False,
            "execution_allowed": False,
            "platform_write_attempted": False,
        }


class HermesToolRegistry:
    def __init__(self, readers: Mapping[str, Any] | None = None) -> None:
        self.readers = dict(readers or {})

    def read(self, name: str, **kwargs: Any) -> Any:
        if name not in READ_TOOLS:
            raise HermesContractError("tool is not an allowed Hermes read tool")
        reader = self.readers.get(name)
        if reader is None:
            raise HermesContractError("read tool is not connected")
        return reader(**kwargs)

    def create_draft(self, name: str, payload: Mapping[str, Any]) -> LocalDraft:
        if name not in WRITE_TOOLS:
            raise HermesContractError("tool is not an allowed Hermes write tool")
        if not isinstance(payload, Mapping):
            raise HermesContractError("draft payload must be an object")
        if "lifecycle" in payload and payload["lifecycle"] != "DRAFT":
            raise HermesContractError("Hermes may only create DRAFT artifacts")
        return LocalDraft(kind=name, payload=dict(payload))


def tool_catalog() -> dict[str, tuple[str, ...]]:
    return {
        "read": READ_TOOLS,
        "write": WRITE_TOOLS,
        "forbidden": FORBIDDEN_TOOLS,
        "policy_lifecycle": POLICY_LIFECYCLE,
    }


__all__ = [
    "FORBIDDEN_TOOLS",
    "HermesToolRegistry",
    "LocalDraft",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "tool_catalog",
]
