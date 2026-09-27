"""Scope-aware Context Registry built on the persistence port."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .contracts import (
    DEFAULT_ROLE_CAPABILITIES,
    ActorV1,
    BrainWorkerV1,
    BrandRecordV1,
    CategoryRecordV1,
    ChannelRecordV1,
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
    _internal_id,
    _safe_ref,
)
from .persistence import (
    InMemoryPersistenceAdapter,
    PersistenceAdapter,
    PersistenceConflict,
)


class RegistryError(RuntimeError):
    """Base error for Context Registry operations."""


class RegistryNotFound(RegistryError):
    """Raised when required lineage or an entity is missing."""


class RegistryConflict(RegistryError):
    """Raised when a registry identity cannot be safely upserted."""


class ContextRegistryRead(Protocol):
    def get_organization(self, organization_id: str) -> OrganizationRecordV1 | None: ...

    def list_shops(self, organization_id: str) -> list[ShopRecordV1]: ...

    def get_brand(self, brand_id: str) -> BrandRecordV1 | None: ...

    def get_category(self, category_id: str) -> CategoryRecordV1 | None: ...

    def get_shop(self, shop_id: str) -> ShopRecordV1 | None: ...

    def get_channel(self, channel_id: str) -> ChannelRecordV1 | None: ...

    def get_shop_categories(
        self,
        shop_id: str,
        *,
        include_proposed: bool = False,
        include_disabled: bool = False,
    ) -> list[ShopCategoryMembershipV1]: ...

    def resolve_external_ref(self, external_ref: ExternalRefV1) -> dict[str, Any] | None: ...

    def validate_scope(self, scope: ScopeV1) -> bool: ...

    def scope_exists(self, scope: ScopeV1, *, include_archived: bool = False) -> bool: ...

    def actor_can_access(
        self,
        actor_id: str,
        scope: ScopeV1,
        capability: str,
        *,
        include_archived: bool = False,
    ) -> bool: ...

    def list_actor_scopes(self, actor_id: str, capability: str | None = None) -> list[ScopeV1]: ...


class ContextRegistryMutation(Protocol):
    def register(self, record: Any, *, upsert: bool = True) -> Any: ...

    def register_external_ref(
        self,
        external_ref: ExternalRefV1,
        *,
        internal_entity_type: str,
        internal_entity_id: str,
        upsert: bool = True,
    ) -> None: ...


_COLLECTIONS = {
    ActorV1: ("actors", "actor_id"),
    OrganizationRecordV1: ("organizations", "organization_id"),
    BrandRecordV1: ("brands", "brand_id"),
    CategoryRecordV1: ("categories", "category_id"),
    ShopRecordV1: ("shops", "shop_id"),
    ShopCategoryMembershipV1: ("shop_category_memberships", "membership_id"),
    ChannelRecordV1: ("channels", "channel_id"),
    MasterProductRefV1: ("master_products", "master_product_id"),
    StoreListingRefV1: ("store_listings", "listing_id"),
    DeviceSessionV1: ("device_sessions", "device_id"),
    BrainWorkerV1: ("brain_workers", "worker_id"),
    ScopeGrantV1: ("scope_grants", "grant_id"),
    RoleAssignmentV1: ("role_assignments", "assignment_id"),
}


def _record_scope(record: Any) -> dict[str, str | None]:
    if isinstance(record, ActorV1):
        return record.scope.as_dict()
    if isinstance(record, OrganizationRecordV1):
        return {"organization_id": record.organization_id}
    if isinstance(record, BrandRecordV1):
        return {"organization_id": record.organization_id, "brand_id": record.brand_id}
    if isinstance(record, CategoryRecordV1):
        return {"organization_id": record.organization_id, "category_id": record.category_id}
    if isinstance(record, ShopRecordV1):
        return {"organization_id": record.organization_id, "shop_id": record.shop_id}
    if isinstance(record, ShopCategoryMembershipV1):
        return {
            "organization_id": record.organization_id,
            "shop_id": record.shop_id,
            "category_id": record.category_id,
        }
    if isinstance(record, ChannelRecordV1):
        return {
            "organization_id": record.organization_id,
            "shop_id": record.shop_id,
            "channel_id": record.channel_id,
        }
    if isinstance(record, MasterProductRefV1):
        return {
            "organization_id": record.organization_id,
            "brand_id": record.brand_id,
            "category_id": record.category_id,
            "product_id": record.master_product_id,
        }
    if isinstance(record, StoreListingRefV1):
        return {
            "shop_id": record.shop_id,
            "channel_id": record.channel_id,
            "product_id": record.master_product_id,
            "listing_id": record.listing_id,
        }
    if isinstance(record, (ScopeGrantV1, RoleAssignmentV1)):
        return record.scope.as_dict()
    if isinstance(record, DeviceSessionV1):
        return {"organization_id": record.organization_id}
    return {}


def _record_indexes(record: Any) -> dict[str, str | None]:
    scope = _record_scope(record)
    indexes = {
        key: value
        for key, value in scope.items()
        if key in {
            "organization_id",
            "brand_id",
            "category_id",
            "shop_id",
            "channel_id",
            "product_id",
            "sku_id",
            "listing_id",
        }
    }
    if isinstance(record, MasterProductRefV1):
        indexes["master_product_id"] = record.master_product_id
    if isinstance(record, StoreListingRefV1):
        indexes["listing_id"] = record.listing_id
        indexes["master_product_id"] = record.master_product_id
    if isinstance(record, (ScopeGrantV1, RoleAssignmentV1)):
        indexes["actor_id"] = record.actor_id
    if isinstance(record, ActorV1):
        indexes["actor_id"] = record.actor_id
    if isinstance(record, (DeviceSessionV1,)):
        indexes["actor_id"] = record.user_id
    return indexes


class ContextRegistry(ContextRegistryRead, ContextRegistryMutation):
    """Read and local-test mutation boundary for identity and context data."""

    def __init__(
        self,
        adapter: PersistenceAdapter | None = None,
        *,
        role_capabilities: Mapping[str, set[str] | frozenset[str]] | None = None,
    ) -> None:
        self.adapter = adapter or InMemoryPersistenceAdapter()
        self.role_capabilities = {
            role.upper(): frozenset(values)
            for role, values in (role_capabilities or DEFAULT_ROLE_CAPABILITIES).items()
        }

    def _save(self, record: Any, *, upsert: bool) -> Any:
        entry = _COLLECTIONS.get(type(record))
        if entry is None:
            raise RegistryError(f"unsupported registry record: {type(record).__name__}")
        collection, key_field = entry
        value = record.as_dict()
        with self.adapter.transaction():
            self.adapter.save_record(
                collection,
                getattr(record, key_field),
                value,
                indexes=_record_indexes(record),
                upsert=upsert,
            )
            if isinstance(record, StoreListingRefV1) and record.external_ref is not None:
                self.register_external_ref(
                    record.external_ref,
                    internal_entity_type="store_listing",
                    internal_entity_id=record.listing_id,
                    upsert=upsert,
                )
        return record

    def register(self, record: Any, *, upsert: bool = True) -> Any:
        self._validate_lineage(record)
        return self._save(record, upsert=upsert)

    def _validate_lineage(self, record: Any) -> None:
        if isinstance(record, OrganizationRecordV1):
            return
        if isinstance(record, ActorV1):
            if not self.scope_exists(record.scope):
                raise RegistryNotFound("actor scope does not exist")
            return
        if isinstance(record, BrandRecordV1):
            organization = self.get_organization(record.organization_id)
            if organization is None:
                raise RegistryNotFound("brand organization does not exist")
            return
        if isinstance(record, CategoryRecordV1):
            organization = self.get_organization(record.organization_id)
            if organization is None:
                raise RegistryNotFound("category organization does not exist")
            if record.parent_category_id is not None:
                parent = self.get_category(record.parent_category_id)
                if parent is None or parent.organization_id != record.organization_id:
                    raise RegistryNotFound("category parent lineage is invalid")
            return
        if isinstance(record, ShopRecordV1):
            organization = self.get_organization(record.organization_id)
            if organization is None:
                raise RegistryNotFound("shop organization does not exist")
            if record.brand_id is not None:
                brand = self.get_brand(record.brand_id)
                if brand is None or brand.organization_id != record.organization_id:
                    raise RegistryNotFound("shop brand lineage is invalid")
            return
        if isinstance(record, ShopCategoryMembershipV1):
            shop = self.get_shop(record.shop_id)
            category = self.get_category(record.category_id)
            if (
                shop is None
                or category is None
                or shop.organization_id != record.organization_id
                or category.organization_id != record.organization_id
            ):
                raise RegistryNotFound("shop category membership lineage is invalid")
            return
        if isinstance(record, ChannelRecordV1):
            shop = self.get_shop(record.shop_id)
            if shop is None or shop.organization_id != record.organization_id:
                raise RegistryNotFound("channel shop lineage is invalid")
            return
        if isinstance(record, MasterProductRefV1):
            category = self.get_category(record.category_id)
            if category is None or category.organization_id != record.organization_id:
                raise RegistryNotFound("master product category lineage is invalid")
            if record.brand_id is not None:
                brand = self.get_brand(record.brand_id)
                if brand is None or brand.organization_id != record.organization_id:
                    raise RegistryNotFound("master product brand lineage is invalid")
            return
        if isinstance(record, StoreListingRefV1):
            shop = self.get_shop(record.shop_id)
            channel = self.get_channel(record.channel_id)
            if shop is None or channel is None or channel.shop_id != record.shop_id:
                raise RegistryNotFound("store listing channel lineage is invalid")
            if record.master_product_id is not None:
                product = self.get_master_product(record.master_product_id)
                if product is None or product.organization_id != shop.organization_id:
                    raise RegistryNotFound("store listing master product lineage is invalid")
            return
        if isinstance(record, (ScopeGrantV1, RoleAssignmentV1)):
            if not self.scope_exists(record.scope):
                raise RegistryNotFound("grant or role scope does not exist")
            return
        if isinstance(record, DeviceSessionV1):
            if self.get_organization(record.organization_id) is None:
                raise RegistryNotFound("device organization does not exist")
            return
        if isinstance(record, BrainWorkerV1):
            return

    def _get(self, record_type: type, record_id: str) -> Any | None:
        collection, _ = _COLLECTIONS[record_type]
        value = self.adapter.get_record(collection, record_id)
        return None if value is None else record_type.from_mapping(value)

    def get_organization(self, organization_id: str) -> OrganizationRecordV1 | None:
        return self._get(OrganizationRecordV1, organization_id)

    def get_actor(self, actor_id: str) -> ActorV1 | None:
        return self._get(ActorV1, actor_id)

    def get_brand(self, brand_id: str) -> BrandRecordV1 | None:
        return self._get(BrandRecordV1, brand_id)

    def get_category(self, category_id: str) -> CategoryRecordV1 | None:
        return self._get(CategoryRecordV1, category_id)

    def get_shop(self, shop_id: str) -> ShopRecordV1 | None:
        return self._get(ShopRecordV1, shop_id)

    def list_shops(self, organization_id: str) -> list[ShopRecordV1]:
        organization_id = _internal_id(organization_id, "organization_id")
        values = self.adapter.list_records(
            "shops",
            filters={"organization_id": organization_id},
        )
        return [ShopRecordV1.from_mapping(value) for value in values]

    def get_channel(self, channel_id: str) -> ChannelRecordV1 | None:
        return self._get(ChannelRecordV1, channel_id)

    def get_master_product(self, master_product_id: str) -> MasterProductRefV1 | None:
        return self._get(MasterProductRefV1, master_product_id)

    def get_store_listing(self, listing_id: str) -> StoreListingRefV1 | None:
        return self._get(StoreListingRefV1, listing_id)

    def get_device(self, device_id: str) -> DeviceSessionV1 | None:
        return self._get(DeviceSessionV1, device_id)

    def get_worker(self, worker_id: str) -> BrainWorkerV1 | None:
        return self._get(BrainWorkerV1, worker_id)

    def get_shop_categories(
        self,
        shop_id: str,
        *,
        include_proposed: bool = False,
        include_disabled: bool = False,
    ) -> list[ShopCategoryMembershipV1]:
        values = self.adapter.list_records(
            "shop_category_memberships",
            filters={"shop_id": _internal_id(shop_id, "shop_id")},
        )
        memberships = [ShopCategoryMembershipV1.from_mapping(value) for value in values]
        return [
            item
            for item in memberships
            if (include_proposed or item.status != "PROPOSED")
            and (include_disabled or item.status != "DISABLED")
        ]

    def register_external_ref(
        self,
        external_ref: ExternalRefV1,
        *,
        internal_entity_type: str,
        internal_entity_id: str,
        upsert: bool = True,
    ) -> None:
        try:
            self.adapter.save_external_mapping(
                source=external_ref.source,
                entity_type=external_ref.entity_type,
                external_id=external_ref.external_id,
                shop_id=external_ref.shop_id,
                channel_id=external_ref.channel_id,
                internal_entity_type=_safe_ref(internal_entity_type, "internal_entity_type"),
                internal_entity_id=_safe_ref(internal_entity_id, "internal_entity_id"),
                value=external_ref.as_dict(),
                upsert=upsert,
            )
        except PersistenceConflict as exc:
            raise RegistryConflict(str(exc)) from exc

    def resolve_external_ref(self, external_ref: ExternalRefV1) -> dict[str, Any] | None:
        return self.adapter.resolve_external_mapping(
            source=external_ref.source,
            entity_type=external_ref.entity_type,
            external_id=external_ref.external_id,
            shop_id=external_ref.shop_id,
            channel_id=external_ref.channel_id,
        )

    def scope_exists(self, scope: ScopeV1, *, include_archived: bool = False) -> bool:
        if scope.is_global:
            return True
        active_statuses = {"ACTIVE"} | ({"ARCHIVED"} if include_archived else set())
        organization = self.get_organization(scope.organization_id)
        if organization is None or organization.status not in active_statuses:
            return False
        if scope.brand_id is not None:
            brand = self.get_brand(scope.brand_id)
            if brand is None or brand.organization_id != scope.organization_id or brand.status not in active_statuses:
                return False
        if scope.category_id is not None:
            category = self.get_category(scope.category_id)
            if category is None or category.organization_id != scope.organization_id or category.status not in active_statuses:
                return False
        if scope.shop_id is not None:
            shop = self.get_shop(scope.shop_id)
            if shop is None or shop.organization_id != scope.organization_id or shop.status not in active_statuses:
                return False
            if scope.category_id is not None:
                memberships = self.get_shop_categories(scope.shop_id)
                if not any(item.category_id == scope.category_id and item.is_formal for item in memberships):
                    return False
        if scope.channel_id is not None:
            channel = self.get_channel(scope.channel_id)
            if (
                channel is None
                or channel.organization_id != scope.organization_id
                or channel.shop_id != scope.shop_id
                or channel.status not in active_statuses
            ):
                return False
        if scope.product_id is not None:
            product = self.get_master_product(scope.product_id)
            if (
                product is None
                or product.organization_id != scope.organization_id
                or product.category_id != scope.category_id
                or product.status not in active_statuses
            ):
                return False
            if scope.shop_id is not None:
                listings = self.adapter.list_records(
                    "store_listings",
                    filters={
                        "shop_id": scope.shop_id,
                        "master_product_id": scope.product_id,
                    },
                )
                if scope.channel_id is not None:
                    listings = [
                        item for item in listings if item.get("channel_id") == scope.channel_id
                    ]
                if not any(item.get("status") in active_statuses for item in listings):
                    return False
        if scope.sku_id is not None:
            return False
        return True

    def validate_scope(self, scope: ScopeV1) -> bool:
        return self.scope_exists(scope)

    def actor_can_access(
        self,
        actor_id: str,
        scope: ScopeV1,
        capability: str,
        *,
        include_archived: bool = False,
    ) -> bool:
        if not self.scope_exists(scope, include_archived=include_archived):
            return False
        actor_id = _safe_ref(actor_id, "actor_id")
        capability = _safe_ref(capability, "capability", limit=80)
        assignments = [
            RoleAssignmentV1.from_mapping(value)
            for value in self.adapter.list_records("role_assignments", filters={"actor_id": actor_id})
        ]
        active_assignments = [
            assignment
            for assignment in assignments
            if assignment.status == "ACTIVE"
            and assignment.scope.contains(scope)
            and capability in self.role_capabilities.get(assignment.role, frozenset())
        ]
        if not active_assignments:
            return False
        grants = [
            ScopeGrantV1.from_mapping(value)
            for value in self.adapter.list_records("scope_grants", filters={"actor_id": actor_id})
        ]
        if any(grant.effect == "DENY" and grant.covers(scope, capability) for grant in grants):
            return False
        return any(grant.effect == "ALLOW" and grant.covers(scope, capability) for grant in grants)

    def list_actor_scopes(self, actor_id: str, capability: str | None = None) -> list[ScopeV1]:
        actor_id = _safe_ref(actor_id, "actor_id")
        grants = [
            ScopeGrantV1.from_mapping(value)
            for value in self.adapter.list_records("scope_grants", filters={"actor_id": actor_id})
            if value.get("effect") == "ALLOW"
        ]
        assignments = [
            RoleAssignmentV1.from_mapping(value)
            for value in self.adapter.list_records("role_assignments", filters={"actor_id": actor_id})
            if value.get("status") == "ACTIVE"
        ]
        scopes: dict[str, ScopeV1] = {}
        for grant in grants:
            if capability is not None and capability not in grant.capabilities:
                continue
            if not any(
                assignment.scope.contains(grant.scope)
                and (
                    capability is None
                    or capability in self.role_capabilities.get(assignment.role, frozenset())
                )
                for assignment in assignments
            ):
                continue
            if any(
                deny.effect == "DENY"
                and deny.covers(grant.scope, capability or grant.capabilities[0])
                for deny in (
                    ScopeGrantV1.from_mapping(value)
                    for value in self.adapter.list_records("scope_grants", filters={"actor_id": actor_id})
                )
            ):
                continue
            scopes[grant.scope.canonical_json()] = grant.scope
        return list(scopes.values())
