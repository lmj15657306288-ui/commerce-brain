"""Small heartbeat loop helper for the outbound worker process."""

from __future__ import annotations

import threading
from collections.abc import Callable


class HeartbeatLoop:
    def __init__(self, callback: Callable[[], None], *, interval_seconds: float = 20.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.callback = callback
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self.callback()
            self._stop.wait(self.interval_seconds)
