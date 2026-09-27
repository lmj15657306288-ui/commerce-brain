"""Server-side clock abstractions for the local Control Plane."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC datetime."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Deterministic clock for service tests."""

    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        self._value = value.astimezone(timezone.utc)
        self._lock = RLock()

    def now(self) -> datetime:
        with self._lock:
            return self._value

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        with self._lock:
            self._value = value.astimezone(timezone.utc)

    def advance(self, **kwargs: float | int) -> datetime:
        with self._lock:
            self._value += timedelta(**kwargs)
            return self._value


def iso_now(clock: Clock) -> str:
    return clock.now().astimezone(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)
