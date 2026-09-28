"""Fail-closed request identity providers for the local Control Plane."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

import jwt

from .errors import unauthenticated


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    actor_id: str
    organization_id: str
    device_id: str | None
    roles: tuple[str, ...]
    authenticated: bool
    auth_source: str

    @classmethod
    def unauthenticated(cls) -> "RequestIdentity":
        return cls("", "", None, (), False, "none")


class AuthProvider(Protocol):
    def authenticate(self, request: object) -> RequestIdentity:
        ...

    def authenticate_websocket(self, websocket: object) -> RequestIdentity:
        ...

    @property
    def configured(self) -> bool:
        ...


class FailClosedAuthProvider:
    """Default provider. It never trusts arbitrary request headers."""

    def authenticate(self, request: object) -> RequestIdentity:
        return RequestIdentity.unauthenticated()

    def authenticate_websocket(self, websocket: object) -> RequestIdentity:
        return RequestIdentity.unauthenticated()

    @property
    def configured(self) -> bool:
        return False


class LocalAuthProvider:
    """Explicit local/test provider keyed by a pre-registered opaque token.

    This provider is intentionally not a production login system. A token is
    accepted only when it exactly matches a token supplied at construction.
    """

    def __init__(self, identities: Mapping[str, RequestIdentity]) -> None:
        self._identities = dict(identities)

    def _lookup(self, headers: Mapping[str, str]) -> RequestIdentity:
        token = str(headers.get("x-local-auth", "")).strip()
        identity = self._identities.get(token)
        if identity is None or not identity.authenticated:
            return RequestIdentity.unauthenticated()
        return identity

    def authenticate(self, request: object) -> RequestIdentity:
        return self._lookup(getattr(request, "headers", {}))

    def authenticate_websocket(self, websocket: object) -> RequestIdentity:
        return self._lookup(getattr(websocket, "headers", {}))

    @property
    def configured(self) -> bool:
        return bool(self._identities)


TestAuthProvider = LocalAuthProvider


def _claim_time(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None
    return None


class JWTAuthProvider:
    """Production bearer-token verifier.

    This class verifies signatures and claims only. It does not implement
    registration, password login, refresh tokens, or an identity UI.
    """

    def __init__(
        self,
        *,
        public_key: str,
        issuer: str,
        audience: str,
        device_validator: Callable[[str, str, str], bool] | None = None,
        role_validator: Callable[[str, str, tuple[str, ...]], bool] | None = None,
        leeway_seconds: int = 30,
    ) -> None:
        if not public_key.strip() or not issuer.strip() or not audience.strip():
            raise ValueError("JWT public key, issuer, and audience are required")
        self.public_key = public_key
        self.issuer = issuer
        self.audience = audience
        self.device_validator = device_validator
        self.role_validator = role_validator
        self.leeway_seconds = leeway_seconds

    @property
    def configured(self) -> bool:
        return True

    def _token(self, headers: Mapping[str, str]) -> str | None:
        value = str(headers.get("authorization", "")).strip()
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return token.strip()

    def _decode(self, token: str) -> RequestIdentity:
        try:
            claims = jwt.decode(
                token,
                self.public_key,
                algorithms=["EdDSA"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "jti",
                        "actor_id",
                        "organization_id",
                        "roles",
                    ],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
            issued = _claim_time(claims.get("iat", claims.get("issued_at")))
            expires = _claim_time(claims.get("exp", claims.get("expires_at")))
            now = time.time()
            if issued is None or expires is None:
                raise ValueError("token time claims are required")
            if issued > now + self.leeway_seconds or expires <= now - self.leeway_seconds:
                raise ValueError("token is outside its validity window")
            actor_id = claims.get("actor_id")
            organization_id = claims.get("organization_id")
            roles = claims.get("roles")
            if (
                not isinstance(actor_id, str)
                or not actor_id
                or not isinstance(organization_id, str)
                or not organization_id
                or not isinstance(roles, list)
                or not all(isinstance(item, str) and item for item in roles)
            ):
                raise ValueError("identity claims are invalid")
            normalized_roles = tuple(roles)
            if self.role_validator is not None and not self.role_validator(
                actor_id,
                organization_id,
                normalized_roles,
            ):
                raise ValueError("identity roles are not assigned")
            device_id = claims.get("device_id")
            if device_id is not None and (
                not isinstance(device_id, str)
                or not device_id
                or self.device_validator is None
                or not self.device_validator(actor_id, organization_id, device_id)
            ):
                raise ValueError("device session is invalid")
            return RequestIdentity(
                actor_id=actor_id,
                organization_id=organization_id,
                device_id=device_id,
                roles=normalized_roles,
                authenticated=True,
                auth_source="jwt",
            )
        except Exception:
            # Do not expose token parsing, issuer, key, or signature details.
            return RequestIdentity.unauthenticated()

    def authenticate(self, request: object) -> RequestIdentity:
        token = self._token(getattr(request, "headers", {}))
        return RequestIdentity.unauthenticated() if token is None else self._decode(token)

    def authenticate_websocket(self, websocket: object) -> RequestIdentity:
        token = self._token(getattr(websocket, "headers", {}))
        return RequestIdentity.unauthenticated() if token is None else self._decode(token)


def require_authenticated(identity: RequestIdentity) -> None:
    if not identity.authenticated:
        raise unauthenticated()
