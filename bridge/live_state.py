"""Fixture-backed live commerce state for the first MVP phase."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

LIVE_STATE_SCHEMA_VERSION = 1
SUPPORTED_WINDOWS = (30, 120, 300)
LIVE_METRICS = (
    "online_users",
    "enter_rate",
    "leave_rate",
    "product_click_rate",
    "orders",
    "gmv",
    "ad_spend",
    "roi",
)
LIVE_ACTIONS = frozenset(
    {
        "OBSERVE",
        "PROMPT_HOST",
        "CHECK_PRODUCT",
        "CHECK_CAMPAIGN",
        "ESCALATE_SLOW_BRAIN",
    }
)


class LiveStateAdapter(Protocol):
    def read(self) -> Mapping[str, Any]:
        ...


def _finite_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    return None


def _metric_average(samples: list[Mapping[str, Any]], key: str) -> float | None:
    values = [_finite_or_none(sample.get(key)) for sample in samples]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 6)


@dataclass(frozen=True)
class LiveStateSnapshot:
    schema_version: int
    snapshot_id: str
    captured_at: str
    source: str
    window_seconds: int
    metrics: dict[str, float | None]
    evidence_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "source": self.source,
            "window_seconds": self.window_seconds,
            "metrics": dict(self.metrics),
            "evidence_hash": self.evidence_hash,
        }


class FixtureLiveStateAdapter:
    """Read-only synthetic adapter; it never connects to a platform."""

    def __init__(self, samples: list[Mapping[str, Any]]):
        self.samples = [dict(sample) for sample in samples]

    def read(self) -> Mapping[str, Any]:
        return {
            "source": "demo_fixture",
            "synthetic": True,
            "platform_write_attempted": False,
            "samples": [dict(sample) for sample in self.samples],
        }


def build_live_state_snapshot(
    adapter: LiveStateAdapter,
    *,
    window_seconds: int,
    captured_at: str | None = None,
) -> LiveStateSnapshot:
    if window_seconds not in SUPPORTED_WINDOWS:
        raise ValueError("window_seconds must be one of 30, 120, 300")
    payload = adapter.read()
    if not isinstance(payload, Mapping) or payload.get("synthetic") is not True:
        raise ValueError("live adapter must explicitly declare synthetic fixture data")
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("live adapter returned no samples")
    metrics = {key: _metric_average(samples, key) for key in LIVE_METRICS}
    timestamp = captured_at or datetime.now(timezone.utc).isoformat()
    digest_payload = {
        "source": payload.get("source"),
        "window_seconds": window_seconds,
        "metrics": metrics,
        "captured_at": timestamp,
    }
    evidence_hash = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return LiveStateSnapshot(
        schema_version=LIVE_STATE_SCHEMA_VERSION,
        snapshot_id=f"live-{evidence_hash[:24]}",
        captured_at=timestamp,
        source=str(payload.get("source") or "fixture"),
        window_seconds=window_seconds,
        metrics=metrics,
        evidence_hash=evidence_hash,
    )


__all__ = [
    "FixtureLiveStateAdapter",
    "LIVE_ACTIONS",
    "LIVE_METRICS",
    "LiveStateSnapshot",
    "SUPPORTED_WINDOWS",
    "build_live_state_snapshot",
]
