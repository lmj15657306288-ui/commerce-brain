"""PostgreSQL persistence adapter for the production Control Plane.

The adapter deliberately exposes the same record-oriented port as the local
SQLite/InMemory implementations. PostgreSQL-specific SQL stays here; service,
registry, and API code do not import a database driver.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from psycopg import sql
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb

from .persistence import (
    _INDEX_COLUMNS,
    PersistenceConflict,
    PersistenceError,
    _safe_collection,
    _safe_id,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _same_json(left: Any, right: Any) -> bool:
    return json.dumps(
        _json_value(left),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) == json.dumps(
        _json_value(right),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class PostgresPersistenceAdapter:
    """Transactional PostgreSQL implementation of the shared persistence port."""

    MIGRATION_VERSION = 1

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
        open_timeout: float = 10.0,
    ) -> None:
        if not dsn or "\n" in dsn:
            raise ValueError("PostgreSQL DSN is required and must be one line")
        if not 1 <= min_size <= max_size:
            raise ValueError("invalid PostgreSQL pool bounds")
        self.dsn = dsn
        self._local = threading.local()
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=open_timeout,
        )
        self.pool.open(wait=True, timeout=open_timeout)
        self._migrate()

    @property
    def schema_version(self) -> int:
        with self._connection_scope() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            return int(row[0] or 0)

    def latest_event_cursor(self) -> int:
        with self._connection_scope() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(cursor), 0) FROM cp_events"
            ).fetchone()
            return int(row[0] or 0)

    def health_check(self) -> bool:
        try:
            with self._connection_scope() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    @contextmanager
    def _connection_scope(self):
        current = getattr(self._local, "connection", None)
        if current is not None:
            yield current
            return
        with self.pool.connection() as connection:
            with connection.transaction():
                yield connection

    @contextmanager
    def transaction(self) -> Iterator["PostgresPersistenceAdapter"]:
        current = getattr(self._local, "connection", None)
        if current is not None:
            yield self
            return
        with self.pool.connection() as connection:
            self._local.connection = connection
            try:
                with connection.transaction():
                    yield self
            finally:
                self._local.connection = None

    def lock_transaction_key(self, namespace: str, key: str) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            raise PersistenceError("transaction lock requires an active transaction")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{namespace}:{key}",),
        )

    def _migrate(self) -> None:
        with self.transaction():
            connection = self._local.connection
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            current = self.schema_version
            if current < 1:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS registry_records (
                        collection TEXT NOT NULL,
                        record_id TEXT NOT NULL,
                        organization_id TEXT,
                        brand_id TEXT,
                        category_id TEXT,
                        shop_id TEXT,
                        channel_id TEXT,
                        product_id TEXT,
                        sku_id TEXT,
                        actor_id TEXT,
                        listing_id TEXT,
                        master_product_id TEXT,
                        record_json JSONB NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (collection, record_id)
                    )
                    """
                )
                for column in _INDEX_COLUMNS:
                    connection.execute(
                        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON registry_records ({})").format(
                            sql.Identifier(f"idx_registry_{column}"),
                            sql.Identifier(column),
                        )
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS external_ref_mappings (
                        source TEXT NOT NULL,
                        entity_type TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        shop_scope TEXT NOT NULL DEFAULT '',
                        channel_scope TEXT NOT NULL DEFAULT '',
                        internal_entity_type TEXT NOT NULL,
                        internal_entity_id TEXT NOT NULL,
                        record_json JSONB NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (
                            source, entity_type, external_id, shop_scope, channel_scope
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_external_lookup
                    ON external_ref_mappings (source, entity_type, external_id)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_external_internal
                    ON external_ref_mappings (internal_entity_type, internal_entity_id)
                    """
                )
                connection.execute(
                    """
                    CREATE SEQUENCE IF NOT EXISTS cp_event_cursor_seq
                    AS BIGINT START WITH 1 INCREMENT BY 1
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cp_events (
                        cursor BIGINT PRIMARY KEY DEFAULT nextval('cp_event_cursor_seq'),
                        event_id TEXT NOT NULL UNIQUE,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        organization_id TEXT,
                        brand_id TEXT,
                        category_id TEXT,
                        shop_id TEXT,
                        channel_id TEXT,
                        product_id TEXT,
                        sku_id TEXT,
                        occurred_at TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        event_json JSONB NOT NULL
                    )
                    """
                )
                for column in (
                    "organization_id",
                    "shop_id",
                    "category_id",
                    "occurred_at",
                    "received_at",
                ):
                    connection.execute(
                        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON cp_events ({})").format(
                            sql.Identifier(f"idx_cp_events_{column}"),
                            sql.Identifier(column),
                        )
                    )
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (%s, %s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (1, _now()),
                )

    def get_record(self, collection: str, record_id: str) -> dict[str, Any] | None:
        collection = _safe_collection(collection)
        record_id = _safe_id(record_id)
        with self._connection_scope() as connection:
            if collection == "cp_events":
                row = connection.execute(
                    """
                    SELECT cursor, event_json
                    FROM cp_events
                    WHERE event_id = %s
                    """,
                    (record_id,),
                ).fetchone()
                if row is None:
                    return None
                return {"cursor": int(row[0]), "event": _json_value(row[1])}
            row = connection.execute(
                """
                SELECT record_json
                FROM registry_records
                WHERE collection = %s AND record_id = %s
                """,
                (collection, record_id),
            ).fetchone()
            return None if row is None else _json_value(row[0])

    def list_records(
        self,
        collection: str,
        *,
        filters: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        collection = _safe_collection(collection)
        filters = dict(filters or {})
        unknown = set(filters) - set(_INDEX_COLUMNS)
        if unknown:
            raise PersistenceError(f"unsupported filter columns: {sorted(unknown)}")
        with self._connection_scope() as connection:
            if collection == "cp_events":
                rows = connection.execute(
                    """
                    SELECT cursor, event_json
                    FROM cp_events
                    ORDER BY cursor
                    """
                ).fetchall()
                return [
                    {"cursor": int(row[0]), "event": _json_value(row[1])}
                    for row in rows
                ]
            clauses = [sql.SQL("collection = %s")]
            params: list[str | None] = [collection]
            for key, value in filters.items():
                clauses.append(sql.SQL("{} = %s").format(sql.Identifier(key)))
                params.append(value)
            query = sql.SQL(
                "SELECT record_json FROM registry_records WHERE {} ORDER BY record_id"
            ).format(sql.SQL(" AND ").join(clauses))
            rows = connection.execute(query, params).fetchall()
            return [_json_value(row[0]) for row in rows]

    def save_record(
        self,
        collection: str,
        record_id: str,
        value: Mapping[str, Any],
        *,
        indexes: Mapping[str, str | None] | None = None,
        upsert: bool = True,
    ) -> None:
        collection = _safe_collection(collection)
        record_id = _safe_id(record_id)
        if collection == "cp_events":
            raise PersistenceError("cp_events must use append_event")
        indexes = dict(indexes or {})
        unknown = set(indexes) - set(_INDEX_COLUMNS)
        if unknown:
            raise PersistenceError(f"unsupported index columns: {sorted(unknown)}")
        index_values = [indexes.get(column) for column in _INDEX_COLUMNS]
        encoded = Jsonb(dict(value))
        columns = ["collection", "record_id", *_INDEX_COLUMNS, "record_json", "created_at", "updated_at"]
        values = [collection, record_id, *index_values, encoded, _now(), _now()]
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in values)
        column_sql = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
        updates = sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in [*_INDEX_COLUMNS, "record_json", "updated_at"]
        )
        conflict_action = (
            sql.SQL("DO UPDATE SET {}").format(updates)
            if upsert
            else sql.SQL("DO NOTHING")
        )
        query = sql.SQL(
            "INSERT INTO registry_records ({}) VALUES ({}) "
            "ON CONFLICT (collection, record_id) {} RETURNING 1"
        ).format(
            column_sql,
            placeholders,
            conflict_action,
        )
        with self._connection_scope() as connection:
            try:
                if not upsert:
                    existing = connection.execute(
                        """
                        SELECT 1 FROM registry_records
                        WHERE collection = %s AND record_id = %s
                        """,
                        (collection, record_id),
                    ).fetchone()
                    if existing is not None:
                        raise PersistenceConflict("record identity already exists")
                # A nested psycopg transaction is a savepoint when the
                # adapter is already inside its outer transaction. This keeps
                # a concurrent unique violation from poisoning the connection.
                with connection.transaction():
                    inserted = connection.execute(query, values).fetchone()
                if not upsert and inserted is None:
                    raise PersistenceConflict("record identity already exists")
            except UniqueViolation as exc:
                raise PersistenceConflict("record identity already exists") from exc

    def delete_record(self, collection: str, record_id: str) -> None:
        with self._connection_scope() as connection:
            connection.execute(
                "DELETE FROM registry_records WHERE collection = %s AND record_id = %s",
                (_safe_collection(collection), _safe_id(record_id)),
            )

    def save_external_mapping(
        self,
        *,
        source: str,
        entity_type: str,
        external_id: str,
        shop_id: str | None,
        channel_id: str | None,
        internal_entity_type: str,
        internal_entity_id: str,
        value: Mapping[str, Any],
        upsert: bool = True,
    ) -> None:
        key = (source, entity_type, external_id, shop_id or "", channel_id or "")
        encoded = Jsonb(dict(value))
        with self._connection_scope() as connection:
            existing = connection.execute(
                """
                SELECT internal_entity_type, internal_entity_id
                FROM external_ref_mappings
                WHERE source = %s AND entity_type = %s AND external_id = %s
                  AND shop_scope = %s AND channel_scope = %s
                """,
                key,
            ).fetchone()
            if existing is not None:
                same = existing[0] == internal_entity_type and existing[1] == internal_entity_id
                if not same or not upsert:
                    raise PersistenceConflict("external reference already maps to another entity")
                connection.execute(
                    """
                    UPDATE external_ref_mappings
                    SET record_json = %s, updated_at = %s
                    WHERE source = %s AND entity_type = %s AND external_id = %s
                      AND shop_scope = %s AND channel_scope = %s
                    """,
                    (encoded, _now(), *key),
                )
                return
            try:
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO external_ref_mappings (
                            source, entity_type, external_id, shop_scope, channel_scope,
                            internal_entity_type, internal_entity_id, record_json,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (*key, internal_entity_type, internal_entity_id, encoded, _now(), _now()),
                    )
            except UniqueViolation as exc:
                raise PersistenceConflict("external reference already exists") from exc

    def resolve_external_mapping(
        self,
        *,
        source: str,
        entity_type: str,
        external_id: str,
        shop_id: str | None,
        channel_id: str | None,
    ) -> dict[str, Any] | None:
        with self._connection_scope() as connection:
            row = connection.execute(
                """
                SELECT internal_entity_type, internal_entity_id, record_json
                FROM external_ref_mappings
                WHERE source = %s AND entity_type = %s AND external_id = %s
                  AND shop_scope = %s AND channel_scope = %s
                """,
                (source, entity_type, external_id, shop_id or "", channel_id or ""),
            ).fetchone()
            if row is None:
                return None
            result = _json_value(row[2])
            result.update(
                {
                    "internal_entity_type": row[0],
                    "internal_entity_id": row[1],
                }
            )
            return result

    def append_event(
        self,
        *,
        event: Mapping[str, Any],
        event_id: str,
        idempotency_key: str,
        scope: Mapping[str, Any],
        occurred_at: str,
        received_at: str,
    ) -> tuple[int, bool]:
        with self._connection_scope() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("commerce_brain:event_cursor",),
            )
            existing = connection.execute(
                """
                SELECT cursor, event_id, idempotency_key, event_json
                FROM cp_events
                WHERE event_id = %s OR idempotency_key = %s
                """,
                (event_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing[1] == event_id
                    and existing[2] == idempotency_key
                    and _same_json(existing[3], event)
                ):
                    return int(existing[0]), True
                raise PersistenceConflict("event identity or idempotency key conflicts")
            fields = {key: scope.get(key) for key in _INDEX_COLUMNS}
            try:
                with connection.transaction():
                    row = connection.execute(
                        """
                        INSERT INTO cp_events (
                            event_id, idempotency_key, organization_id, brand_id,
                            category_id, shop_id, channel_id, product_id, sku_id,
                            occurred_at, received_at, event_json
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        RETURNING cursor
                        """,
                        (
                            event_id,
                            idempotency_key,
                            fields["organization_id"],
                            fields["brand_id"],
                            fields["category_id"],
                            fields["shop_id"],
                            fields["channel_id"],
                            fields["product_id"],
                            fields["sku_id"],
                            occurred_at,
                            received_at,
                            Jsonb(dict(event)),
                        ),
                    ).fetchone()
                return int(row[0]), False
            except UniqueViolation:
                existing = connection.execute(
                    """
                    SELECT cursor, event_id, idempotency_key, event_json
                    FROM cp_events
                    WHERE event_id = %s OR idempotency_key = %s
                    """,
                    (event_id, idempotency_key),
                ).fetchone()
                if (
                    existing is not None
                    and existing[1] == event_id
                    and existing[2] == idempotency_key
                    and _same_json(existing[3], event)
                ):
                    return int(existing[0]), True
                raise PersistenceConflict("event identity or idempotency key conflicts")

    def list_event_records_after(
        self,
        cursor: int,
        *,
        limit: int,
        scope: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if cursor < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid event cursor or limit")
        clauses = [sql.SQL("cursor > %s")]
        params: list[Any] = [cursor]
        for field in _INDEX_COLUMNS:
            value = None if scope is None else scope.get(field)
            if value is not None:
                clauses.append(sql.SQL("{} = %s").format(sql.Identifier(field)))
                params.append(value)
        params.append(limit)
        query = sql.SQL(
            """
            SELECT cursor, event_json
            FROM cp_events
            WHERE {}
            ORDER BY cursor
            LIMIT %s
            """
        ).format(sql.SQL(" AND ").join(clauses))
        with self._connection_scope() as connection:
            rows = connection.execute(query, params).fetchall()
            return [
                {"cursor": int(row[0]), "event": _json_value(row[1])}
                for row in rows
            ]

    def close(self) -> None:
        self.pool.close()
