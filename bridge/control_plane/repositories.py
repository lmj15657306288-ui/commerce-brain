"""Persistence-facing repositories for Control Plane records."""

from __future__ import annotations

import hashlib
import json
from threading import RLock
from typing import Any, Iterable

from core.contracts import ScopeV1
from core.persistence import PersistenceAdapter, PersistenceConflict

from .errors import conflict


def _record_indexes(value: dict[str, Any]) -> dict[str, str | None]:
    scope = value.get("scope")
    if not isinstance(scope, dict):
        return {}
    return {
        key: scope.get(key)
        for key in (
            "organization_id",
            "brand_id",
            "category_id",
            "shop_id",
            "channel_id",
            "product_id",
            "sku_id",
        )
        if scope.get(key) is not None
    }


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RecordRepository:
    def __init__(self, adapter: PersistenceAdapter, collection: str) -> None:
        self.adapter = adapter
        self.collection = collection

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self.adapter.get_record(self.collection, record_id)

    def list(self) -> list[dict[str, Any]]:
        return self.adapter.list_records(self.collection)

    def save(
        self,
        record_id: str,
        value: dict[str, Any],
        *,
        upsert: bool = True,
        indexes: dict[str, str | None] | None = None,
    ) -> None:
        self.adapter.save_record(
            self.collection,
            record_id,
            value,
            indexes=_record_indexes(value) if indexes is None else indexes,
            upsert=upsert,
        )


class IdempotencyRepository:
    """Durable intent-to-result mapping shared by all mutating services."""

    def __init__(self, adapter: PersistenceAdapter) -> None:
        self._records = RecordRepository(adapter, "cp_idempotency")
        self._lock = RLock()

    def _record_id(self, kind: str, key: str) -> str:
        return f"{kind}:{key}"

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._records.get(self._record_id(kind, key))

    def reserve_or_replay(
        self,
        *,
        kind: str,
        key: str,
        payload: Any,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Return a previous result, or reserve a new intent.

        A key may only ever be associated with one payload fingerprint.
        """

        payload_hash = fingerprint(payload)
        record_id = self._record_id(kind, key)
        with self._lock:
            existing = self._records.get(record_id)
            if existing is not None:
                if existing.get("payload_hash") != payload_hash:
                    raise conflict(
                        "idempotency key was reused with a different payload",
                        code="IDEMPOTENCY_CONFLICT",
                    )
                return existing.get("result")
            if result is None:
                return None
            self._records.save(
                record_id,
                {
                    "kind": kind,
                    "idempotency_key": key,
                    "payload_hash": payload_hash,
                    "result": result,
                },
                upsert=False,
            )
            return result

    def save_result(self, *, kind: str, key: str, payload: Any, result: dict[str, Any]) -> None:
        self.reserve_or_replay(kind=kind, key=key, payload=payload, result=result)


def scope_from_record(value: dict[str, Any]) -> ScopeV1 | None:
    raw = value.get("scope")
    if not isinstance(raw, dict):
        return None
    return ScopeV1.from_mapping(raw)


def paginate(
    values: Iterable[dict[str, Any]],
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int | None]:
    ordered = list(values)
    page = ordered[offset : offset + limit]
    next_offset = offset + len(page)
    return page, (next_offset if next_offset < len(ordered) else None)


def save_conflict_as_control_plane_error(exc: Exception) -> None:
    if isinstance(exc, PersistenceConflict):
        raise conflict(str(exc)) from exc
