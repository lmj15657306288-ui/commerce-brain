"""Fail-closed request identity providers for the local Control Plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

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


class FailClosedAuthProvider:
    """Default provider. It never trusts arbitrary request headers."""

    def authenticate(self, request: object) -> RequestIdentity:
        return RequestIdentity.unauthenticated()

    def authenticate_websocket(self, websocket: object) -> RequestIdentity:
        return RequestIdentity.unauthenticated()


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


TestAuthProvider = LocalAuthProvider


def require_authenticated(identity: RequestIdentity) -> None:
    if not identity.authenticated:
        raise unauthenticated()
