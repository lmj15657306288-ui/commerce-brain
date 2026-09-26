"""WebSocket-shaped realtime transport and three-node fixture gateway.

The fixture uses asyncio queues with the same envelope and ACK semantics as a
network WebSocket. It keeps Phase 2B locally testable without adding a runtime
dependency or opening a public listener. A production WSS adapter can replace
the transport class later.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from .contracts import CentralEnvelopeV1, DeviceV1
from .offline_queue import OfflineQueue


class WebSocketTransportError(RuntimeError):
    """Raised when a fixture WebSocket is closed or receives invalid data."""


class WebSocketEndpoint(Protocol):
    async def send(self, value: Mapping[str, Any]) -> None:
        ...

    async def recv(self) -> Mapping[str, Any]:
        ...

    async def close(self) -> None:
        ...


class JsonWebSocketAdapter:
    """Adapt an external JSON WebSocket client without importing its package.

    The injected endpoint may be provided by a real WSS library in deployment.
    This adapter keeps envelope validation at the Commerce Brain boundary.
    """

    def __init__(self, endpoint: Any) -> None:
        self.endpoint = endpoint

    async def send(self, value: Mapping[str, Any]) -> None:
        await self.endpoint.send(CentralEnvelopeV1.from_mapping(value).as_dict())

    async def recv(self) -> dict[str, Any]:
        raw = await self.endpoint.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            import json

            raw = json.loads(raw)
        if not isinstance(raw, Mapping):
            raise WebSocketTransportError("websocket message must be a JSON object")
        return CentralEnvelopeV1.from_mapping(raw).as_dict()

    async def close(self) -> None:
        await self.endpoint.close()


class InMemoryWebSocket:
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._peer: "InMemoryWebSocket | None" = None
        self._closed = False

    def pair_with(self, peer: "InMemoryWebSocket") -> None:
        self._peer = peer

    async def send(self, value: Mapping[str, Any]) -> None:
        if self._closed or self._peer is None or self._peer._closed:
            raise WebSocketTransportError("websocket is closed")
        await self._peer._incoming.put(dict(value))

    async def recv(self) -> dict[str, Any]:
        if self._closed:
            raise WebSocketTransportError("websocket is closed")
        return await self._incoming.get()

    async def close(self) -> None:
        self._closed = True


def _pair() -> tuple[InMemoryWebSocket, InMemoryWebSocket]:
    left = InMemoryWebSocket()
    right = InMemoryWebSocket()
    left.pair_with(right)
    right.pair_with(left)
    return left, right


class FixtureGateway:
    """Minimal Gateway relay for Brain and Edge fixture clients."""

    def __init__(self, *, device_id: str = "device_gateway_fixture") -> None:
        self.device_id = device_id
        self._connections: dict[str, InMemoryWebSocket] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._handshaken: set[str] = set()
        self._history: list[CentralEnvelopeV1] = []
        self._closed = False

    async def connect(self, device_id: str) -> InMemoryWebSocket:
        if self._closed:
            raise WebSocketTransportError("gateway is closed")
        client, server = _pair()
        old = self._connections.pop(device_id, None)
        if old is not None:
            await old.close()
        self._connections[device_id] = server
        self._tasks[device_id] = asyncio.create_task(self._serve(device_id, server))
        return client

    async def _serve(self, device_id: str, server: InMemoryWebSocket) -> None:
        try:
            while not self._closed:
                raw = await server.recv()
                envelope = CentralEnvelopeV1.from_mapping(raw)
                if envelope.sender_device_id != device_id:
                    raise WebSocketTransportError("sender does not match the connection identity")
                if envelope.message_type == "hello":
                    self._handshaken.add(device_id)
                    welcome = CentralEnvelopeV1.create(
                        message_type="welcome",
                        sender_device_id=self.device_id,
                        recipient_device_id=device_id,
                        session_id=envelope.session_id,
                        correlation_id=envelope.correlation_id,
                        idempotency_key=f"idem_welcome_{device_id}",
                        payload={"current_cursor": 0, "heartbeat_interval_seconds": 30},
                    )
                    await server.send(welcome.as_dict())
                    resume_cursor = envelope.payload.get("resume_cursor", 0)
                    if isinstance(resume_cursor, int) and resume_cursor >= 0:
                        for previous in self._history:
                            if previous.cursor <= resume_cursor:
                                continue
                            if previous.recipient_device_id not in {device_id, "broadcast"}:
                                continue
                            await server.send(previous.as_dict())
                    continue
                if device_id not in self._handshaken:
                    raise WebSocketTransportError("HELLO is required before data messages")
                if envelope.message_type == "resume":
                    continue
                if envelope.recipient_device_id == "broadcast":
                    targets = [
                        (target_id, target)
                        for target_id, target in self._connections.items()
                        if target_id != device_id and target_id in self._handshaken
                    ]
                else:
                    target = self._connections.get(envelope.recipient_device_id)
                    targets = [(envelope.recipient_device_id, target)] if target else []
                for target_id, target in targets:
                    if target is not None:
                        if envelope.message_type in {"event", "task", "alert"}:
                            self._history.append(envelope)
                        await target.send(envelope.as_dict())
        except (WebSocketTransportError, asyncio.CancelledError):
            return

    async def disconnect(self, device_id: str) -> None:
        server = self._connections.pop(device_id, None)
        self._handshaken.discard(device_id)
        task = self._tasks.pop(device_id, None)
        if task:
            task.cancel()
        if server:
            await server.close()

    async def close(self) -> None:
        self._closed = True
        for device_id in list(self._connections):
            await self.disconnect(device_id)


class RealtimeClient:
    def __init__(
        self,
        *,
        device: DeviceV1,
        gateway: FixtureGateway,
        session_id: str,
        offline_queue: OfflineQueue | None = None,
        auto_ack: bool = True,
    ) -> None:
        self.device = device
        self.gateway = gateway
        self.session_id = session_id
        self.offline_queue = offline_queue
        self.auto_ack = auto_ack
        self.cursor = 0
        self._endpoint: WebSocketEndpoint | None = None
        self.connected = False

    async def connect(self, *, resume_cursor: int | None = None) -> CentralEnvelopeV1:
        self._endpoint = await self.gateway.connect(self.device.device_id)
        hello = CentralEnvelopeV1.create(
            message_type="hello",
            sender_device_id=self.device.device_id,
            recipient_device_id=self.gateway.device_id,
            session_id=self.session_id,
            cursor=self.cursor if resume_cursor is None else resume_cursor,
            payload={
                "device": self.device.as_dict(),
                "resume_cursor": self.cursor if resume_cursor is None else resume_cursor,
                "can_execute": False,
            },
        )
        await self._endpoint.send(hello.as_dict())
        welcome = CentralEnvelopeV1.from_mapping(await self._endpoint.recv())
        self.connected = True
        if welcome.message_type != "welcome":
            raise WebSocketTransportError("gateway did not return WELCOME")
        return welcome

    async def disconnect(self) -> None:
        self.connected = False
        await self.gateway.disconnect(self.device.device_id)
        self._endpoint = None

    async def publish(self, envelope: CentralEnvelopeV1) -> bool:
        if not self.connected or self._endpoint is None:
            if self.offline_queue is None:
                return False
            self.offline_queue.enqueue(envelope, destination=envelope.recipient_device_id)
            return False
        try:
            await self._endpoint.send(envelope.as_dict())
            return True
        except WebSocketTransportError:
            self.connected = False
            if self.offline_queue is not None:
                self.offline_queue.enqueue(envelope, destination=envelope.recipient_device_id)
            return False

    async def publish_payload(
        self,
        *,
        message_type: str,
        recipient_device_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        cursor: int = 0,
    ) -> CentralEnvelopeV1:
        envelope = CentralEnvelopeV1.create(
            message_type=message_type,
            sender_device_id=self.device.device_id,
            recipient_device_id=recipient_device_id,
            session_id=self.session_id,
            payload=payload,
            idempotency_key=idempotency_key,
            cursor=cursor,
        )
        await self.publish(envelope)
        return envelope

    async def receive_once(self) -> CentralEnvelopeV1:
        if not self.connected or self._endpoint is None:
            raise WebSocketTransportError("client is offline")
        envelope = CentralEnvelopeV1.from_mapping(await self._endpoint.recv())
        self.cursor = max(self.cursor, envelope.cursor)
        if envelope.message_type == "ack" and self.offline_queue is not None:
            acknowledged = envelope.payload.get("ack_message_id")
            if isinstance(acknowledged, str):
                self.offline_queue.acknowledge(acknowledged)
        elif self.auto_ack and envelope.message_type in {"event", "task", "alert"}:
            await self.publish_payload(
                message_type="ack",
                recipient_device_id=envelope.sender_device_id,
                payload={"ack_message_id": envelope.message_id, "can_execute": False},
                idempotency_key=f"idem_ack_{envelope.message_id}",
                cursor=envelope.cursor,
            )
        return envelope

    async def flush(self, *, limit: int = 50) -> int:
        if not self.connected or self._endpoint is None or self.offline_queue is None:
            return 0
        claimed = self.offline_queue.claim(limit=limit)
        sent = 0
        for row in claimed:
            envelope = row["payload"]
            try:
                await self._endpoint.send(envelope.as_dict())
                sent += 1
            except WebSocketTransportError:
                self.connected = False
                self.offline_queue.fail(envelope.message_id, error_code="WEBSOCKET_CLOSED")
                break
        return sent
