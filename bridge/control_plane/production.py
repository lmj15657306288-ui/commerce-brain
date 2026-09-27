"""Production-like application wiring for the Cloud Control Plane."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI

from core.context_registry import ContextRegistry
from core.postgres_persistence import PostgresPersistenceAdapter
from core.contracts import DeviceSessionV1

from .app import create_app
from .auth import JWTAuthProvider
from .redis_layer import RedisEphemeralLayer


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for production Control Plane")
    return value


def _public_key() -> str:
    inline = os.environ.get("JWT_PUBLIC_KEY", "").strip()
    path = os.environ.get("JWT_PUBLIC_KEY_PATH", "").strip()
    if inline and path:
        raise RuntimeError("configure only one of JWT_PUBLIC_KEY or JWT_PUBLIC_KEY_PATH")
    if inline:
        return inline.replace("\\n", "\n")
    if path:
        return Path(path).read_text(encoding="utf-8")
    raise RuntimeError("JWT_PUBLIC_KEY or JWT_PUBLIC_KEY_PATH is required")


def _csv(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def create_production_app() -> FastAPI:
    environment = os.environ.get("CONTROL_PLANE_ENV", "production").strip().lower()
    if environment not in {"production", "staging", "local-production"}:
        raise RuntimeError("CONTROL_PLANE_ENV must be production, staging, or local-production")
    database_url = _required("DATABASE_URL")
    redis_url = _required("REDIS_URL")
    issuer = _required("JWT_ISSUER")
    audience = _required("JWT_AUDIENCE")
    public_key = _public_key()
    min_pool = int(os.environ.get("POSTGRES_POOL_MIN", "2"))
    max_pool = int(os.environ.get("POSTGRES_POOL_MAX", "10"))
    adapter = PostgresPersistenceAdapter(
        database_url,
        min_size=min_pool,
        max_size=max_pool,
    )
    registry = ContextRegistry(adapter)
    ephemeral = RedisEphemeralLayer(redis_url)

    def device_validator(actor_id: str, organization_id: str, device_id: str) -> bool:
        raw = adapter.get_record("cp_device_sessions", device_id)
        if raw is None:
            return False
        try:
            device = DeviceSessionV1.from_mapping(raw["device"])
            expires_at = datetime.fromisoformat(
                str(raw["expires_at"]).replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return (
                device.user_id == actor_id
                and device.organization_id == organization_id
                and device.status == "ACTIVE"
                and expires_at.astimezone(timezone.utc) > datetime.now(timezone.utc)
            )
        except Exception:
            return False

    auth = JWTAuthProvider(
        public_key=public_key,
        issuer=issuer,
        audience=audience,
        device_validator=device_validator,
    )
    app = create_app(
        adapter=adapter,
        registry=registry,
        auth_provider=auth,
        ephemeral=ephemeral,
        allowed_origins=_csv("CORS_ALLOWED_ORIGINS"),
        rate_limit_enabled=True,
        rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120")),
    )
    app.state.production_config = {
        "environment": environment,
        "app_version": os.environ.get("APP_VERSION", "unversioned"),
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
        "public_base_url": os.environ.get("PUBLIC_BASE_URL", ""),
        "postgres_pool_min": min_pool,
        "postgres_pool_max": max_pool,
    }
    return app


app = create_production_app()
