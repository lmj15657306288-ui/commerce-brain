# Phase 2B-2 Scope-aware Context Registry

更新时间：2026-09-27
阶段：Phase 2B-2，Scope-aware Context Registry + Persistence Adapter Foundation

最高目标来源：

- [GLOBAL_GOAL.md](../GLOBAL_GOAL.md)
- [Commerce OS Global Architecture v2](commerce-os-global-architecture-v2.md)
- [Phase 2B-1 Multi-Store Core Contracts](phase2b-multistore-core-contracts.md)

## 1. 本阶段边界

本阶段建立后续 Phase 2B 模块共同依赖的本地 Context Registry 和 persistence
port。实现是 backend-neutral 的本地 foundation，不是正式 Cloud Control Plane。

新增模块：

- `bridge/core/context_registry.py`
- `bridge/core/persistence.py`
- 对 `bridge/core/contracts.py` 的向后兼容扩展
- `bridge/test_context_registry.py`
- `bridge/test_persistence.py`

本阶段不部署 Cloud Gateway，不接真实平台，不运行 Browser Runtime、
Browser Observer、Owner Mobile、FastAPI、WebSocket、Redis 或 PostgreSQL。

## 2. Context Registry 职责

Context Registry 只负责：

- identity；
- organization / brand / category / shop / channel lineage；
- ShopCategoryMembership；
- scope existence 和 containment 的业务验证；
- Actor、RoleAssignment、ScopeGrant；
- DeviceSession 和 BrainWorker identity；
- Context status；
- ExternalRef 到内部实体的 reference mapping。

它不是 Knowledge Store，不负责：

- 业务知识；
- embedding；
- prompt；
- 聊天历史；
- 商品长文；
- 模型推理；
- Profit、Inventory 或广告事实；
- Task scheduler；
- 平台 API 或平台写操作。

Registry 的目标是回答：

- organization、shop、category、channel 是否存在；
- shop 属于哪个 organization；
- shop 当前有哪些正式 category membership；
- actor 是否拥有某个 capability 和 scope；
- device 属于哪个 organization/user；
- worker 的 capability 和 health status；
- context 是否 active / disabled / archived；
- external reference 映射到哪个内部实体；
- request scope 是否存在且合法。

## 3. MasterProduct 与 StoreListing

本阶段保留两种正式身份：

```text
MasterProduct
  ├── ProductVariant / SKU       (future)
  └── StoreListing
        └── ListingSKU / ExternalRef (future)
```

### MasterProductRefV1

只保存：

- `master_product_id`
- `organization_id`
- `brand_id` optional
- `category_id`
- `status`

`master_product_id` 是 Commerce Brain 内部身份，不是平台商品 ID。

### StoreListingRefV1

只保存：

- `listing_id`
- `master_product_id` optional
- `shop_id`
- `channel_id`
- `external_ref` optional
- `status`

早期 listing 可以尚未绑定 MasterProduct，但必须保留明确的 `null` 和 status。
本阶段不实现商品业务、SKU 经营、价格、库存或上下架逻辑。

## 4. ShopCategoryMembership

一个 Shop 可以属于多个 Category，但必须存在显式：

`ShopCategoryMembershipV1`

来源优先级：

```text
MANUAL / MASTER_DATA
→ OFFICIAL_MAPPING
→ IMPORTED_MAPPING
→ INFERRED
```

来源值：

- `MANUAL`
- `MASTER_DATA`
- `OFFICIAL_MAPPING`
- `IMPORTED_MAPPING`
- `INFERRED`

状态：

- `ACTIVE`
- `PROPOSED`
- `DISABLED`

`INFERRED` 自动只能是 `PROPOSED`。契约拒绝把 inferred membership 直接标记为
`ACTIVE`。只有正式 membership 才能让 category scope 服务该 Shop 的新业务
context；proposal 可以被读取和审核，但不能直接改变正式 membership。

商品属于某类目，不会自动改变整个 Shop 的 membership。

## 5. Role 与 Scope Grant

权限采用两条独立轴：

```text
Role → Capability
Scope Grant → Scope + Effect

实际权限 = Role Capability ∩ Scope Grant
```

`RoleAssignmentV1` 记录：

- assignment；
- actor；
- role；
- scope；
- status。

`ScopeGrantV1` 记录：

- grant；
- actor；
- scope；
- capabilities；
- `ALLOW` / `DENY` effect。

角色不绑定死某个店铺。一个 Owner 可以有 organization-level assignment，
一个 Operator 可以有 shop-level assignment；数据范围通过 Scope Grant 进一步
限制。

权限检查必须同时满足：

1. RoleAssignment active；
2. role 包含 requested capability；
3. assignment scope 包含 requested scope；
4. 存在匹配的 `ALLOW` ScopeGrant；
5. 不存在匹配的 `DENY` ScopeGrant。

`DENY` 优先于 `ALLOW`。没有 grant、没有 capability 或 scope 不匹配时默认拒绝。

## 6. Context lifecycle

Context 状态：

```text
ACTIVE
DISABLED
ARCHIVED
```

- `ACTIVE`：可以参与新的业务 context；
- `DISABLED`：默认不能创建新的业务任务或 context；
- `ARCHIVED`：可以按显式 `include_archived=True` 读取历史，但不能作为新的
  active scope。

`validate_scope` 默认只接受 active lineage。状态判断由 Registry 完成，不由
调用方自行猜测。

## 7. ExternalRef mapping

`ExternalRefV1` 仍然只表示：

```text
source + entity_type + external_id + optional shop/channel scope
```

Persistence mapping 额外保存：

```text
internal_entity_type
internal_entity_id
```

唯一性使用：

```text
source
+ entity_type
+ external_id
+ shop scope
+ channel scope
```

因此同一个 `external_id` 在不同 source、entity_type、shop 或 channel 下可以
合法重复。相同作用域的外部引用若尝试映射到不同内部实体，Registry fail closed。
不会使用全局 `external_id UNIQUE` 这种错误约束。

## 8. Persistence abstraction

业务层只依赖 `PersistenceAdapter` port：

- `get_record`
- `list_records`
- `save_record`
- `delete_record`
- `save_external_mapping`
- `resolve_external_mapping`
- `transaction`

当前实现：

- `SQLitePersistenceAdapter`：本地可重启持久化；
- `InMemoryPersistenceAdapter`：合同和纯本地测试。

`ContextRegistry` 不直接 import `sqlite3`。未来 PostgreSQL adapter 可以实现同一
port，不改变上层 contracts 或 Registry API。

## 9. SQLite foundation

SQLite adapter 已启用：

- WAL mode；
- `foreign_keys = ON`；
- transaction / rollback；
- upsert；
- unique identity；
- migration/version table；
- organization、shop、category、actor、listing、master product 和 external
  reference 索引。

核心表保持少量基础结构：

- `schema_migrations`
- `registry_records`
- `external_ref_mappings`

`registry_records` 保存 versioned contract JSON 和必要索引列，避免在 foundation
阶段提前拆成大量业务表。未来 PostgreSQL 迁移时，再把各 collection 映射为
正式领域表和 foreign key/RLS。

### PostgreSQL migration path

未来迁移顺序：

1. 对同一 `PersistenceAdapter` port 实现 PostgreSQL adapter；
2. 将 registry collections 映射为 organizations、brands、categories、shops、
   channels、products、listings、actors、grants、assignments 等表；
3. 保持 contract schema version 和 canonical IDs；
4. 增加 foreign key、事务边界、审计 actor 和 RLS；
5. 先双读/校验，再切换写入；
6. 通过 scope/hash/idempotency 对账后移除 SQLite 运行路径。

## 10. Lease 与 server time

Phase 2B-2 不实现 lease scheduler，但后续 Control Plane 必须遵守：

- server time 是 lease 有效性的权威；
- client time 不参与冲突判断；
- Lease 需要 `issued_at`、`expires_at`、`server_now`；
- 支持 acquire、renew、release、expire；
- active lease 不能被第二 Worker 抢占；
- expired lease 可以重新领取，并生成新的 `lease_id`；
- renew 必须匹配 `lease_id + worker_id`；
- stale renew fail closed；
- release 幂等；
- 业务结果仍需 `idempotency_key`。

## 11. Approval 不等于 execution

Approval 只允许批准：

- proposal；
- experiment；
- human task；
- low-risk workflow draft。

`APPROVED` 不等于 platform execution。ApprovalRequest 不内嵌：

- browser click；
- API write payload；
- shell command；
- platform executable command。

未来可能是：

```text
APPROVED
→ Safety Gate
→ Execution Policy
→ Controlled Executor
```

本阶段不实现这条执行链，也不打开任何真实平台写能力。

## 12. Non-goals

本阶段禁止：

- FastAPI server；
- 正式 REST API；
- WebSocket；
- Redis；
- PostgreSQL 部署；
- Cloud Gateway；
- Browser Runtime / Browser Observer；
- Owner Mobile PWA；
- Live Audio；
- Hermes 新功能；
- Laya 新模型功能；
- 真实账号认证；
- 平台 API；
- 平台写操作；
- 自动审批执行；
- 完整 task scheduler；
- Kafka、Kubernetes 或复杂微服务。

## 13. Verification

专项测试：

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest \
  test_context_registry \
  test_persistence \
  test_multistore_core_contracts
```

测试覆盖：

- organization / brand / category / shop / channel / product / listing lineage；
- 多 Shop Category membership；
- inferred membership proposal boundary；
- Owner organization grant；
- Shop Operator isolation；
- Category grant；
- DENY precedence；
- missing capability / missing grant / scope mismatch；
- disabled / archived lifecycle；
- external reference scoped uniqueness；
- SQLite insert/read/upsert/restart；
- transaction rollback；
- schema migration version；
- unique conflict 和重复写幂等语义。
