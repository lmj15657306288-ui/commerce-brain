"""Durable local state for the outbound Mac Brain Worker client."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock


@dataclass
class WorkerState:
    worker_id: str
    last_ack_cursor: int = 0
    last_server_time: str | None = None
    registration_idempotency_key: str | None = None


class WorkerStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def load(self, *, worker_id: str | None = None) -> WorkerState:
        with self._lock:
            if not self.path.exists():
                if not worker_id:
                    raise ValueError("worker_id is required for first state initialization")
                state = WorkerState(worker_id=worker_id)
                self.save(state)
                return state
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = WorkerState(
                worker_id=str(raw["worker_id"]),
                last_ack_cursor=int(raw.get("last_ack_cursor", 0)),
                last_server_time=raw.get("last_server_time"),
                registration_idempotency_key=raw.get("registration_idempotency_key"),
            )
            if worker_id and state.worker_id != worker_id:
                raise ValueError("persisted worker_id does not match configured worker_id")
            if state.last_ack_cursor < 0:
                raise ValueError("persisted cursor must be non-negative")
            return state

    def save(self, state: WorkerState) -> None:
        encoded = json.dumps(asdict(state), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with self._lock:
            fd, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                dir=str(self.path.parent),
                text=True,
            )
            try:
                os.chmod(temporary, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
