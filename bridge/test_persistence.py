from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.persistence import PersistenceConflict, SQLitePersistenceAdapter


class PersistenceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "registry.sqlite3"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_schema_and_sqlite_pragmas(self):
        adapter = SQLitePersistenceAdapter(self.path)
        try:
            self.assertEqual(1, adapter.schema_version)
            journal = adapter._connection.execute("PRAGMA journal_mode").fetchone()[0]
            foreign_keys = adapter._connection.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual("wal", journal.lower())
            self.assertEqual(1, foreign_keys)
        finally:
            adapter.close()

    def test_insert_read_upsert_and_restart_persistence(self):
        adapter = SQLitePersistenceAdapter(self.path)
        adapter.save_record(
            "shops",
            "shop_a",
            {"schema_version": 1, "name": "before"},
            indexes={"organization_id": "org_demo", "shop_id": "shop_a"},
        )
        adapter.save_record(
            "shops",
            "shop_a",
            {"schema_version": 1, "name": "after"},
            indexes={"organization_id": "org_demo", "shop_id": "shop_a"},
        )
        self.assertEqual("after", adapter.get_record("shops", "shop_a")["name"])
        adapter.close()
        reopened = SQLitePersistenceAdapter(self.path)
        try:
            self.assertEqual("after", reopened.get_record("shops", "shop_a")["name"])
        finally:
            reopened.close()

    def test_non_upsert_duplicate_is_a_unique_violation(self):
        adapter = SQLitePersistenceAdapter(self.path)
        try:
            adapter.save_record("shops", "shop_a", {"name": "one"}, upsert=False)
            with self.assertRaises(PersistenceConflict):
                adapter.save_record("shops", "shop_a", {"name": "two"}, upsert=False)
        finally:
            adapter.close()

    def test_transaction_rolls_back_all_records(self):
        adapter = SQLitePersistenceAdapter(self.path)
        try:
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with adapter.transaction():
                    adapter.save_record("shops", "shop_a", {"name": "one"})
                    adapter.save_record("shops", "shop_b", {"name": "two"})
                    raise RuntimeError("rollback")
            self.assertIsNone(adapter.get_record("shops", "shop_a"))
            self.assertIsNone(adapter.get_record("shops", "shop_b"))
        finally:
            adapter.close()

    def test_external_scope_key_allows_same_external_id_and_is_idempotent(self):
        adapter = SQLitePersistenceAdapter(self.path)
        try:
            args = {
                "source": "platform",
                "entity_type": "product",
                "external_id": "same",
                "shop_id": "shop_a",
                "channel_id": None,
                "internal_entity_type": "master_product",
                "internal_entity_id": "master_product_a",
                "value": {"external_id": "same"},
            }
            adapter.save_external_mapping(**args)
            adapter.save_external_mapping(**args)
            args["shop_id"] = "shop_b"
            args["internal_entity_id"] = "master_product_b"
            adapter.save_external_mapping(**args)
            self.assertEqual(
                "master_product_b",
                adapter.resolve_external_mapping(
                    source="platform",
                    entity_type="product",
                    external_id="same",
                    shop_id="shop_b",
                    channel_id=None,
                )["internal_entity_id"],
            )
        finally:
            adapter.close()

    def test_conflicting_external_mapping_rolls_back(self):
        adapter = SQLitePersistenceAdapter(self.path)
        try:
            args = {
                "source": "platform",
                "entity_type": "product",
                "external_id": "same",
                "shop_id": "shop_a",
                "channel_id": None,
                "internal_entity_type": "master_product",
                "internal_entity_id": "master_product_a",
                "value": {"external_id": "same"},
            }
            adapter.save_external_mapping(**args)
            with self.assertRaises(PersistenceConflict):
                adapter.save_external_mapping(**{**args, "internal_entity_id": "master_product_b"})
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
