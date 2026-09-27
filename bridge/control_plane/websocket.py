"""Bounded WebSocket event broker for low-latency delivery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from core.contracts import ScopeV1

from .event_store import StoredEvent


@dataclass
class EventSubscription:
    client_id: str
    scope: ScopeV1
    max_queue: int = 100
    visible: Any = None
    queue: asyncio.Queue[dict[str, Any]] = field(init=False)
    closed_reason: str | None = None
    close_callback: Any = None

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.max_queue)

    def offer(self, event: StoredEvent) -> bool:
        if self.closed_reason is not None:
            return False
        try:
            self.queue.put_nowait(event.as_dict())
            return True
        except asyncio.QueueFull:
            self.closed_reason = "SLOW_CONSUMER"
            callback = self.close_callback
            if callback is not None:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(callback("SLOW_CONSUMER"))
                except RuntimeError:
                    pass
            return False


class EventBroker:
    def __init__(self, *, default_queue_size: int = 100) -> None:
        if not 1 <= default_queue_size <= 500:
            raise ValueError("default_queue_size must be between 1 and 500")
        self.default_queue_size = default_queue_size
        self._subscriptions: dict[str, EventSubscription] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        *,
        client_id: str,
        scope: ScopeV1,
        max_queue: int | None = None,
        visible: Any = None,
        close_callback: Any = None,
    ) -> EventSubscription:
        subscription = EventSubscription(
            client_id=client_id,
            scope=scope,
            max_queue=max_queue or self.default_queue_size,
            visible=visible,
            close_callback=close_callback,
        )
        async with self._lock:
            self._subscriptions[client_id] = subscription
        return subscription

    async def unsubscribe(self, client_id: str) -> None:
        async with self._lock:
            self._subscriptions.pop(client_id, None)

    async def publish(self, event: StoredEvent) -> None:
        async with self._lock:
            subscriptions = list(self._subscriptions.values())
        for subscription in subscriptions:
            if (
                subscription.scope.contains(event.event.scope)
                and (subscription.visible is None or subscription.visible(event.event))
            ):
                subscription.offer(event)

    def publish_nowait(self, event: StoredEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(event))

    async def active_count(self) -> int:
        async with self._lock:
            return len(self._subscriptions)
