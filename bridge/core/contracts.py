"""Versioned, fail-closed contracts for the Commerce OS multi-store foundation.

This module contains data contracts only. It does not connect to a database,
network service, platform API, browser runtime, scheduler, or credential store.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA_VERSION = 1
SCOPE_FIELDS = (
    "organization_id",
    "brand_id",
    "category_id",
    "shop_id",
    "channel_id",
    "product_id",
    "sku_id",
)
SCOPE_SCHEMA_VERSION = 1
EXTERNAL_REF_SCHEMA_VERSION = 1
ACTOR_SCHEMA_VERSION = 1
DEVICE_SESSION_SCHEMA_VERSION = 1
WORKER_SCHEMA_VERSION = 1
LEASE_SCHEMA_VERSION = 1
TASK_SCHEMA_VERSION = 1
ALERT_SCHEMA_VERSION = 1
APPROVAL_SCHEMA_VERSION = 1
BUSINESS_IMPACT_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1

ACTOR_TYPES = frozenset({"HUMAN", "LAYA", "HERMES", "CODEX", "SYSTEM", "EDGE", "WORKER"})
DEVICE_TYPES = frozenset({"DESKTOP", "BROWSER_EXTENSION", "LIVE_EDGE", "MOBILE", "BRAIN_WORKER"})
DEVICE_STATUSES = frozenset({"ACTIVE", "DISCONNECTED", "REVOKED", "EXPIRED"})
HEALTH_STATUSES = frozenset({"ONLINE", "DEGRADED", "OFFLINE", "UNKNOWN"})
TASK_STATUSES = frozenset(
    {"PENDING", "ASSIGNED", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED", "EXPIRED"}
)
ALERT_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
ALERT_STATUSES = frozenset({"OPEN", "ACKNOWLEDGED", "RESOLVED", "EXPIRED", "CANCELLED"})
APPROVAL_STATUSES = frozenset(
    {"PENDING", "APPROVED", "REJECTED", "REVISION_REQUESTED", "EXPIRED", "CANCELLED"}
)
RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_ID_PREFIXES = {
    "organization_id": ("org", "organization"),
    "brand_id": ("brand",),
    "category_id": ("cat", "category"),
    "shop_id": ("shop",),
    "channel_id": ("channel",),
    "product_id": ("product", "master_product"),
    "sku_id": ("sku",),
}
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
        "raw_token",
        "screenshot",
        "script",
        "secret",
        "session_token",
        "token",
    }
)
_FORBIDDEN_ACTION_KEYS = frozenset(
    {
        "browser_action",
        "browser_actions",
        "command",
        "execute",
        "execution",
        "platform_write",
        "platform_write_attempted",
        "shell",
        "write",
    }
)
_MAX_PAYLOAD_BYTES = 64 * 1024


class ContractValidationError(ValueError):
    """Raised when a multi-store contract cannot be safely constructed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _unknown_fields(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ContractValidationError(
            "UNKNOWN_FIELDS",
            f"{name} contains unsupported fields: {sorted(unknown)}.",
        )


def _text(value: Any, field: str, *, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ContractValidationError("INVALID_FIELD", f"{field} must be bounded non-empty text.")
    return value.strip()


def _safe_ref(value: Any, field: str, *, limit: int = 128) -> str:
    result = _text(value, field, limit=limit)
    if not _REF_RE.fullmatch(result):
        raise ContractValidationError("INVALID_REF", f"{field} is not a safe reference.")
    return result


def _internal_id(value: Any, field: str) -> str:
    result = _safe_ref(value, field)
    if not _ID_RE.fullmatch(result):
        raise ContractValidationError("INVALID_ID", f"{field} is not a valid internal ID.")
    prefixes = _ID_PREFIXES[field]
    if not any(result.lower().startswith(f"{prefix}_") or result.lower().startswith(f"{prefix}-") for prefix in prefixes):
        raise ContractValidationError("INVALID_ID", f"{field} must use the Commerce Brain ID namespace.")
    return result


def _optional_internal_id(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _internal_id(value, field)


def _timestamp(value: Any, field: str) -> str:
    result = _text(value, field, limit=80)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError("INVALID_TIMESTAMP", f"{field} must be ISO-8601.") from exc
    if parsed.tzinfo is None:
        raise ContractValidationError("INVALID_TIMESTAMP", f"{field} must include a timezone.")
    return result


def _canonical(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ContractValidationError("PAYLOAD_TOO_DEEP", "value nesting is too deep.")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError("NON_FINITE_VALUE", "numbers must be finite.")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if not _KEY_RE.fullmatch(key):
                raise ContractValidationError("INVALID_KEY", "object keys must be safe references.")
            output[key] = _canonical(child, depth=depth + 1)
        return {key: output[key] for key in sorted(output)}
    if isinstance(value, (list, tuple)):
        if len(value) > 500:
            raise ContractValidationError("LIST_TOO_LARGE", "list exceeds the contract bound.")
        return [_canonical(item, depth=depth + 1) for item in value]
    raise ContractValidationError("UNSUPPORTED_VALUE", "value type is not supported.")


def _safe_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("INVALID_PAYLOAD", "payload must be an object.")
    result: dict[str, Any] = {}
    for raw_key, child in value.items():
        key = str(raw_key).strip()
        lowered = key.lower().replace("-", "_").replace(" ", "_")
        if (
            not _KEY_RE.fullmatch(key)
            or lowered in _FORBIDDEN_KEYS
            or (
                lowered in _FORBIDDEN_ACTION_KEYS
                and not (lowered == "platform_write_attempted" and child is False)
            )
            or lowered.endswith("_token")
            or lowered.endswith("_secret")
        ):
            raise ContractValidationError("SENSITIVE_FIELD", "payload contains a forbidden field.")
        if lowered in {"can_execute", "execution_allowed"} and child is not False:
            raise ContractValidationError("EXECUTION_DISABLED", "execution flags must remain false.")
        if lowered == "platform_write_attempted" and child is not False:
            raise ContractValidationError("PLATFORM_WRITE_DISABLED", "platform writes are disabled.")
        result[key] = _canonical(child)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ContractValidationError("PAYLOAD_TOO_LARGE", "payload exceeds the contract bound.")
    return result


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for a contract value."""

    return json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stable_hash(value: Any) -> str:
    """Return a deterministic SHA-256 over canonical contract data."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_enum(value: Any, field: str, allowed: set[str] | frozenset[str]) -> str:
    result = _text(value, field, limit=64).upper()
    if result not in allowed:
        raise ContractValidationError("INVALID_ENUM", f"{field} is unsupported.")
    return result


@dataclass(frozen=True, slots=True)
class ScopeV1:
    schema_version: int
    organization_id: str | None = None
    brand_id: str | None = None
    category_id: str | None = None
    shop_id: str | None = None
    channel_id: str | None = None
    product_id: str | None = None
    sku_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SCOPE_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "scope schema is unsupported.")
        for field in SCOPE_FIELDS:
            value = getattr(self, field)
            if value is not None:
                _internal_id(value, field)
        if self.organization_id is None and any(getattr(self, field) is not None for field in SCOPE_FIELDS[1:]):
            raise ContractValidationError("INVALID_SCOPE_LINEAGE", "non-global scope requires organization_id.")
        if self.brand_id is not None and self.organization_id is None:
            raise ContractValidationError("INVALID_SCOPE_LINEAGE", "brand scope requires organization_id.")
        if self.category_id is not None and self.organization_id is None:
            raise ContractValidationError("INVALID_SCOPE_LINEAGE", "category scope requires organization_id.")
        if self.channel_id is not None and self.shop_id is None:
            raise ContractValidationError("INVALID_SCOPE_LINEAGE", "channel scope requires shop_id.")
        if self.product_id is not None and self.category_id is None:
            raise ContractValidationError("INVALID_SCOPE_LINEAGE", "product scope requires category_id.")
        if self.sku_id is not None and (self.product_id is None or self.shop_id is None):
            raise ContractValidationError("INVALID_SCOPE_LINEAGE", "sku scope requires product_id and shop_id.")

    @classmethod
    def create(cls, **values: Any) -> "ScopeV1":
        _unknown_fields(values, set(SCOPE_FIELDS), "ScopeV1")
        return cls(schema_version=SCOPE_SCHEMA_VERSION, **values)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScopeV1":
        if not isinstance(value, Mapping):
            raise ContractValidationError("INVALID_SCOPE", "scope must be an object.")
        _unknown_fields(value, {"schema_version", *SCOPE_FIELDS}, "ScopeV1")
        if value.get("schema_version", SCOPE_SCHEMA_VERSION) != SCOPE_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "scope schema is unsupported.")
        return cls.create(**{field: value.get(field) for field in SCOPE_FIELDS})

    @property
    def kind(self) -> str:
        if self.sku_id is not None:
            return "sku"
        if self.product_id is not None:
            return "product"
        if self.channel_id is not None:
            return "channel"
        if self.shop_id is not None:
            return "shop"
        if self.category_id is not None:
            return "category"
        if self.brand_id is not None:
            return "brand"
        if self.organization_id is not None:
            return "organization"
        return "global"

    @property
    def is_global(self) -> bool:
        return self.kind == "global"

    def contains(self, child: "ScopeV1") -> bool:
        """Return whether this scope may authorize access to the child scope."""

        if not isinstance(child, ScopeV1):
            return False
        for field in SCOPE_FIELDS:
            parent_value = getattr(self, field)
            child_value = getattr(child, field)
            if parent_value is not None and parent_value != child_value:
                return False
        return True

    def matches(self, other: "ScopeV1") -> bool:
        """Return exact scope equality, including explicit null fields."""

        return isinstance(other, ScopeV1) and self.as_dict() == other.as_dict()

    def stable_hash(self) -> str:
        return stable_hash(self.as_dict())

    def canonical_json(self) -> str:
        return canonical_json(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            **{field: getattr(self, field) for field in SCOPE_FIELDS},
        }


def scope_allows(actor_scope: ScopeV1, requested_scope: ScopeV1) -> bool:
    """Return whether an actor or context scope contains a requested scope."""

    return actor_scope.contains(requested_scope)


@dataclass(frozen=True, slots=True)
class ExternalRefV1:
    schema_version: int
    source: str
    entity_type: str
    external_id: str
    shop_id: str | None = None
    channel_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        source: str,
        entity_type: str,
        external_id: Any,
        shop_id: str | None = None,
        channel_id: str | None = None,
    ) -> "ExternalRefV1":
        normalized_shop = _optional_internal_id(shop_id, "shop_id")
        normalized_channel = _optional_internal_id(channel_id, "channel_id")
        if normalized_channel is not None and normalized_shop is None:
            raise ContractValidationError("INVALID_EXTERNAL_SCOPE", "channel_id requires shop_id.")
        external_text = str(external_id).strip() if isinstance(external_id, (str, int)) else ""
        if not external_text or len(external_text) > 256 or any(char.isspace() for char in external_text):
            raise ContractValidationError("INVALID_EXTERNAL_ID", "external_id must be bounded non-empty text.")
        return cls(
            EXTERNAL_REF_SCHEMA_VERSION,
            _safe_ref(source, "source", limit=64),
            _safe_ref(entity_type, "entity_type", limit=64),
            external_text,
            normalized_shop,
            normalized_channel,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExternalRefV1":
        _unknown_fields(value, {"schema_version", "source", "entity_type", "external_id", "shop_id", "channel_id"}, "ExternalRefV1")
        if value.get("schema_version") != EXTERNAL_REF_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "external ref schema is unsupported.")
        return cls.create(
            source=value.get("source"),
            entity_type=value.get("entity_type"),
            external_id=value.get("external_id"),
            shop_id=value.get("shop_id"),
            channel_id=value.get("channel_id"),
        )

    def identity_key(self) -> tuple[str, str, str]:
        return self.source, self.entity_type, self.external_id

    def conflicts_with(self, other: "ExternalRefV1") -> bool:
        return self.identity_key() == other.identity_key() and (
            self.shop_id != other.shop_id or self.channel_id != other.channel_id
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "entity_type": self.entity_type,
            "external_id": self.external_id,
            "shop_id": self.shop_id,
            "channel_id": self.channel_id,
        }


@dataclass(frozen=True, slots=True)
class ActorV1:
    schema_version: int
    actor_id: str
    actor_type: str
    scope: ScopeV1
    role: str

    @classmethod
    def create(
        cls,
        *,
        actor_id: str,
        actor_type: str,
        role: str,
        scope: ScopeV1 | Mapping[str, Any] | None = None,
    ) -> "ActorV1":
        normalized_scope = (
            scope
            if isinstance(scope, ScopeV1)
            else ScopeV1.create(**(dict(scope or {})))
        )
        return cls(
            ACTOR_SCHEMA_VERSION,
            _safe_ref(actor_id, "actor_id"),
            _normalize_enum(actor_type, "actor_type", ACTOR_TYPES),
            normalized_scope,
            _safe_ref(role, "role", limit=64),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActorV1":
        _unknown_fields(value, {"schema_version", "actor_id", "actor_type", "scope", "role"}, "ActorV1")
        if value.get("schema_version") != ACTOR_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "actor schema is unsupported.")
        return cls.create(
            actor_id=value.get("actor_id"),
            actor_type=value.get("actor_type"),
            scope=ScopeV1.from_mapping(value.get("scope")),
            role=value.get("role"),
        )

    def can_access(self, requested_scope: ScopeV1) -> bool:
        return scope_allows(self.scope, requested_scope)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "scope": self.scope.as_dict(),
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class DeviceSessionV1:
    schema_version: int
    device_id: str
    device_type: str
    user_id: str | None
    organization_id: str
    shop_ids: tuple[str, ...]
    capabilities: tuple[str, ...]
    status: str
    connected_at: str
    last_seen_at: str

    @classmethod
    def create(
        cls,
        *,
        device_id: str,
        device_type: str,
        organization_id: str,
        user_id: str | None = None,
        shop_ids: list[str] | tuple[str, ...] = (),
        capabilities: list[str] | tuple[str, ...] = (),
        status: str = "ACTIVE",
        connected_at: str | None = None,
        last_seen_at: str | None = None,
    ) -> "DeviceSessionV1":
        normalized_shops = tuple(sorted({_internal_id(item, "shop_id") for item in shop_ids}))
        normalized_capabilities = tuple(sorted({_safe_ref(item, "capability") for item in capabilities}))
        connected = _timestamp(connected_at or _now(), "connected_at")
        return cls(
            DEVICE_SESSION_SCHEMA_VERSION,
            _safe_ref(device_id, "device_id"),
            _normalize_enum(device_type, "device_type", DEVICE_TYPES),
            None if user_id is None else _safe_ref(user_id, "user_id"),
            _internal_id(organization_id, "organization_id"),
            normalized_shops,
            normalized_capabilities,
            _normalize_enum(status, "status", DEVICE_STATUSES),
            connected,
            _timestamp(last_seen_at or connected, "last_seen_at"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeviceSessionV1":
        _unknown_fields(value, {"schema_version", "device_id", "device_type", "user_id", "organization_id", "shop_ids", "capabilities", "status", "connected_at", "last_seen_at"}, "DeviceSessionV1")
        if value.get("schema_version") != DEVICE_SESSION_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "device session schema is unsupported.")
        if not isinstance(value.get("shop_ids"), list) or not isinstance(value.get("capabilities"), list):
            raise ContractValidationError("INVALID_DEVICE_SESSION", "shop_ids and capabilities must be arrays.")
        return cls.create(**{key: value.get(key) for key in (
            "device_id", "device_type", "user_id", "organization_id", "shop_ids",
            "capabilities", "status", "connected_at", "last_seen_at",
        )})

    def scope_for_shop(self, shop_id: str) -> ScopeV1:
        normalized = _internal_id(shop_id, "shop_id")
        if self.shop_ids and normalized not in self.shop_ids:
            raise ContractValidationError("SCOPE_DENIED", "device session is not authorized for this shop.")
        return ScopeV1.create(organization_id=self.organization_id, shop_id=normalized)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "device_id": self.device_id,
            "device_type": self.device_type,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "shop_ids": list(self.shop_ids),
            "capabilities": list(self.capabilities),
            "status": self.status,
            "connected_at": self.connected_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True, slots=True)
class BrainWorkerV1:
    schema_version: int
    worker_id: str
    worker_type: str
    capabilities: tuple[str, ...]
    status: str
    heartbeat_at: str
    last_seen_at: str

    @classmethod
    def create(
        cls,
        *,
        worker_id: str,
        worker_type: str,
        capabilities: list[str] | tuple[str, ...] = (),
        status: str = "UNKNOWN",
        heartbeat_at: str | None = None,
        last_seen_at: str | None = None,
    ) -> "BrainWorkerV1":
        heartbeat = _timestamp(heartbeat_at or _now(), "heartbeat_at")
        return cls(
            WORKER_SCHEMA_VERSION,
            _safe_ref(worker_id, "worker_id"),
            _safe_ref(worker_type, "worker_type", limit=64),
            tuple(sorted({_safe_ref(item, "capability") for item in capabilities})),
            _normalize_enum(status, "status", HEALTH_STATUSES),
            heartbeat,
            _timestamp(last_seen_at or heartbeat, "last_seen_at"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BrainWorkerV1":
        _unknown_fields(value, {"schema_version", "worker_id", "worker_type", "capabilities", "status", "heartbeat_at", "last_seen_at"}, "BrainWorkerV1")
        if value.get("schema_version") != WORKER_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "worker schema is unsupported.")
        if not isinstance(value.get("capabilities"), list):
            raise ContractValidationError("INVALID_WORKER", "capabilities must be an array.")
        return cls.create(**{key: value.get(key) for key in (
            "worker_id", "worker_type", "capabilities", "status", "heartbeat_at", "last_seen_at",
        )})

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        return result


@dataclass(frozen=True, slots=True)
class LeaseV1:
    schema_version: int
    lease_id: str
    resource_type: str
    resource_id: str
    worker_id: str
    issued_at: str
    expires_at: str
    idempotency_key: str

    @classmethod
    def create(
        cls,
        *,
        lease_id: str,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        issued_at: str,
        expires_at: str,
        idempotency_key: str,
    ) -> "LeaseV1":
        issued = _timestamp(issued_at, "issued_at")
        expires = _timestamp(expires_at, "expires_at")
        if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.fromisoformat(issued.replace("Z", "+00:00")):
            raise ContractValidationError("INVALID_LEASE", "expires_at must be after issued_at.")
        return cls(
            LEASE_SCHEMA_VERSION,
            _safe_ref(lease_id, "lease_id"),
            _safe_ref(resource_type, "resource_type", limit=64),
            _safe_ref(resource_id, "resource_id"),
            _safe_ref(worker_id, "worker_id"),
            issued,
            expires,
            _safe_ref(idempotency_key, "idempotency_key"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LeaseV1":
        _unknown_fields(value, {"schema_version", "lease_id", "resource_type", "resource_id", "worker_id", "issued_at", "expires_at", "idempotency_key"}, "LeaseV1")
        if value.get("schema_version") != LEASE_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "lease schema is unsupported.")
        return cls.create(**{key: value.get(key) for key in (
            "lease_id", "resource_type", "resource_id", "worker_id",
            "issued_at", "expires_at", "idempotency_key",
        )})

    def is_active(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        return current < expiry

    def resource_key(self) -> tuple[str, str]:
        return self.resource_type, self.resource_id

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def can_acquire_lease(
    existing: LeaseV1 | None,
    *,
    now: datetime | None = None,
    worker_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> bool:
    """Return whether a resource has no conflicting active lease."""

    if existing is None or not existing.is_active(now):
        return True
    if worker_id is not None and existing.worker_id == worker_id:
        return existing.resource_key() == (resource_type, resource_id)
    return False


@dataclass(frozen=True, slots=True)
class BusinessImpactV1:
    schema_version: int
    gmv_impact: float | None = None
    profit_impact: float | None = None
    inventory_impact: float | None = None
    customer_impact: float | None = None
    live_impact: float | None = None
    confidence: float | None = None

    @classmethod
    def create(cls, **values: Any) -> "BusinessImpactV1":
        _unknown_fields(values, {"gmv_impact", "profit_impact", "inventory_impact", "customer_impact", "live_impact", "confidence"}, "BusinessImpactV1")
        normalized: dict[str, float | None] = {}
        for field in ("gmv_impact", "profit_impact", "inventory_impact", "customer_impact", "live_impact"):
            raw = values.get(field)
            if raw is None:
                normalized[field] = None
            elif isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
                raise ContractValidationError("INVALID_IMPACT", f"{field} must be finite or null.")
            else:
                normalized[field] = float(raw)
        confidence = values.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            raise ContractValidationError("INVALID_CONFIDENCE", "confidence must be between 0 and 1 or null.")
        return cls(BUSINESS_IMPACT_SCHEMA_VERSION, **normalized, confidence=None if confidence is None else float(confidence))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "BusinessImpactV1":
        if value is None:
            return cls.create()
        _unknown_fields(value, {"schema_version", "gmv_impact", "profit_impact", "inventory_impact", "customer_impact", "live_impact", "confidence"}, "BusinessImpactV1")
        if value.get("schema_version", BUSINESS_IMPACT_SCHEMA_VERSION) != BUSINESS_IMPACT_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "business impact schema is unsupported.")
        return cls.create(**{field: value.get(field) for field in (
            "gmv_impact", "profit_impact", "inventory_impact", "customer_impact", "live_impact", "confidence",
        )})

    def is_unknown(self, field: str) -> bool:
        if field not in {"gmv_impact", "profit_impact", "inventory_impact", "customer_impact", "live_impact", "confidence"}:
            raise ContractValidationError("INVALID_FIELD", "unknown business impact field.")
        return getattr(self, field) is None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _actor_id(value: str | ActorV1, field: str) -> str:
    return value.actor_id if isinstance(value, ActorV1) else _safe_ref(value, field)


@dataclass(frozen=True, slots=True)
class TaskV1:
    schema_version: int
    task_id: str
    scope: ScopeV1
    task_type: str
    priority: int
    status: str
    owner: str | None
    created_by: str
    created_at: str
    updated_at: str
    due_at: str | None
    business_impact: BusinessImpactV1
    source_refs: tuple[str, ...]
    idempotency_key: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        scope: ScopeV1 | Mapping[str, Any],
        task_type: str,
        priority: int,
        status: str = "PENDING",
        owner: str | ActorV1 | None = None,
        created_by: str | ActorV1,
        created_at: str | None = None,
        updated_at: str | None = None,
        due_at: str | None = None,
        business_impact: BusinessImpactV1 | Mapping[str, Any] | None = None,
        source_refs: list[str] | tuple[str, ...] = (),
        idempotency_key: str,
    ) -> "TaskV1":
        normalized_scope = scope if isinstance(scope, ScopeV1) else ScopeV1.from_mapping(scope)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ContractValidationError("INVALID_PRIORITY", "priority must be an integer from 0 to 100.")
        created = _timestamp(created_at or _now(), "created_at")
        updated = _timestamp(updated_at or created, "updated_at")
        due = None if due_at is None else _timestamp(due_at, "due_at")
        return cls(
            TASK_SCHEMA_VERSION,
            _safe_ref(task_id, "task_id"),
            normalized_scope,
            _safe_ref(task_type, "task_type", limit=64),
            priority,
            _normalize_enum(status, "status", TASK_STATUSES),
            None if owner is None else _actor_id(owner, "owner"),
            _actor_id(created_by, "created_by"),
            created,
            updated,
            due,
            business_impact if isinstance(business_impact, BusinessImpactV1) else BusinessImpactV1.from_mapping(business_impact),
            tuple(sorted({_safe_ref(item, "source_ref") for item in source_refs})),
            _safe_ref(idempotency_key, "idempotency_key"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskV1":
        _unknown_fields(value, {"schema_version", "task_id", "scope", "task_type", "priority", "status", "owner", "created_by", "created_at", "updated_at", "due_at", "business_impact", "source_refs", "idempotency_key"}, "TaskV1")
        if value.get("schema_version") != TASK_SCHEMA_VERSION or not isinstance(value.get("source_refs"), list):
            raise ContractValidationError("INVALID_TASK", "task schema or source_refs is invalid.")
        return cls.create(**{key: value.get(key) for key in (
            "task_id", "scope", "task_type", "priority", "status", "owner",
            "created_by", "created_at", "updated_at", "due_at",
            "business_impact", "source_refs", "idempotency_key",
        )})

    def transition(self, status: str) -> "TaskV1":
        next_status = _normalize_enum(status, "status", TASK_STATUSES)
        allowed = {
            "PENDING": {"ASSIGNED", "IN_PROGRESS", "BLOCKED", "CANCELLED", "EXPIRED"},
            "ASSIGNED": {"IN_PROGRESS", "BLOCKED", "CANCELLED", "EXPIRED"},
            "IN_PROGRESS": {"DONE", "BLOCKED", "CANCELLED", "EXPIRED"},
            "BLOCKED": {"ASSIGNED", "IN_PROGRESS", "CANCELLED", "EXPIRED"},
            "DONE": set(),
            "CANCELLED": set(),
            "EXPIRED": set(),
        }
        if next_status not in allowed[self.status]:
            raise ContractValidationError("INVALID_TRANSITION", f"task cannot transition from {self.status} to {next_status}.")
        return TaskV1(
            self.schema_version, self.task_id, self.scope, self.task_type, self.priority,
            next_status, self.owner, self.created_by, self.created_at, _now(),
            self.due_at, self.business_impact, self.source_refs, self.idempotency_key,
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scope"] = self.scope.as_dict()
        result["business_impact"] = self.business_impact.as_dict()
        result["source_refs"] = list(self.source_refs)
        return result


@dataclass(frozen=True, slots=True)
class AlertV1:
    schema_version: int
    alert_id: str
    scope: ScopeV1
    priority: str
    status: str
    reason_code: str
    summary: str
    evidence_refs: tuple[str, ...]
    business_impact: BusinessImpactV1
    recommended_action: str
    created_at: str
    updated_at: str
    dedupe_key: str
    cooldown_until: str | None

    @classmethod
    def create(
        cls,
        *,
        alert_id: str,
        scope: ScopeV1 | Mapping[str, Any],
        priority: str,
        reason_code: str,
        summary: str,
        evidence_refs: list[str] | tuple[str, ...] = (),
        business_impact: BusinessImpactV1 | Mapping[str, Any] | None = None,
        recommended_action: str,
        dedupe_key: str,
        status: str = "OPEN",
        created_at: str | None = None,
        updated_at: str | None = None,
        cooldown_until: str | None = None,
    ) -> "AlertV1":
        created = _timestamp(created_at or _now(), "created_at")
        return cls(
            ALERT_SCHEMA_VERSION,
            _safe_ref(alert_id, "alert_id"),
            scope if isinstance(scope, ScopeV1) else ScopeV1.from_mapping(scope),
            _normalize_enum(priority, "priority", ALERT_PRIORITIES),
            _normalize_enum(status, "status", ALERT_STATUSES),
            _safe_ref(reason_code, "reason_code", limit=80),
            _text(summary, "summary", limit=500),
            tuple(sorted({_safe_ref(item, "evidence_ref") for item in evidence_refs})),
            business_impact if isinstance(business_impact, BusinessImpactV1) else BusinessImpactV1.from_mapping(business_impact),
            _text(recommended_action, "recommended_action", limit=500),
            created,
            _timestamp(updated_at or created, "updated_at"),
            _safe_ref(dedupe_key, "dedupe_key"),
            None if cooldown_until is None else _timestamp(cooldown_until, "cooldown_until"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AlertV1":
        _unknown_fields(value, {"schema_version", "alert_id", "scope", "priority", "status", "reason_code", "summary", "evidence_refs", "business_impact", "recommended_action", "created_at", "updated_at", "dedupe_key", "cooldown_until"}, "AlertV1")
        if value.get("schema_version") != ALERT_SCHEMA_VERSION or not isinstance(value.get("evidence_refs"), list):
            raise ContractValidationError("INVALID_ALERT", "alert schema or evidence_refs is invalid.")
        return cls.create(**{key: value.get(key) for key in (
            "alert_id", "scope", "priority", "reason_code", "summary",
            "evidence_refs", "business_impact", "recommended_action",
            "dedupe_key", "status", "created_at", "updated_at", "cooldown_until",
        )})

    def transition(self, status: str) -> "AlertV1":
        next_status = _normalize_enum(status, "status", ALERT_STATUSES)
        allowed = {
            "OPEN": {"ACKNOWLEDGED", "RESOLVED", "EXPIRED", "CANCELLED"},
            "ACKNOWLEDGED": {"RESOLVED", "EXPIRED", "CANCELLED"},
            "RESOLVED": set(),
            "EXPIRED": set(),
            "CANCELLED": set(),
        }
        if next_status not in allowed[self.status]:
            raise ContractValidationError("INVALID_TRANSITION", f"alert cannot transition from {self.status} to {next_status}.")
        return AlertV1(
            self.schema_version, self.alert_id, self.scope, self.priority, next_status,
            self.reason_code, self.summary, self.evidence_refs, self.business_impact,
            self.recommended_action, self.created_at, _now(), self.dedupe_key,
            self.cooldown_until,
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scope"] = self.scope.as_dict()
        result["evidence_refs"] = list(self.evidence_refs)
        result["business_impact"] = self.business_impact.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class ApprovalRequestV1:
    schema_version: int
    approval_id: str
    scope: ScopeV1
    proposal_id: str
    risk_level: str
    requested_by: str
    requested_at: str
    expires_at: str
    reason: str
    status: str
    decided_by: str | None
    decided_at: str | None
    decision_note: str | None

    @classmethod
    def create(
        cls,
        *,
        approval_id: str,
        scope: ScopeV1 | Mapping[str, Any],
        proposal_id: str,
        risk_level: str,
        requested_by: str | ActorV1,
        requested_at: str,
        expires_at: str,
        reason: str,
        status: str = "PENDING",
        decided_by: str | ActorV1 | None = None,
        decided_at: str | None = None,
        decision_note: str | None = None,
    ) -> "ApprovalRequestV1":
        requested = _timestamp(requested_at, "requested_at")
        expires = _timestamp(expires_at, "expires_at")
        if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.fromisoformat(requested.replace("Z", "+00:00")):
            raise ContractValidationError("INVALID_APPROVAL", "expires_at must be after requested_at.")
        normalized_status = _normalize_enum(status, "status", APPROVAL_STATUSES)
        normalized_decided_by = None if decided_by is None else _actor_id(decided_by, "decided_by")
        if normalized_status != "PENDING" and normalized_decided_by is None:
            raise ContractValidationError("INVALID_APPROVAL", "decided_by is required after a decision.")
        return cls(
            APPROVAL_SCHEMA_VERSION,
            _safe_ref(approval_id, "approval_id"),
            scope if isinstance(scope, ScopeV1) else ScopeV1.from_mapping(scope),
            _safe_ref(proposal_id, "proposal_id"),
            _normalize_enum(risk_level, "risk_level", RISK_LEVELS),
            _actor_id(requested_by, "requested_by"),
            requested,
            expires,
            _text(reason, "reason", limit=1000),
            normalized_status,
            normalized_decided_by,
            None if decided_at is None else _timestamp(decided_at, "decided_at"),
            None if decision_note is None else _text(decision_note, "decision_note", limit=1000),
        )

    def validate_requester(self, actor: ActorV1) -> None:
        if not actor.can_access(self.scope):
            raise ContractValidationError("SCOPE_DENIED", "approval scope exceeds requester permission scope.")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scope"] = self.scope.as_dict()
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApprovalRequestV1":
        _unknown_fields(value, {"schema_version", "approval_id", "scope", "proposal_id", "risk_level", "requested_by", "requested_at", "expires_at", "reason", "status", "decided_by", "decided_at", "decision_note"}, "ApprovalRequestV1")
        if value.get("schema_version") != APPROVAL_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "approval schema is unsupported.")
        return cls.create(**{key: value.get(key) for key in (
            "approval_id", "scope", "proposal_id", "risk_level", "requested_by",
            "requested_at", "expires_at", "reason", "status", "decided_by",
            "decided_at", "decision_note",
        )})


@dataclass(frozen=True, slots=True)
class EventEnvelopeV1:
    schema_version: int
    event_id: str
    event_type: str
    scope: ScopeV1
    actor: ActorV1
    source: str
    occurred_at: str
    received_at: str
    idempotency_key: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        event_type: str,
        scope: ScopeV1 | Mapping[str, Any],
        actor: ActorV1 | Mapping[str, Any],
        source: str,
        occurred_at: str,
        received_at: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> "EventEnvelopeV1":
        normalized_actor = actor if isinstance(actor, ActorV1) else ActorV1.from_mapping(actor)
        normalized_scope = scope if isinstance(scope, ScopeV1) else ScopeV1.from_mapping(scope)
        if not normalized_actor.can_access(normalized_scope):
            raise ContractValidationError("SCOPE_DENIED", "event actor cannot publish outside its scope.")
        return cls(
            EVENT_SCHEMA_VERSION,
            _safe_ref(event_id, "event_id"),
            _safe_ref(event_type, "event_type", limit=80),
            normalized_scope,
            normalized_actor,
            _safe_ref(source, "source", limit=80),
            _timestamp(occurred_at, "occurred_at"),
            _timestamp(received_at, "received_at"),
            _safe_ref(idempotency_key, "idempotency_key"),
            _safe_payload(payload),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventEnvelopeV1":
        _unknown_fields(value, {"schema_version", "event_id", "event_type", "scope", "actor", "source", "occurred_at", "received_at", "idempotency_key", "payload"}, "EventEnvelopeV1")
        if value.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "event schema is unsupported.")
        return cls.create(**{key: value.get(key) for key in (
            "event_id", "event_type", "scope", "actor", "source", "occurred_at",
            "received_at", "idempotency_key", "payload",
        )})

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scope"] = self.scope.as_dict()
        result["actor"] = self.actor.as_dict()
        return result


class IdempotencyLedger:
    """Small in-memory contract helper; production persistence belongs to Phase 2B-2."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], str] = {}

    def register(self, *, kind: str, idempotency_key: str, resource_id: str) -> str:
        key = (_safe_ref(kind, "kind"), _safe_ref(idempotency_key, "idempotency_key"))
        resource = _safe_ref(resource_id, "resource_id")
        existing = self._entries.get(key)
        if existing is not None and existing != resource:
            raise ContractValidationError("IDEMPOTENCY_CONFLICT", "idempotency key maps to another resource.")
        self._entries[key] = resource
        return existing or resource

    def get(self, *, kind: str, idempotency_key: str) -> str | None:
        return self._entries.get((_safe_ref(kind, "kind"), _safe_ref(idempotency_key, "idempotency_key")))
