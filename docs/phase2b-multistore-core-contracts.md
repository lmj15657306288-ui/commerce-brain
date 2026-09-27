# Phase 2B-1 Multi-Store Core Contracts

更新时间：2026-09-27
阶段：Phase 2B-1，Multi-Store Core Contracts

最高层目标来源：

- [GLOBAL_GOAL.md](../GLOBAL_GOAL.md)
- [Commerce OS Global Architecture v2](commerce-os-global-architecture-v2.md)

本阶段只建立多店、多品类和多设备通信共同依赖的数据契约。契约是本地
Python schema validation 和安全边界，不是 Cloud API、数据库迁移或任务调度器。

## 1. 实现边界

实现位于：

- `bridge/core/contracts.py`
- `bridge/core/__init__.py`
- `bridge/test_multistore_core_contracts.py`

本阶段不修改已完成的 V1、Laya、Hermes、Central Brain 或 Browser Runtime
实现。已有未跟踪的 Browser Runtime 草稿也不属于本阶段交付。

所有契约具有以下共同性质：

- 版本化；
- `from_mapping` 严格拒绝未知字段；
- `as_dict` 输出规范化结构；
- ID、时间、枚举、数值和嵌套对象做边界校验；
- 敏感字段和真实平台执行字段 fail closed；
- 无网络依赖，不需要真实 token、cookie 或平台账号。

## 2. Scope hierarchy

Canonical hierarchy：

```text
Organization
→ Brand
→ Category
→ Shop
→ Channel
→ Product
→ SKU
```

`Campaign`、`Live`、`Customer` 和后续 `Order` 通过所属业务对象引用这条层级。
不是所有对象必须填满所有层级，但任何非全局对象都必须有足够的 lineage，
不能用名称或平台页面位置猜测归属。

### 2.1 ScopeV1

`ScopeV1` 字段：

```json
{
  "schema_version": 1,
  "organization_id": "org_<internal-id>|null",
  "brand_id": "brand_<internal-id>|null",
  "category_id": "category_<internal-id>|null",
  "shop_id": "shop_<internal-id>|null",
  "channel_id": "channel_<internal-id>|null",
  "product_id": "product_<internal-id>|null",
  "sku_id": "sku_<internal-id>|null"
}
```

内部 ID 由 Commerce Brain 自己生成。契约要求 ID 使用对应的内部命名空间；
平台自己的店铺、商品、SKU 或渠道 ID 不得直接作为系统主键。

作用域类型由最窄非空字段确定：

| Scope | 最低要求 | 典型用途 |
| --- | --- | --- |
| `global` | 全部为空 | 明确允许全局共享的对象 |
| `organization` | `organization_id` | 组织级目录和策略 |
| `brand` | organization + brand | 品牌知识和策略 |
| `category` | organization + category | 可服务该类目下多个店铺 |
| `shop` | organization + shop | 店铺事实和店铺知识 |
| `channel` | organization + shop + channel | 渠道数据和状态 |
| `product` | organization + category + product；店铺可选 | Master Product 或 Store Listing 兼容层 |
| `sku` | organization + category + shop + product + sku | 可交易库存单元 |

`product_id` 不强制绑定单一店铺，以便未来区分 `MasterProduct` 和
`StoreListing`；但若使用 `sku_id`，必须同时提供 shop/product lineage。

### 2.2 Containment and matching

`parent.contains(child)` 表示父 scope 可以授权访问子 scope。父 scope 中每个
非空字段都必须在子 scope 中存在且相等：

```text
category(org_1, cat_apparel)
    contains shop(org_1, cat_apparel, shop_a)
    contains shop(org_1, cat_apparel, shop_b)
    does not contain category(org_1, cat_food)
```

`matches` 是完整 canonical equality，不是模糊匹配。`scope_allows(actor_scope,
requested_scope)` 只执行 containment，不自动扩展 scope。

因此：

- Shop A 的私有对象不默认匹配 Shop B；
- Category-level knowledge 可以服务该 Category 下多个明确标注的 Shop；
- Category A 不默认流入 Category B；
- Global scope 只代表候选范围，不自动授予所有业务对象的共享资格；
- 全局知识或对象必须由上层 policy 明确允许。

### 2.3 Canonical serialization and hash

`ScopeV1.canonical_json()` 使用排序后的键、固定 JSON 分隔符和 UTF-8；
`ScopeV1.stable_hash()` 使用 SHA-256。字段输入顺序变化不会改变 hash。

## 3. Internal ID vs External ID

### 3.1 ExternalRefV1

```json
{
  "schema_version": 1,
  "source": "douyin",
  "entity_type": "product",
  "external_id": "platform-value",
  "shop_id": "shop_<internal-id>|null",
  "channel_id": "channel_<internal-id>|null"
}
```

`external_id` 只是指定 source 和 entity type 下的外部引用，不是内部主键。
相同平台引用在不同内部 scope 下会被标记为 conflict，不能静默合并内部实体。
ExternalRef 不负责证明平台 ID 的真实性，真实性由后续 authorized collector
和 reconciliation 层负责。

## 4. Actor / RBAC boundary

### 4.1 ActorV1

Actor 类型：

```text
HUMAN
LAYA
HERMES
CODEX
SYSTEM
EDGE
WORKER
```

Actor 至少包含：

- `actor_id`
- `actor_type`
- `scope`
- `role`

Actor 访问请求 scope 时必须满足 `actor.scope.contains(requested_scope)`。
Actor 可以是模型、系统、Edge 或 Worker，但 Actor 类型不等于平台写权限。
任何平台写能力仍由 Rules/Safety、proposal、approval 和更高层 policy 共同
限制。

### 4.2 Approval scope

`ApprovalRequestV1` 只记录：

- approval ID；
- scope；
- proposal ID；
- risk level；
- requester；
- 时间；
- reason；
- 状态；
- decision actor 和 note。

Approval 不包含真实 platform executable payload。`APPROVED` 只表示人类批准
该 proposal、experiment、human task 或 low-risk workflow draft，不表示平台
写入已发生。

Owner approval 状态：

```text
PENDING
APPROVED
REJECTED
REVISION_REQUESTED
EXPIRED
CANCELLED
```

Approval 的 scope 不得大于 requester Actor 的授权 scope；不满足时拒绝创建
后续业务动作。

## 5. Device identity

`DeviceSessionV1` 描述设备与组织范围，不保存 session token 或 cookie：

```json
{
  "schema_version": 1,
  "device_id": "device_<safe-id>",
  "device_type": "DESKTOP|BROWSER_EXTENSION|LIVE_EDGE|MOBILE|BRAIN_WORKER",
  "user_id": "user_<safe-id>|null",
  "organization_id": "org_<internal-id>",
  "shop_ids": ["shop_<internal-id>"],
  "capabilities": ["task_sync", "observer"],
  "status": "ACTIVE|DISCONNECTED|REVOKED|EXPIRED",
  "connected_at": "...",
  "last_seen_at": "..."
}
```

`shop_ids` 为空表示组织级设备范围；非空时只能通过列出的店铺 scope。
Device capability 说明通信或观察能力，不等于平台写权限。

## 6. Worker identity and health

`BrainWorkerV1` 只定义 Worker identity / health：

- `worker_id`
- `worker_type`
- `capabilities`
- `status`
- `heartbeat_at`
- `last_seen_at`

Worker health 状态：

```text
ONLINE
DEGRADED
OFFLINE
UNKNOWN
```

当前不实现模型调度、Worker 注册服务、抢占策略或复杂 scheduler。Worker
离线不等于任务业务失败；业务状态和基础设施健康状态必须分开记录。

## 7. Lease semantics

`LeaseV1` 字段：

- `lease_id`
- `resource_type`
- `resource_id`
- `worker_id`
- `issued_at`
- `expires_at`
- `idempotency_key`

规则：

1. 同一个 `(resource_type, resource_id)` 同时最多一个有效 lease。
2. 未过期 lease 阻止第二 Worker 获取同一资源。
3. lease 过期后允许重新获取。
4. Lease 只是领取/处理权的时间边界，不代表业务动作成功。
5. 所有后续结果必须使用 `idempotency_key`，避免重连或重试产生重复语义。

本阶段只提供 `LeaseV1.is_active` 和 `can_acquire_lease` 判断，不实现数据库
锁、分布式锁或 scheduler。

## 8. Task / Alert / Approval semantics

### 8.1 TaskV1

Task 字段：

- `task_id`
- `scope`
- `task_type`
- `priority`
- `status`
- `owner`
- `created_by`
- `created_at`
- `updated_at`
- `due_at`
- `business_impact`
- `source_refs`
- `idempotency_key`

Task 状态：

```text
PENDING
ASSIGNED
IN_PROGRESS
BLOCKED
DONE
CANCELLED
EXPIRED
```

契约只定义状态和合法迁移，不实现完整 task scheduler。

### 8.2 AlertV1

Alert 字段：

- `alert_id`
- `scope`
- `priority`
- `status`
- `reason_code`
- `summary`
- `evidence_refs`
- `business_impact`
- `recommended_action`
- `created_at`
- `updated_at`
- `dedupe_key`
- `cooldown_until`

Alert 是通知和人工复核入口，不是执行授权。`dedupe_key` 与 cooldown 为后续
通知去重、冷却和聚合保留。

### 8.3 BusinessImpactV1

至少支持：

- `gmv_impact`
- `profit_impact`
- `inventory_impact`
- `customer_impact`
- `live_impact`
- `confidence`

`null` 表示 unknown/unavailable；真实 `0` 保持为 `0.0`，不能被转成 null，
unknown 也不能被默认填成 0。

## 9. EventEnvelopeV1 and idempotency

`EventEnvelopeV1` 为未来 WebSocket 和 offline queue 预留：

- `event_id`
- `event_type`
- `scope`
- `actor`
- `source`
- `occurred_at`
- `received_at`
- `schema_version`
- `idempotency_key`
- `payload`

事件 actor 必须能访问事件 scope。Payload 只接受有界 JSON-like 数据，并拒绝
token、cookie、secret、password、完整 DOM、HTML、截图、脚本和平台执行字段。

`IdempotencyLedger` 是本地内存语义 helper：

- 同一 `(kind, idempotency_key)` 重复指向同一 resource 时返回原 resource；
- 同一 key 指向不同 resource 时 fail closed；
- 本阶段不承诺跨进程或持久化 exactly-once。

## 10. Future PostgreSQL mapping

Phase 2B Cloud 未来使用 PostgreSQL 时，契约可映射为模块化单体中的表：

| Contract | Future table |
| --- | --- |
| `ScopeV1` | `organizations`, `brands`, `categories`, `shops`, `channels`, `products`, `skus` |
| `ExternalRefV1` | `external_refs` |
| `ActorV1` | `actors`, `actor_scope_grants` |
| `DeviceSessionV1` | `devices`, `device_sessions` |
| `BrainWorkerV1` | `brain_workers`, `worker_heartbeats` |
| `LeaseV1` | `resource_leases` |
| `TaskV1` | `tasks` |
| `AlertV1` | `alerts` |
| `ApprovalRequestV1` | `approval_requests` |
| `BusinessImpactV1` | JSONB columns or typed impact columns |
| `EventEnvelopeV1` | `event_envelopes`, `idempotency_records` |

建议所有业务表保留：

- scope foreign keys；
- `created_at` / `updated_at`；
- schema version；
- idempotency key where applicable；
- audit actor；
- source/evidence references。

具体 migration、索引、RLS 和事务边界属于后续 Cloud Control Plane 阶段。

## 11. Future Redis responsibilities

Redis 后续可承担：

- 短期 session/cache；
- worker heartbeat TTL；
- lease expiry lookup；
- WebSocket presence；
- bounded notification cooldown/dedupe；
- short-lived resume cursor；
- rate limiting。

Redis 不应成为长期业务事实源、利润真相源、知识唯一存储或审计唯一存储。
长期事件、Task、Alert、Approval、Outcome 和审计记录仍应可在 PostgreSQL 或
持久化事实层恢复。

## 12. Fail-closed rules

- 未知 contract field 拒绝；
- 非法内部 ID 拒绝；
- 缺少必要 lineage 拒绝；
- Actor 不包含 requested scope 拒绝；
- Approval scope 超过 requester scope 拒绝；
- 未过期 lease 不允许第二 Worker 获取；
- 过期、冲突或未知状态不得被静默当作成功；
- unknown business impact 不得转成 0；
- external ID 冲突不得自动合并内部实体；
- payload 含真实凭证、原始页面或执行字段拒绝；
- approval 不产生平台 executable payload；
- 本阶段所有真实平台写操作继续关闭。

## 13. Non-goals

本阶段不做：

- PostgreSQL 部署或 migration；
- Redis 部署；
- 正式域名、TLS 或公网 Cloud；
- WebSocket server；
- 完整 task scheduler；
- Browser Runtime 或 Browser Observer；
- Owner Mobile 页面；
- 真实登录认证；
- 真实抖音 API；
- 任何真实平台写操作；
- Hermes runtime 修改；
- Laya 重装或升级；
- 复杂微服务、Kafka、Kubernetes 或大型事件流平台。

## 14. Verification

专项测试：

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest test_multistore_core_contracts -v
```

测试覆盖 scope isolation、lineage、category knowledge containment、global
scope、external reference conflict、unknown field、invalid ID、stable hash、
Actor authorization、Approval scope、lease expiry、idempotency、unknown
business impact、round-trip schema validation 和安全 payload 拒绝。
