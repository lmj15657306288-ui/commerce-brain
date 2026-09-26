"""SQLite at-least-once queue for bounded Central Brain envelopes."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from .contracts import CentralEnvelopeV1


class OfflineQueueError(RuntimeError):
    """Raised when an offline queue operation is invalid."""


class OfflineQueue:
    def __init__(
        self,
        path: str | Path,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.0,
    ) -> None:
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise OfflineQueueError("max_attempts must be positive")
        self.path = str(path)
        self.max_attempts = max_attempts
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS central_offline_queue (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL,
                destination TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                next_attempt_at REAL NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL CHECK (state IN ('pending', 'in_flight', 'acked', 'dead_letter')),
                last_error_code TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_central_queue_due ON central_offline_queue (state, next_attempt_at, queue_id)"
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def enqueue(self, envelope: CentralEnvelopeV1 | Mapping[str, Any], *, destination: str) -> int:
        parsed = envelope if isinstance(envelope, CentralEnvelopeV1) else CentralEnvelopeV1.from_mapping(envelope)
        destination = str(destination or parsed.recipient_device_id)
        if not destination:
            raise OfflineQueueError("destination is required")
        payload = json.dumps(parsed.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        now = time.time()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO central_offline_queue
                (message_id, idempotency_key, destination, payload_json, created_at, next_attempt_at, state)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """,
                (parsed.message_id, parsed.idempotency_key, destination, payload, now, now),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT queue_id FROM central_offline_queue WHERE message_id = ?", (parsed.message_id,)
            ).fetchone()
            if row is None:
                raise OfflineQueueError("queued message could not be read back")
            return int(row["queue_id"])

    def claim(self, *, limit: int = 50, now: float | None = None) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise OfflineQueueError("limit is outside the allowed range")
        now = time.time() if now is None else float(now)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM central_offline_queue
                WHERE state = 'pending' AND next_attempt_at <= ?
                ORDER BY queue_id LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            ids = [int(row["queue_id"]) for row in rows]
            if ids:
                self._connection.executemany(
                    "UPDATE central_offline_queue SET state = 'in_flight' WHERE queue_id = ?",
                    [(queue_id,) for queue_id in ids],
                )
                self._connection.commit()
            return [self._row(row) for row in rows]

    def acknowledge(self, message_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE central_offline_queue SET state = 'acked', last_error_code = '' WHERE message_id = ?",
                (message_id,),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def fail(self, message_id: str, *, error_code: str, now: float | None = None) -> str:
        now = time.time() if now is None else float(now)
        with self._lock:
            row = self._connection.execute(
                "SELECT attempt_count FROM central_offline_queue WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row is None:
                raise OfflineQueueError("message is not queued")
            attempts = int(row["attempt_count"]) + 1
            if attempts >= self.max_attempts:
                state = "dead_letter"
                next_attempt = now
            else:
                state = "pending"
                next_attempt = now + self.base_delay_seconds * (2 ** (attempts - 1))
            self._connection.execute(
                """
                UPDATE central_offline_queue
                SET attempt_count = ?, state = ?, next_attempt_at = ?, last_error_code = ?
                WHERE message_id = ?
                """,
                (attempts, state, next_attempt, str(error_code)[:80], message_id),
            )
            self._connection.commit()
            return state

    def recover_in_flight(self) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE central_offline_queue SET state = 'pending' WHERE state = 'in_flight'"
            )
            self._connection.commit()
            return cursor.rowcount

    def list(self, *, state: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if state is None:
                rows = self._connection.execute("SELECT * FROM central_offline_queue ORDER BY queue_id").fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM central_offline_queue WHERE state = ? ORDER BY queue_id", (state,)
                ).fetchall()
            return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = CentralEnvelopeV1.from_mapping(json.loads(value.pop("payload_json")))
        return value
