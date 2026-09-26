"""Versioned, proposal-only contracts for Phase 2B synchronization."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA_VERSION = 1
MESSAGE_TYPES = frozenset(
    {
        "hello",
        "welcome",
        "event",
        "task",
        "alert",
        "ack",
        "heartbeat",
        "heartbeat_ack",
        "resume",
    }
)
DEVICE_TYPES = frozenset({"brain", "gateway", "edge"})
PLATFORMS = frozenset({"macos", "linux", "chrome"})
TRUST_STATES = frozenset({"pending", "active", "revoked"})
SESSION_ROLES = DEVICE_TYPES
SESSION_STATES = frozenset({"active", "expired", "revoked"})
TASK_KINDS = frozenset({"review", "sync", "human_action", "outcome"})
TASK_STATUSES = frozenset(
    {"queued", "assigned", "acknowledged", "in_progress", "completed", "blocked", "cancelled"}
)
ALERT_SEVERITIES = frozenset({"info", "warning", "critical"})
ALERT_STATUSES = frozenset({"open", "acknowledged", "resolved", "expired"})
_SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
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
        "screenshot",
        "script",
        "secret",
        "session_token",
        "token",
    }
)
_FORBIDDEN_WRITE_KEYS = frozenset(
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
    """Raised when a Phase 2B contract is invalid or unsafe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _safe_ref(value: Any, field: str, *, allow_broadcast: bool = False) -> str:
    if allow_broadcast and value == "broadcast":
        return value
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value.strip()):
        raise ContractValidationError("INVALID_REF", f"{field} must be a bounded safe reference.")
    return value.strip()


def _text(value: Any, field: str, *, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ContractValidationError("INVALID_FIELD", f"{field} must be bounded text.")
    return value.strip()


def _timestamp(value: Any, field: str) -> str:
    return _text(value, field, limit=80)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ContractValidationError("PAYLOAD_TOO_DEEP", "payload nesting is too deep.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise ContractValidationError("NON_FINITE_VALUE", "payload numbers must be finite.")
        return value
    if isinstance(value, str):
        if len(value) > 1000:
            raise ContractValidationError("PAYLOAD_TEXT_TOO_LARGE", "payload text is too long.")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key).strip()
            lowered = key.lower().replace("-", "_").replace(" ", "_")
            if (
                not _SAFE_KEY.fullmatch(key)
                or lowered in _FORBIDDEN_KEYS
                or (lowered in _FORBIDDEN_WRITE_KEYS and lowered != "platform_write_attempted")
                or lowered.endswith("_token")
                or lowered.endswith("_secret")
            ):
                raise ContractValidationError("SENSITIVE_FIELD", "payload contains a forbidden field.")
            if lowered in {"can_execute", "execution_allowed"} and child is not False:
                raise ContractValidationError("EXECUTION_DISABLED", "execution flags must remain false.")
            if lowered == "platform_write_attempted" and child is not False:
                raise ContractValidationError("PLATFORM_WRITE_DISABLED", "platform writes are disabled.")
            output[key] = _safe_value(child, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        if len(value) > 200:
            raise ContractValidationError("PAYLOAD_LIST_TOO_LARGE", "payload list is too long.")
        return [_safe_value(item, depth=depth + 1) for item in value]
    raise ContractValidationError("UNSUPPORTED_VALUE", "payload contains an unsupported value.")


def _payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("INVALID_PAYLOAD", "payload must be an object.")
    normalized = _safe_value(value)
    if not isinstance(normalized, dict):
        raise ContractValidationError("INVALID_PAYLOAD", "payload must be an object.")
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ContractValidationError("PAYLOAD_TOO_LARGE", "payload exceeds the bounded message size.")
    return normalized


def _unknown(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ContractValidationError("UNKNOWN_FIELDS", f"{name} contains unsupported fields.")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class DeviceV1:
    schema_version: int
    device_id: str
    device_type: str
    platform: str
    client_version: str
    capabilities: tuple[str, ...]
    trust_state: str
    last_seen_at: str

    @classmethod
    def create(
        cls,
        *,
        device_id: str | None = None,
        device_type: str,
        platform: str,
        client_version: str,
        capabilities: list[str] | tuple[str, ...] = (),
        trust_state: str = "active",
        last_seen_at: str | None = None,
    ) -> "DeviceV1":
        device_type = _text(device_type, "device_type", limit=32).lower()
        platform = _text(platform, "platform", limit=32).lower()
        trust_state = _text(trust_state, "trust_state", limit=32).lower()
        if device_type not in DEVICE_TYPES or platform not in PLATFORMS or trust_state not in TRUST_STATES:
            raise ContractValidationError("INVALID_DEVICE", "device type, platform or trust state is unsupported.")
        normalized_capabilities = tuple(_safe_ref(item, "capability") for item in capabilities)
        return cls(
            SCHEMA_VERSION,
            _safe_ref(device_id or _new_id("device"), "device_id"),
            device_type,
            platform,
            _text(client_version, "client_version", limit=32),
            normalized_capabilities,
            trust_state,
            _timestamp(last_seen_at or _now(), "last_seen_at"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeviceV1":
        _unknown(
            value,
            {"schema_version", "device_id", "device_type", "platform", "client_version", "capabilities", "trust_state", "last_seen_at"},
            "DeviceV1",
        )
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "device schema is unsupported.")
        capabilities = value.get("capabilities")
        if not isinstance(capabilities, list):
            raise ContractValidationError("INVALID_DEVICE", "capabilities must be an array.")
        return cls.create(
            device_id=value.get("device_id"),
            device_type=value.get("device_type"),
            platform=value.get("platform"),
            client_version=value.get("client_version"),
            capabilities=capabilities,
            trust_state=value.get("trust_state"),
            last_seen_at=value.get("last_seen_at"),
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        return result


@dataclass(frozen=True, slots=True)
class SessionV1:
    schema_version: int
    session_id: str
    device_id: str
    role: str
    scope: dict[str, str]
    issued_at: str
    expires_at: str
    resume_cursor: int
    state: str

    @classmethod
    def create(
        cls,
        *,
        device_id: str,
        role: str,
        scope: Mapping[str, Any],
        issued_at: str | None = None,
        expires_at: str,
        resume_cursor: int = 0,
        session_id: str | None = None,
        state: str = "active",
    ) -> "SessionV1":
        normalized_scope = _payload(scope)
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in normalized_scope.items()):
            raise ContractValidationError("INVALID_SCOPE", "session scope values must be text.")
        role = _text(role, "role", limit=32).lower()
        state = _text(state, "state", limit=32).lower()
        if role not in SESSION_ROLES or state not in SESSION_STATES:
            raise ContractValidationError("INVALID_SESSION", "session role or state is unsupported.")
        if not isinstance(resume_cursor, int) or isinstance(resume_cursor, bool) or resume_cursor < 0:
            raise ContractValidationError("INVALID_CURSOR", "resume_cursor must be a non-negative integer.")
        return cls(
            SCHEMA_VERSION,
            _safe_ref(session_id or _new_id("session"), "session_id"),
            _safe_ref(device_id, "device_id"),
            role,
            normalized_scope,
            _timestamp(issued_at or _now(), "issued_at"),
            _timestamp(expires_at, "expires_at"),
            resume_cursor,
            state,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SessionV1":
        _unknown(value, {"schema_version", "session_id", "device_id", "role", "scope", "issued_at", "expires_at", "resume_cursor", "state"}, "SessionV1")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "session schema is unsupported.")
        return cls.create(
            session_id=value.get("session_id"),
            device_id=value.get("device_id"),
            role=value.get("role"),
            scope=value.get("scope"),
            issued_at=value.get("issued_at"),
            expires_at=value.get("expires_at"),
            resume_cursor=value.get("resume_cursor"),
            state=value.get("state"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TaskV1:
    schema_version: int
    task_id: str
    kind: str
    status: str
    priority: int
    created_by: str
    assigned_to: str | None
    idempotency_key: str
    payload: dict[str, Any]
    can_execute: bool
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str | None = None,
        kind: str,
        status: str = "queued",
        priority: int = 0,
        created_by: str,
        assigned_to: str | None = None,
        idempotency_key: str | None = None,
        payload: Mapping[str, Any] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> "TaskV1":
        kind = _text(kind, "kind", limit=32).lower()
        status = _text(status, "status", limit=32).lower()
        if kind not in TASK_KINDS or status not in TASK_STATUSES:
            raise ContractValidationError("INVALID_TASK", "task kind or status is unsupported.")
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 100:
            raise ContractValidationError("INVALID_PRIORITY", "task priority must be between 0 and 100.")
        if assigned_to is not None:
            assigned_to = _safe_ref(assigned_to, "assigned_to")
        return cls(
            SCHEMA_VERSION,
            _safe_ref(task_id or _new_id("task"), "task_id"),
            kind,
            status,
            priority,
            _safe_ref(created_by, "created_by"),
            assigned_to,
            _safe_ref(idempotency_key or _new_id("idem"), "idempotency_key"),
            _payload(payload or {}),
            False,
            _timestamp(created_at or _now(), "created_at"),
            _timestamp(updated_at or created_at or _now(), "updated_at"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskV1":
        _unknown(
            value,
            {"schema_version", "task_id", "kind", "status", "priority", "created_by", "assigned_to", "idempotency_key", "payload", "can_execute", "created_at", "updated_at"},
            "TaskV1",
        )
        if value.get("schema_version") != SCHEMA_VERSION or value.get("can_execute") is not False:
            raise ContractValidationError("EXECUTION_DISABLED", "task schema or execution flag is invalid.")
        return cls.create(
            task_id=value.get("task_id"),
            kind=value.get("kind"),
            status=value.get("status"),
            priority=value.get("priority"),
            created_by=value.get("created_by"),
            assigned_to=value.get("assigned_to"),
            idempotency_key=value.get("idempotency_key"),
            payload=value.get("payload"),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
        )

    def transition(self, status: str, *, updated_at: str | None = None) -> "TaskV1":
        status = _text(status, "status", limit=32).lower()
        transitions = {
            "queued": {"assigned", "acknowledged", "in_progress", "blocked", "cancelled"},
            "assigned": {"acknowledged", "in_progress", "blocked", "cancelled"},
            "acknowledged": {"in_progress", "blocked", "cancelled"},
            "in_progress": {"completed", "blocked", "cancelled"},
            "blocked": {"queued", "cancelled"},
            "completed": set(),
            "cancelled": set(),
        }
        if status not in transitions.get(self.status, set()):
            raise ContractValidationError("INVALID_TASK_TRANSITION", f"cannot move task from {self.status} to {status}.")
        return replace(self, status=status, updated_at=_timestamp(updated_at or _now(), "updated_at"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AlertV1:
    schema_version: int
    alert_id: str
    severity: str
    dedupe_key: str
    status: str
    title: str
    evidence_refs: tuple[str, ...]
    created_at: str
    expires_at: str

    @classmethod
    def create(
        cls,
        *,
        alert_id: str | None = None,
        severity: str,
        dedupe_key: str,
        status: str = "open",
        title: str,
        evidence_refs: list[str] | tuple[str, ...] = (),
        created_at: str | None = None,
        expires_at: str,
    ) -> "AlertV1":
        severity = _text(severity, "severity", limit=20).lower()
        status = _text(status, "status", limit=20).lower()
        if severity not in ALERT_SEVERITIES or status not in ALERT_STATUSES:
            raise ContractValidationError("INVALID_ALERT", "alert severity or status is unsupported.")
        return cls(
            SCHEMA_VERSION,
            _safe_ref(alert_id or _new_id("alert"), "alert_id"),
            severity,
            _safe_ref(dedupe_key, "dedupe_key"),
            status,
            _text(title, "title", limit=240),
            tuple(_safe_ref(item, "evidence_ref") for item in evidence_refs),
            _timestamp(created_at or _now(), "created_at"),
            _timestamp(expires_at, "expires_at"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AlertV1":
        _unknown(value, {"schema_version", "alert_id", "severity", "dedupe_key", "status", "title", "evidence_refs", "created_at", "expires_at"}, "AlertV1")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "alert schema is unsupported.")
        evidence_refs = value.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            raise ContractValidationError("INVALID_ALERT", "evidence_refs must be an array.")
        return cls.create(
            alert_id=value.get("alert_id"),
            severity=value.get("severity"),
            dedupe_key=value.get("dedupe_key"),
            status=value.get("status"),
            title=value.get("title"),
            evidence_refs=evidence_refs,
            created_at=value.get("created_at"),
            expires_at=value.get("expires_at"),
        )

    def transition(self, status: str) -> "AlertV1":
        status = _text(status, "status", limit=20).lower()
        transitions = {
            "open": {"acknowledged", "resolved", "expired"},
            "acknowledged": {"resolved", "expired"},
            "resolved": set(),
            "expired": set(),
        }
        if status not in transitions.get(self.status, set()):
            raise ContractValidationError("INVALID_ALERT_TRANSITION", f"cannot move alert from {self.status} to {status}.")
        return replace(self, status=status)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_refs"] = list(self.evidence_refs)
        return result


@dataclass(frozen=True, slots=True)
class EventV1:
    schema_version: int
    event_id: str
    topic: str
    aggregate_id: str
    aggregate_version: int
    origin_device_id: str
    payload: dict[str, Any]
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        topic: str,
        aggregate_id: str,
        aggregate_version: int,
        origin_device_id: str,
        payload: Mapping[str, Any],
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> "EventV1":
        topic = _text(topic, "topic", limit=80)
        if "." not in topic:
            raise ContractValidationError("INVALID_EVENT", "event topic must be namespaced.")
        if not isinstance(aggregate_version, int) or isinstance(aggregate_version, bool) or aggregate_version < 1:
            raise ContractValidationError("INVALID_EVENT_VERSION", "aggregate_version must be positive.")
        return cls(
            SCHEMA_VERSION,
            _safe_ref(event_id or _new_id("evt"), "event_id"),
            topic,
            _safe_ref(aggregate_id, "aggregate_id"),
            aggregate_version,
            _safe_ref(origin_device_id, "origin_device_id"),
            _payload(payload),
            _timestamp(created_at or _now(), "created_at"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventV1":
        _unknown(value, {"schema_version", "event_id", "topic", "aggregate_id", "aggregate_version", "origin_device_id", "payload", "created_at"}, "EventV1")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "event schema is unsupported.")
        return cls.create(
            event_id=value.get("event_id"),
            topic=value.get("topic"),
            aggregate_id=value.get("aggregate_id"),
            aggregate_version=value.get("aggregate_version"),
            origin_device_id=value.get("origin_device_id"),
            payload=value.get("payload"),
            created_at=value.get("created_at"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CentralEnvelopeV1:
    schema_version: int
    message_id: str
    event_id: str
    message_type: str
    sender_device_id: str
    recipient_device_id: str
    session_id: str
    sent_at: str
    correlation_id: str
    idempotency_key: str
    cursor: int
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        message_type: str,
        sender_device_id: str,
        recipient_device_id: str,
        session_id: str,
        payload: Mapping[str, Any],
        event_id: str | None = None,
        message_id: str | None = None,
        sent_at: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        cursor: int = 0,
    ) -> "CentralEnvelopeV1":
        message_type = _text(message_type, "message_type", limit=32).lower()
        if message_type not in MESSAGE_TYPES:
            raise ContractValidationError("INVALID_MESSAGE_TYPE", "message_type is unsupported.")
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ContractValidationError("INVALID_CURSOR", "cursor must be a non-negative integer.")
        return cls(
            SCHEMA_VERSION,
            _safe_ref(message_id or _new_id("msg"), "message_id"),
            _safe_ref(event_id or _new_id("evt"), "event_id"),
            message_type,
            _safe_ref(sender_device_id, "sender_device_id"),
            _safe_ref(recipient_device_id, "recipient_device_id", allow_broadcast=True),
            _safe_ref(session_id, "session_id"),
            _timestamp(sent_at or _now(), "sent_at"),
            _safe_ref(correlation_id or _new_id("corr"), "correlation_id"),
            _safe_ref(idempotency_key or _new_id("idem"), "idempotency_key"),
            cursor,
            _payload(payload),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CentralEnvelopeV1":
        _unknown(
            value,
            {"schema_version", "message_id", "event_id", "message_type", "sender_device_id", "recipient_device_id", "session_id", "sent_at", "correlation_id", "idempotency_key", "cursor", "payload"},
            "CentralEnvelopeV1",
        )
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ContractValidationError("SCHEMA_VERSION_MISMATCH", "envelope schema is unsupported.")
        return cls.create(
            message_id=value.get("message_id"),
            event_id=value.get("event_id"),
            message_type=value.get("message_type"),
            sender_device_id=value.get("sender_device_id"),
            recipient_device_id=value.get("recipient_device_id"),
            session_id=value.get("session_id"),
            sent_at=value.get("sent_at"),
            correlation_id=value.get("correlation_id"),
            idempotency_key=value.get("idempotency_key"),
            cursor=value.get("cursor"),
            payload=value.get("payload"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
