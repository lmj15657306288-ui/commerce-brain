"""Cursor synchronization helpers for the Mac Brain Worker."""

from __future__ import annotations

from typing import Any

from .client import ControlPlaneWorkerClient


class WorkerEventSync:
    def __init__(self, client: ControlPlaneWorkerClient) -> None:
        self.client = client

    def reconnect_and_replay(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.client.register()
        self.client.heartbeat()
        return self.client.poll_events(limit=limit)
