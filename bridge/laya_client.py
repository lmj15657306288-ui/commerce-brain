"""Small, localhost-only HTTP client for the installed Laya service."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_LAYA_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_TIMEOUT_SECONDS = 60.0
MAX_RESPONSE_BYTES = 1 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class LayaClientError(RuntimeError):
    """Safe client failure that never includes credentials or response bodies."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LayaHealth:
    status: str
    loaded: tuple[str, ...]
    device: str
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "loaded": list(self.loaded),
            "device": self.device,
            "latency_ms": self.latency_ms,
        }


def _normalize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LayaClientError("INVALID_BASE_URL", "Laya base URL is required.")
    parsed = urlparse(value.strip())
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise LayaClientError(
            "NON_LOOPBACK_URL",
            "Laya client only permits an HTTP loopback endpoint.",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LayaClientError("INVALID_BASE_URL", "Laya base URL contains unsupported components.")
    if not parsed.netloc:
        raise LayaClientError("INVALID_BASE_URL", "Laya base URL is invalid.")
    return value.strip().rstrip("/")


def _normalize_timeout(value: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise LayaClientError("INVALID_TIMEOUT", "Laya timeout must be a positive number.") from None
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise LayaClientError("INVALID_TIMEOUT", "Laya timeout is outside the allowed range.")
    return timeout


def _safe_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise LayaClientError("INVALID_REQUEST", "Laya request is not valid JSON.") from error


def _read_capped(response: Any) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise LayaClientError("RESPONSE_TOO_LARGE", "Laya response is too large.")
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise LayaClientError("RESPONSE_TOO_LARGE", "Laya response is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LayaClientError("INVALID_JSON", "Laya returned invalid JSON.") from error
    if not isinstance(value, dict):
        raise LayaClientError("INVALID_SCHEMA", "Laya response must be a JSON object.")
    return value


class LayaClient:
    """Call only the local Laya health and System-1 endpoints."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_LAYA_BASE_URL,
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.base_url = _normalize_base_url(base_url)
        self._api_key = str(api_key or "")
        self.timeout_seconds = _normalize_timeout(timeout_seconds)
        self._last_latency_ms = 0
        self._latency_lock = threading.Lock()

    @property
    def last_latency_ms(self) -> int:
        with self._latency_lock:
            return self._last_latency_ms

    def _request(self, *, path: str, method: str, body: Any = None) -> dict[str, Any]:
        payload = None if body is None else _safe_json_bytes(body)
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=headers,
            method=method,
        )
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = _read_capped(response)
                status = int(getattr(response, "status", 200))
        except HTTPError as error:
            raise LayaClientError(
                f"HTTP_{error.code}",
                f"Laya request failed with HTTP {error.code}.",
            ) from None
        except (TimeoutError, URLError, OSError):
            raise LayaClientError("UNAVAILABLE", "Laya service is unavailable.") from None
        finally:
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
            with self._latency_lock:
                self._last_latency_ms = elapsed_ms
        if status < 200 or status >= 300:
            raise LayaClientError("HTTP_ERROR", "Laya request returned an unsuccessful status.")
        return _parse_object(raw)

    def health(self) -> LayaHealth:
        value = self._request(path="/health", method="GET")
        loaded = value.get("loaded")
        if not isinstance(loaded, list) or any(not isinstance(item, str) for item in loaded):
            raise LayaClientError("INVALID_SCHEMA", "Laya health response has an invalid loaded list.")
        status = value.get("status")
        device = value.get("device")
        if not isinstance(status, str) or not status.strip() or not isinstance(device, str):
            raise LayaClientError("INVALID_SCHEMA", "Laya health response is incomplete.")
        return LayaHealth(
            status=status.strip(),
            loaded=tuple(item.strip() for item in loaded if item.strip()),
            device=device.strip(),
            latency_ms=self.last_latency_ms,
        )

    def systemone(
        self,
        *,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(state, Mapping) or not isinstance(questions, Mapping):
            raise LayaClientError("INVALID_REQUEST", "Laya state and questions must be objects.")
        body: dict[str, Any] = {
            "state": dict(state),
            "questions": dict(questions),
        }
        if model is not None:
            body["model"] = str(model)
        value = self._request(path="/v1/systemone", method="POST", body=body)
        if not isinstance(value.get("answers"), dict):
            raise LayaClientError("INVALID_SCHEMA", "Laya response is missing answers.")
        return value


__all__ = [
    "DEFAULT_LAYA_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "LayaClient",
    "LayaClientError",
    "LayaHealth",
]
