"""Backend-neutral persistence ports and local SQLite implementation.

The adapter stores versioned contract dictionaries. Business code talks to this
port rather than importing sqlite3 directly, so PostgreSQL can replace SQLite
without changing the Context Registry contract.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol


class PersistenceError(RuntimeError):
    """Base error for the persistence boundary."""


class PersistenceConflict(PersistenceError):
    """Raised when a unique identity maps to incompatible data."""


class PersistenceAdapter(Protocol):
    @property
    def schema_version(self) -> int: ...

    def get_record(self, collection: str, record_id: str) -> dict[str, Any] | None: ...

    def list_records(
        self,
        collection: str,
        *,
        filters: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def save_record(
        self,
        collection: str,
        record_id: str,
        value: Mapping[str, Any],
        *,
        indexes: Mapping[str, str | None] | None = None,
        upsert: bool = True,
    ) -> None: ...

    def delete_record(self, collection: str, record_id: str) -> None: ...

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
    ) -> None: ...

    def resolve_external_mapping(
        self,
        *,
        source: str,
        entity_type: str,
        external_id: str,
        shop_id: str | None,
        channel_id: str | None,
    ) -> dict[str, Any] | None: ...

    @contextmanager
    def transaction(self) -> Iterator["PersistenceAdapter"]: ...

    def close(self) -> None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_collection(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise PersistenceError("collection must be bounded text")
    return value


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise PersistenceError("record_id must be bounded text")
    return value


_INDEX_COLUMNS = (
    "organization_id",
    "brand_id",
    "category_id",
    "shop_id",
    "channel_id",
    "product_id",
    "sku_id",
    "actor_id",
    "listing_id",
    "master_product_id",
)


class SQLitePersistenceAdapter:
    """Small transactional SQLite adapter for local foundation work."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self._connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    @property
    def schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT MAX(version) AS version FROM schema_migrations"
        ).fetchone()
        return int(row["version"] or 0)

    def _migrate(self) -> None:
        with self.transaction():
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            if self.schema_version < 1:
                self._connection.executescript(
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
                        record_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (collection, record_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_registry_organization
                        ON registry_records (organization_id);
                    CREATE INDEX IF NOT EXISTS idx_registry_shop
                        ON registry_records (shop_id);
                    CREATE INDEX IF NOT EXISTS idx_registry_category
                        ON registry_records (category_id);
                    CREATE INDEX IF NOT EXISTS idx_registry_actor
                        ON registry_records (actor_id);
                    CREATE INDEX IF NOT EXISTS idx_registry_listing
                        ON registry_records (listing_id);
                    CREATE INDEX IF NOT EXISTS idx_registry_master_product
                        ON registry_records (master_product_id);

                    CREATE TABLE IF NOT EXISTS external_ref_mappings (
                        source TEXT NOT NULL,
                        entity_type TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        shop_scope TEXT NOT NULL DEFAULT '',
                        channel_scope TEXT NOT NULL DEFAULT '',
                        internal_entity_type TEXT NOT NULL,
                        internal_entity_id TEXT NOT NULL,
                        record_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (
                            source, entity_type, external_id, shop_scope, channel_scope
                        )
                    );

                    CREATE INDEX IF NOT EXISTS idx_external_lookup
                        ON external_ref_mappings (source, entity_type, external_id);
                    CREATE INDEX IF NOT EXISTS idx_external_internal
                        ON external_ref_mappings (internal_entity_type, internal_entity_id);
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, _now()),
                )

    @contextmanager
    def transaction(self) -> Iterator["SQLitePersistenceAdapter"]:
        with self._lock:
            outer = self._transaction_depth == 0
            if outer:
                self._connection.execute("BEGIN")
            self._transaction_depth += 1
            try:
                yield self
            except Exception:
                if outer:
                    self._connection.rollback()
                raise
            else:
                if outer:
                    self._connection.commit()
            finally:
                self._transaction_depth -= 1

    def _run_in_transaction(self, operation):
        if self._transaction_depth:
            return operation()
        with self.transaction():
            return operation()

    def get_record(self, collection: str, record_id: str) -> dict[str, Any] | None:
        collection = _safe_collection(collection)
        record_id = _safe_id(record_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT record_json FROM registry_records WHERE collection = ? AND record_id = ?",
                (collection, record_id),
            ).fetchone()
        return None if row is None else json.loads(row["record_json"])

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
        clauses = ["collection = ?"]
        params: list[str | None] = [collection]
        for key, value in filters.items():
            clauses.append(f"{key} = ?")
            params.append(value)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT record_json FROM registry_records WHERE {' AND '.join(clauses)} ORDER BY record_id",
                params,
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

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
        indexes = dict(indexes or {})
        unknown = set(indexes) - set(_INDEX_COLUMNS)
        if unknown:
            raise PersistenceError(f"unsupported index columns: {sorted(unknown)}")
        encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        columns = {
            key: indexes.get(key)
            for key in _INDEX_COLUMNS
        }
        now = _now()

        def operation() -> None:
            if upsert:
                self._connection.execute(
                    """
                    INSERT INTO registry_records (
                        collection, record_id, organization_id, brand_id, category_id,
                        shop_id, channel_id, product_id, sku_id, actor_id, listing_id,
                        master_product_id, record_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection, record_id) DO UPDATE SET
                        organization_id = excluded.organization_id,
                        brand_id = excluded.brand_id,
                        category_id = excluded.category_id,
                        shop_id = excluded.shop_id,
                        channel_id = excluded.channel_id,
                        product_id = excluded.product_id,
                        sku_id = excluded.sku_id,
                        actor_id = excluded.actor_id,
                        listing_id = excluded.listing_id,
                        master_product_id = excluded.master_product_id,
                        record_json = excluded.record_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        collection,
                        record_id,
                        columns["organization_id"],
                        columns["brand_id"],
                        columns["category_id"],
                        columns["shop_id"],
                        columns["channel_id"],
                        columns["product_id"],
                        columns["sku_id"],
                        columns["actor_id"],
                        columns["listing_id"],
                        columns["master_product_id"],
                        encoded,
                        now,
                        now,
                    ),
                )
            else:
                try:
                    self._connection.execute(
                        """
                        INSERT INTO registry_records (
                            collection, record_id, organization_id, brand_id, category_id,
                            shop_id, channel_id, product_id, sku_id, actor_id, listing_id,
                            master_product_id, record_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            collection,
                            record_id,
                            columns["organization_id"],
                            columns["brand_id"],
                            columns["category_id"],
                            columns["shop_id"],
                            columns["channel_id"],
                            columns["product_id"],
                            columns["sku_id"],
                            columns["actor_id"],
                            columns["listing_id"],
                            columns["master_product_id"],
                            encoded,
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise PersistenceConflict("record identity already exists") from exc

        with self._lock:
            self._run_in_transaction(operation)

    def delete_record(self, collection: str, record_id: str) -> None:
        collection = _safe_collection(collection)
        record_id = _safe_id(record_id)

        def operation() -> None:
            self._connection.execute(
                "DELETE FROM registry_records WHERE collection = ? AND record_id = ?",
                (collection, record_id),
            )

        with self._lock:
            self._run_in_transaction(operation)

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
        encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        now = _now()

        def operation() -> None:
            existing = self._connection.execute(
                """
                SELECT internal_entity_type, internal_entity_id
                FROM external_ref_mappings
                WHERE source = ? AND entity_type = ? AND external_id = ?
                  AND shop_scope = ? AND channel_scope = ?
                """,
                key,
            ).fetchone()
            if existing is not None:
                same = (
                    existing["internal_entity_type"] == internal_entity_type
                    and existing["internal_entity_id"] == internal_entity_id
                )
                if not same or not upsert:
                    raise PersistenceConflict("external reference already maps to another entity")
                self._connection.execute(
                    """
                    UPDATE external_ref_mappings
                    SET record_json = ?, updated_at = ?
                    WHERE source = ? AND entity_type = ? AND external_id = ?
                      AND shop_scope = ? AND channel_scope = ?
                    """,
                    (encoded, now, *key),
                )
                return
            self._connection.execute(
                """
                INSERT INTO external_ref_mappings (
                    source, entity_type, external_id, shop_scope, channel_scope,
                    internal_entity_type, internal_entity_id, record_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*key, internal_entity_type, internal_entity_id, encoded, now, now),
            )

        with self._lock:
            self._run_in_transaction(operation)

    def resolve_external_mapping(
        self,
        *,
        source: str,
        entity_type: str,
        external_id: str,
        shop_id: str | None,
        channel_id: str | None,
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT internal_entity_type, internal_entity_id, record_json
            FROM external_ref_mappings
            WHERE source = ? AND entity_type = ? AND external_id = ?
              AND shop_scope = ? AND channel_scope = ?
            """,
            (source, entity_type, external_id, shop_id or "", channel_id or ""),
        ).fetchone()
        if row is None:
            return None
        result = json.loads(row["record_json"])
        result.update(
            {
                "internal_entity_type": row["internal_entity_type"],
                "internal_entity_id": row["internal_entity_id"],
            }
        )
        return result

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class InMemoryPersistenceAdapter:
    """Deterministic adapter useful for contract-only tests."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._external: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._snapshots: list[tuple[dict, dict]] = []

    @property
    def schema_version(self) -> int:
        return 1

    @contextmanager
    def transaction(self) -> Iterator["InMemoryPersistenceAdapter"]:
        with self._lock:
            snapshot = (copy.deepcopy(self._records), copy.deepcopy(self._external))
            self._snapshots.append(snapshot)
            try:
                yield self
            except Exception:
                self._records, self._external = snapshot
                raise
            finally:
                self._snapshots.pop()

    def get_record(self, collection: str, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._records.get((_safe_collection(collection), _safe_id(record_id)))
            return None if value is None else copy.deepcopy(value["value"])

    def list_records(self, collection: str, *, filters: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        output = []
        for item in self._records.values():
            if item["collection"] != _safe_collection(collection):
                continue
            if all(item["indexes"].get(key) == value for key, value in filters.items()):
                output.append(copy.deepcopy(item["value"]))
        return sorted(output, key=lambda value: json.dumps(value, sort_keys=True))

    def save_record(self, collection: str, record_id: str, value: Mapping[str, Any], *, indexes: Mapping[str, str | None] | None = None, upsert: bool = True) -> None:
        key = (_safe_collection(collection), _safe_id(record_id))
        with self._lock:
            if not upsert and key in self._records:
                raise PersistenceConflict("record identity already exists")
            self._records[key] = {
                "collection": key[0],
                "indexes": dict(indexes or {}),
                "value": copy.deepcopy(dict(value)),
            }

    def delete_record(self, collection: str, record_id: str) -> None:
        with self._lock:
            self._records.pop((_safe_collection(collection), _safe_id(record_id)), None)

    def save_external_mapping(self, *, source: str, entity_type: str, external_id: str, shop_id: str | None, channel_id: str | None, internal_entity_type: str, internal_entity_id: str, value: Mapping[str, Any], upsert: bool = True) -> None:
        key = (source, entity_type, external_id, shop_id or "", channel_id or "")
        with self._lock:
            existing = self._external.get(key)
            identity = (internal_entity_type, internal_entity_id)
            if existing is not None and (existing["identity"] != identity or not upsert):
                raise PersistenceConflict("external reference already maps to another entity")
            self._external[key] = {
                "identity": identity,
                "value": copy.deepcopy(dict(value)),
            }

    def resolve_external_mapping(self, *, source: str, entity_type: str, external_id: str, shop_id: str | None, channel_id: str | None) -> dict[str, Any] | None:
        item = self._external.get((source, entity_type, external_id, shop_id or "", channel_id or ""))
        if item is None:
            return None
        result = copy.deepcopy(item["value"])
        result["internal_entity_type"], result["internal_entity_id"] = item["identity"]
        return result

    def close(self) -> None:
        return None
