"""Device registration and revocable session lifecycle for Phase 2B."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Mapping

from .contracts import DeviceV1, SessionV1


class IdentityError(RuntimeError):
    """Raised when a device or session cannot be used."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, DeviceV1] = {}
        self._lock = RLock()

    def register(self, device: DeviceV1) -> DeviceV1:
        with self._lock:
            existing = self._devices.get(device.device_id)
            if existing and existing.trust_state == "revoked":
                raise IdentityError("device is revoked")
            self._devices[device.device_id] = device
            return device

    def get(self, device_id: str) -> DeviceV1:
        with self._lock:
            try:
                return self._devices[device_id]
            except KeyError as exc:
                raise IdentityError("device is unknown") from exc

    def set_trust_state(self, device_id: str, trust_state: str) -> DeviceV1:
        with self._lock:
            device = self.get(device_id)
            updated = DeviceV1.create(
                device_id=device.device_id,
                device_type=device.device_type,
                platform=device.platform,
                client_version=device.client_version,
                capabilities=device.capabilities,
                trust_state=trust_state,
                last_seen_at=device.last_seen_at,
            )
            self._devices[device_id] = updated
            return updated

    def touch(self, device_id: str) -> DeviceV1:
        with self._lock:
            device = self.get(device_id)
            updated = DeviceV1.create(
                device_id=device.device_id,
                device_type=device.device_type,
                platform=device.platform,
                client_version=device.client_version,
                capabilities=device.capabilities,
                trust_state=device.trust_state,
            )
            self._devices[device_id] = updated
            return updated


class SessionManager:
    def __init__(self, *, clock=_now) -> None:
        self._clock = clock
        self._sessions: dict[str, SessionV1] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        device: DeviceV1,
        scope: Mapping[str, Any],
        ttl_seconds: int = 3600,
        resume_cursor: int = 0,
    ) -> SessionV1:
        if device.trust_state != "active":
            raise IdentityError("device is not active")
        if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 86400:
            raise IdentityError("session ttl is outside the allowed range")
        now = self._clock()
        session = SessionV1.create(
            device_id=device.device_id,
            role=device.device_type,
            scope=scope,
            issued_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=ttl_seconds)),
            resume_cursor=resume_cursor,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> SessionV1:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise IdentityError("session is unknown") from exc

    def validate(self, session_id: str, device_id: str) -> SessionV1:
        session = self.get(session_id)
        if session.device_id != device_id:
            raise IdentityError("session device mismatch")
        if session.state != "active":
            raise IdentityError("session is not active")
        expires = datetime.fromisoformat(session.expires_at)
        if expires <= self._clock():
            self._sessions[session_id] = SessionV1.create(
                session_id=session.session_id,
                device_id=session.device_id,
                role=session.role,
                scope=session.scope,
                issued_at=session.issued_at,
                expires_at=session.expires_at,
                resume_cursor=session.resume_cursor,
                state="expired",
            )
            raise IdentityError("session is expired")
        return session

    def advance_cursor(self, session_id: str, cursor: int) -> SessionV1:
        session = self.get(session_id)
        if cursor < session.resume_cursor:
            return session
        updated = SessionV1.create(
            session_id=session.session_id,
            device_id=session.device_id,
            role=session.role,
            scope=session.scope,
            issued_at=session.issued_at,
            expires_at=session.expires_at,
            resume_cursor=cursor,
            state=session.state,
        )
        with self._lock:
            self._sessions[session_id] = updated
        return updated

    def revoke(self, session_id: str) -> SessionV1:
        session = self.get(session_id)
        updated = SessionV1.create(
            session_id=session.session_id,
            device_id=session.device_id,
            role=session.role,
            scope=session.scope,
            issued_at=session.issued_at,
            expires_at=session.expires_at,
            resume_cursor=session.resume_cursor,
            state="revoked",
        )
        with self._lock:
            self._sessions[session_id] = updated
        return updated
