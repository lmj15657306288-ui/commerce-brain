"""Machine-readable Control Plane errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ControlPlaneError(RuntimeError):
    code: str
    message: str
    status_code: int = 400
    details: Any = None

    def __post_init__(self) -> None:
        super().__init__(self.message)


def unauthenticated(message: str = "authentication is required") -> ControlPlaneError:
    return ControlPlaneError("UNAUTHENTICATED", message, 401)


def forbidden(message: str = "access is denied", *, details: Any = None) -> ControlPlaneError:
    return ControlPlaneError("FORBIDDEN", message, 403, details)


def scope_denied(message: str = "requested scope is not authorized") -> ControlPlaneError:
    return ControlPlaneError("SCOPE_DENIED", message, 403)


def not_found(message: str = "resource was not found") -> ControlPlaneError:
    return ControlPlaneError("NOT_FOUND", message, 404)


def conflict(message: str = "resource conflict", *, code: str = "CONFLICT") -> ControlPlaneError:
    return ControlPlaneError(code, message, 409)


def invalid_transition(message: str) -> ControlPlaneError:
    return ControlPlaneError("INVALID_TRANSITION", message, 409)


def validation(message: str, *, details: Any = None) -> ControlPlaneError:
    return ControlPlaneError("VALIDATION_ERROR", message, 422, details)
