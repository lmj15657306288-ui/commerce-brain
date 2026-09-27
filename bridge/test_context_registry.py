from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.context_registry import ContextRegistry, RegistryConflict, RegistryNotFound
from core.contracts import (
    ActorV1,
    BrainWorkerV1,
    BrandRecordV1,
    CategoryRecordV1,
    ChannelRecordV1,
    ContractValidationError,
    DeviceSessionV1,
    ExternalRefV1,
    MasterProductRefV1,
    OrganizationRecordV1,
    RoleAssignmentV1,
    ScopeGrantV1,
    ScopeV1,
    ShopCategoryMembershipV1,
    ShopRecordV1,
    StoreListingRefV1,
)
from core.persistence import SQLitePersistenceAdapter


class ContextRegistryTests(unittest.TestCase):
    NOW = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc).isoformat()

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "registry.sqlite3"
        self.adapter = SQLitePersistenceAdapter(self.db_path)
        self.registry = ContextRegistry(self.adapter)
        self.org = OrganizationRecordV1.create(
            organization_id="org_demo",
            name="Demo Organization",
            created_at=self.NOW,
            updated_at=self.NOW,
        )
        self.brand = BrandRecordV1.create(
            brand_id="brand_demo",
            organization_id="org_demo",
            name="Demo Brand",
        )
        self.category = CategoryRecordV1.create(
            category_id="category_apparel",
            organization_id="org_demo",
            name="Apparel",
        )
        self.category_food = CategoryRecordV1.create(
            category_id="category_food",
            organization_id="org_demo",
            name="Food",
        )
        self.shop_a = ShopRecordV1.create(
            shop_id="shop_a",
            organization_id="org_demo",
            brand_id="brand_demo",
            name="Shop A",
        )
        self.shop_b = ShopRecordV1.create(
            shop_id="shop_b",
            organization_id="org_demo",
            name="Shop B",
        )
        self.channel_a = ChannelRecordV1.create(
            channel_id="channel_a",
            organization_id="org_demo",
            shop_id="shop_a",
            platform="douyin",
        )
        for record in (self.org, self.brand, self.category, self.category_food, self.shop_a, self.shop_b, self.channel_a):
            self.registry.register(record)

    def tearDown(self):
        self.adapter.close()
        self.tempdir.cleanup()

    def membership(self, *, shop_id="shop_a", category_id="category_apparel", source="MANUAL", status="ACTIVE"):
        return ShopCategoryMembershipV1.create(
            membership_id=f"membership_{shop_id}_{category_id}",
            organization_id="org_demo",
            shop_id=shop_id,
            category_id=category_id,
            source=source,
            status=status,
            confidence=0.9,
            created_at=self.NOW,
            updated_at=self.NOW,
        )

    def role_and_grant(self, *, actor_id, role, scope, capabilities, effect="ALLOW", suffix="1"):
        self.registry.register(
            RoleAssignmentV1.create(
                assignment_id=f"assignment_{suffix}",
                actor_id=actor_id,
                role=role,
                scope=scope,
            )
        )
        self.registry.register(
            ScopeGrantV1.create(
                grant_id=f"grant_{suffix}",
                actor_id=actor_id,
                scope=scope,
                capabilities=capabilities,
                effect=effect,
            )
        )

    def test_records_and_master_product_store_listing_lineage(self):
        self.registry.register(self.membership())
        product = MasterProductRefV1.create(
            master_product_id="master_product_coat",
            organization_id="org_demo",
            brand_id="brand_demo",
            category_id="category_apparel",
        )
        self.registry.register(product)
        external = ExternalRefV1.create(
            source="douyin",
            entity_type="listing",
            external_id="platform_listing_1",
            shop_id="shop_a",
            channel_id="channel_a",
        )
        listing = StoreListingRefV1.create(
            listing_id="listing_a",
            master_product_id=product.master_product_id,
            shop_id="shop_a",
            channel_id="channel_a",
            external_ref=external,
        )
        self.registry.register(listing)
        self.assertEqual(product, self.registry.get_master_product(product.master_product_id))
        self.assertEqual(listing, self.registry.get_store_listing(listing.listing_id))
        self.assertEqual("store_listing", self.registry.resolve_external_ref(external)["internal_entity_type"])
        self.assertTrue(
            self.registry.scope_exists(
                ScopeV1.create(
                    organization_id="org_demo",
                    category_id="category_apparel",
                    shop_id="shop_a",
                    product_id="master_product_coat",
                )
            )
        )

    def test_owner_org_grant_can_access_child_shops(self):
        self.registry.register(self.membership(shop_id="shop_a"))
        self.registry.register(self.membership(shop_id="shop_b"))
        self.role_and_grant(
            actor_id="actor_owner",
            role="Owner",
            scope=ScopeV1.create(organization_id="org_demo"),
            capabilities=["context.read"],
            suffix="owner",
        )
        self.assertTrue(
            self.registry.actor_can_access(
                "actor_owner",
                ScopeV1.create(organization_id="org_demo", shop_id="shop_a"),
                "context.read",
            )
        )
        self.assertTrue(
            self.registry.actor_can_access(
                "actor_owner",
                ScopeV1.create(organization_id="org_demo", shop_id="shop_b"),
                "context.read",
            )
        )

    def test_shop_operator_cannot_access_other_shop(self):
        self.role_and_grant(
            actor_id="actor_operator",
            role="Operator",
            scope=ScopeV1.create(organization_id="org_demo", shop_id="shop_a"),
            capabilities=["context.read"],
            suffix="operator",
        )
        self.assertTrue(
            self.registry.actor_can_access(
                "actor_operator",
                ScopeV1.create(organization_id="org_demo", shop_id="shop_a"),
                "context.read",
            )
        )
        self.assertFalse(
            self.registry.actor_can_access(
                "actor_operator",
                ScopeV1.create(organization_id="org_demo", shop_id="shop_b"),
                "context.read",
            )
        )

    def test_category_grant_requires_formal_membership(self):
        category_scope = ScopeV1.create(organization_id="org_demo", category_id="category_apparel")
        self.role_and_grant(
            actor_id="actor_category",
            role="Operator",
            scope=category_scope,
            capabilities=["context.read"],
            suffix="category",
        )
        self.assertFalse(
            self.registry.actor_can_access(
                "actor_category",
                ScopeV1.create(
                    organization_id="org_demo",
                    category_id="category_apparel",
                    shop_id="shop_a",
                ),
                "context.read",
            )
        )
        self.registry.register(self.membership())
        self.assertTrue(
            self.registry.actor_can_access(
                "actor_category",
                ScopeV1.create(
                    organization_id="org_demo",
                    category_id="category_apparel",
                    shop_id="shop_a",
                ),
                "context.read",
            )
        )

    def test_deny_precedes_allow_and_missing_grant_denies(self):
        scope = ScopeV1.create(organization_id="org_demo", shop_id="shop_a")
        self.role_and_grant(
            actor_id="actor_denied",
            role="Owner",
            scope=ScopeV1.create(organization_id="org_demo"),
            capabilities=["context.read"],
            suffix="allow",
        )
        self.role_and_grant(
            actor_id="actor_denied",
            role="Owner",
            scope=scope,
            capabilities=["context.read"],
            effect="DENY",
            suffix="deny",
        )
        self.assertFalse(self.registry.actor_can_access("actor_denied", scope, "context.read"))
        self.assertFalse(self.registry.actor_can_access("actor_unknown", scope, "context.read"))

    def test_capability_and_scope_are_both_required(self):
        scope_a = ScopeV1.create(organization_id="org_demo", shop_id="shop_a")
        scope_b = ScopeV1.create(organization_id="org_demo", shop_id="shop_b")
        self.role_and_grant(
            actor_id="actor_viewer",
            role="Viewer",
            scope=ScopeV1.create(organization_id="org_demo"),
            capabilities=["task.create"],
            suffix="viewer",
        )
        self.assertFalse(self.registry.actor_can_access("actor_viewer", scope_a, "task.create"))
        self.role_and_grant(
            actor_id="actor_operator",
            role="Operator",
            scope=scope_a,
            capabilities=["context.read"],
            suffix="scope_only",
        )
        self.assertFalse(self.registry.actor_can_access("actor_operator", scope_b, "context.read"))

    def test_disabled_and_archived_context_lifecycle(self):
        disabled = ShopRecordV1.create(
            shop_id="shop_disabled",
            organization_id="org_demo",
            name="Disabled",
            status="DISABLED",
        )
        archived = ShopRecordV1.create(
            shop_id="shop_archived",
            organization_id="org_demo",
            name="Archived",
            status="ARCHIVED",
        )
        self.registry.register(disabled)
        self.registry.register(archived)
        disabled_scope = ScopeV1.create(organization_id="org_demo", shop_id="shop_disabled")
        archived_scope = ScopeV1.create(organization_id="org_demo", shop_id="shop_archived")
        self.assertFalse(self.registry.validate_scope(disabled_scope))
        self.assertFalse(self.registry.validate_scope(archived_scope))
        self.assertTrue(self.registry.scope_exists(archived_scope, include_archived=True))

    def test_inferred_membership_is_proposed_not_formal(self):
        inferred = self.membership(source="INFERRED", status=None)
        self.registry.register(inferred)
        self.assertEqual([], self.registry.get_shop_categories("shop_a"))
        self.assertEqual([inferred], self.registry.get_shop_categories("shop_a", include_proposed=True))
        with self.assertRaises(ContractValidationError):
            self.registry.register(self.membership(source="INFERRED", status="ACTIVE"))

    def test_external_reference_scope_is_not_global_unique(self):
        first = ExternalRefV1.create(
            source="platform_a",
            entity_type="product",
            external_id="same",
            shop_id="shop_a",
        )
        second = ExternalRefV1.create(
            source="platform_a",
            entity_type="product",
            external_id="same",
            shop_id="shop_b",
        )
        self.registry.register_external_ref(
            first,
            internal_entity_type="master_product",
            internal_entity_id="master_product_a",
        )
        self.registry.register_external_ref(
            second,
            internal_entity_type="master_product",
            internal_entity_id="master_product_b",
        )
        with self.assertRaises(RegistryConflict):
            self.registry.register_external_ref(
                first,
                internal_entity_type="master_product",
                internal_entity_id="master_product_other",
            )

    def test_actor_device_and_worker_identity_are_registry_records(self):
        self.registry.register(
            ActorV1.create(
                actor_id="actor_edge",
                actor_type="EDGE",
                role="Operator",
                scope=ScopeV1.create(organization_id="org_demo"),
            )
        )
        device = DeviceSessionV1.create(
            device_id="device_edge",
            device_type="BROWSER_EXTENSION",
            organization_id="org_demo",
            user_id="actor_edge",
        )
        worker = BrainWorkerV1.create(worker_id="worker_mac", worker_type="MAC")
        self.registry.register(device)
        self.registry.register(worker)
        self.assertEqual("actor_edge", self.registry.get_actor("actor_edge").actor_id)
        self.assertEqual(device, self.registry.get_device("device_edge"))
        self.assertEqual(worker, self.registry.get_worker("worker_mac"))

    def test_missing_lineage_is_rejected(self):
        with self.assertRaises(RegistryNotFound):
            self.registry.register(
                ShopRecordV1.create(
                    shop_id="shop_orphan",
                    organization_id="org_missing",
                    name="Orphan",
                )
            )


if __name__ == "__main__":
    unittest.main()
