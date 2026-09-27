# Phase 2B-3 Central Brain Control Plane

更新时间：2026-09-27
阶段：Phase 2B-3，Central Brain Control Plane API + Event Sync

目标来源：

- [GLOBAL_GOAL.md](../GLOBAL_GOAL.md)
- [Commerce OS Global Architecture v2](commerce-os-global-architecture-v2.md)
- [Phase 2B-1 Multi-Store Core Contracts](phase2b-multistore-core-contracts.md)
- [Phase 2B-2 Context Registry](phase2b-context-registry.md)

## 1. 阶段边界

本阶段实现本地可运行、可测试的 modular monolith skeleton。它是共享状态和通信
中心，不是 Laya、Hermes 或平台执行器。

已实现：

- FastAPI application factory：`control_plane.create_app`；
- backend-neutral service layer；
- 复用 `bridge/core/contracts.py`、`ContextRegistry` 和 `PersistenceAdapter`；
- Device Session、Brain Worker heartbeat、Lease foundation；
- Task、Alert、Approval state service；
- append-only Event Store 和 monotonic cursor；
- `/sync` batch upload/replay；
- bounded WebSocket event channel；
- Durable Outbox / offline retry foundation；
- 统一错误、request ID、audit event 和 fail-closed AuthProvider。

本阶段不部署正式云服务器，不连接真实抖店/千川，不启动 Browser Runtime，不做
Owner Mobile UI，不触发 Laya/Hermes 推理，也不执行任何平台写操作。

## 2. 分层与依赖方向

```text
HTTP / WebSocket
        ↓
FastAPI app + API schemas + RequestIdentity
        ↓
Task / Alert / Approval / Device / Worker / Lease / Sync services
        ↓
ContextRegistry + EventStore + Repository + EventBroker
        ↓
PersistenceAdapter
        ↓
InMemoryPersistenceAdapter / SQLitePersistenceAdapter
```

API 不直接 import `sqlite3`。业务层不依赖 SQLite 查询细节。未来可以实现
PostgreSQL adapter，而不改变上层 core contracts 或 service API。

`ControlPlane.build` 支持注入：

- `PersistenceAdapter`；
- `ContextRegistry`；
- `AuthProvider`；
- `Clock`；
- `EventBroker`。

因此测试可以注入 InMemory adapter、SQLite adapter、`FakeClock` 和本地
`TestAuthProvider`，不需要 monkeypatch 系统时间。

## 3. AuthContext 与 RBAC

请求身份使用：

```text
RequestIdentity
├── actor_id
├── organization_id
├── device_id optional
├── roles
├── authenticated
└── auth_source
```

默认 `FailClosedAuthProvider` 对所有请求返回未认证。它不会把没有身份的请求
当作 Owner，也不会信任任意 `X-User-Id`。

本地测试使用 `LocalAuthProvider` / `TestAuthProvider`，只接受构造时注册的
opaque local token。它不是生产登录系统。

每个受保护写请求经过：

```text
RequestIdentity
→ authenticated
→ actor registered
→ organization match
→ ContextRegistry.scope_exists
→ ContextRegistry.actor_can_access
→ capability ∩ scope
→ DENY precedence
```

未认证返回 `UNAUTHENTICATED`。能力不足或 scope 不匹配返回
`SCOPE_DENIED` / `FORBIDDEN`。跨组织请求不会被降级为组织级访问。

本阶段对 `DEFAULT_ROLE_CAPABILITIES` 做了向后兼容扩展，增加了：

- `event.read` / `event.write`；
- `sync.read` / `sync.write`；
- `alert.read` / `alert.create` / `alert.update`；
- `approval.create`；
- `device.session` / `device.heartbeat`；
- `worker.heartbeat`；
- `lease.acquire` / `lease.renew` / `lease.release`；
- Owner / Manager 的 `task.update`。

没有修改 `ScopeV1`、Task/Alert/Approval/Event 的 schema version。

## 4. Device Session

Device Session 复用 `DeviceSessionV1`，不把 Actor 和 Device 混成一个对象。

支持：

- `POST /devices/session`；
- `GET /devices/{device_id}`；
- `POST /devices/{device_id}/heartbeat`；
- `POST /devices/{device_id}/disconnect`；
- `POST /devices/{device_id}/revoke`；
- `GET /context/...` 访问时的设备范围校验；
- `/sync` 和 WebSocket 建连时的 active session 校验。

Session wrapper 只保存：

- session_id；
- issued_at；
- expires_at；
- ack_cursor；
- DeviceSessionV1。

不保存 token、Cookie、Secret 或 Session 原文。网络断开只改变连接/设备状态，
不等同于用户登出。`REVOKED` 设备不能继续 heartbeat、sync 或 WebSocket。

## 5. Brain Worker 与 Lease

Worker 复用 `BrainWorkerV1`，当前只实现：

- register；
- heartbeat；
- list；
- 基于服务端 `Clock` 计算 `ONLINE` / `DEGRADED` / `OFFLINE`。

Worker 自己不能通过请求体永久声明 `ONLINE`。当前阈值：

- 30 秒后 `DEGRADED`；
- 120 秒后 `OFFLINE`。

Worker `OFFLINE` 不会自动把业务 Task 标记为失败。基础设施健康和业务状态分开。

Lease 复用 `LeaseV1`，提供：

- acquire；
- renew；
- release；
- expired lease 自动不再阻塞新的 acquire；
- worker_id 和 lease_id mismatch fail closed；
- release 幂等；
- 业务结果仍需要 idempotency key。

duration 限制在 30–120 秒。所有有效性判断使用 server time；客户端时间不参与
冲突判断。

## 6. Task Service

Task Service 复用 `TaskV1`，支持：

```text
PENDING → ASSIGNED → IN_PROGRESS → DONE
PENDING → CANCELLED
ASSIGNED → CANCELLED
IN_PROGRESS → BLOCKED
BLOCKED → IN_PROGRESS
BLOCKED → CANCELLED
```

API：

- `GET /tasks`
- `POST /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/{task_id}/assign`
- `POST /tasks/{task_id}/start`
- `POST /tasks/{task_id}/block`
- `POST /tasks/{task_id}/complete`
- `POST /tasks/{task_id}/cancel`

非法迁移返回 `INVALID_TRANSITION`。不会静默跳过状态，也不支持本阶段 reopen。
每次成功迁移产生业务 Event 和 Security/Audit Event。

list API 支持 scope、status、created_after、limit、offset。默认 limit 为 100，
最大 500，不会无限返回。

## 7. Alert Service

Alert Service 复用 `AlertV1`，支持：

- create；
- get/list；
- acknowledge；
- resolve；
- expire。

API：

- `GET /alerts`
- `POST /alerts`
- `GET /alerts/{alert_id}`
- `POST /alerts/{alert_id}/ack`
- `POST /alerts/{alert_id}/resolve`
- `POST /alerts/{alert_id}/expire`

Alert 不是 Task，也不会自动转换为 Task。

同一 `scope + reason_code + dedupe_key` 在 `cooldown_until` 之前不会创建新的
业务 Alert。当前选择返回已有 Alert，并标记响应 `deduped=true`；不会重复追加
业务事件。cooldown 到期后允许新的 Alert。

## 8. Approval Service

Approval Service 复用 `ApprovalRequestV1`，支持：

```text
PENDING
→ APPROVED
→ REJECTED
→ REVISION_REQUESTED
→ CANCELLED
→ EXPIRED
```

API：

- `GET /approvals`
- `POST /approvals`
- `GET /approvals/{approval_id}`
- `POST /approvals/{approval_id}/approve`
- `POST /approvals/{approval_id}/reject`
- `POST /approvals/{approval_id}/request-revision`
- `POST /approvals/{approval_id}/cancel`
- `POST /approvals/{approval_id}/expire`

`APPROVED` 只改变 approval state 并产生 event。它不调用 Executor，不调用
Browser Runtime，不调用平台 API，不改变预算、ROI、价格、库存或上下架状态。

Approval payload 没有 browser click、shell command、platform write payload 等
字段。所有 event payload 继续由 core contract 做敏感字段和执行字段拒绝。

## 9. Event Store

Event Store 复用 `EventEnvelopeV1`，每条服务器存储记录增加：

```text
cursor: monotonic integer
event: EventEnvelopeV1
```

三类时间语义严格分开：

- `occurred_at`：业务事件发生时间；
- `received_at`：Control Plane 接收时间；
- `cursor`：服务器同步顺序。

cursor 不是业务时间，也不能用客户端时间替代。

Event Store 是 append-only：

- 不 UPDATE historical event payload；
- 不 DELETE history 修复错误；
- 修正通过追加 correction event 表达；
- 同一 event_id 或 idempotency_key 重传返回原事件；
- 同 key 不同内容返回 `IDEMPOTENCY_CONFLICT`。

读取接口：

```text
GET /events?cursor=0&limit=100&organization_id=...&shop_id=...
```

`cursor=0` 表示从可用历史起点开始。cursor 超过当前历史时显式返回
`STALE_CURSOR`，不会悄悄返回最新状态。limit 默认 100，最大 500。

直接事件上传：

```text
POST /events
POST /sync
```

事件上传要求 event actor 等于 authenticated actor，并再次通过 Registry 的
scope/capability 检查。

## 10. Event Delivery 与 WebSocket

WebSocket：

```text
GET /ws/events
```

连接参数至少包括 `device_id`、`organization_id`，可选 shop/category scope、
cursor 和 queue_size。

连接顺序：

```text
authenticate
→ active device session
→ authorized subscription scope
→ accept
→ cursor replay
→ live event delivery
```

WebSocket 是低延迟通知渠道，不是唯一真相来源。

```text
WebSocket = realtime hint
Event Store = recovery source of truth
```

客户端断线后必须使用最后确认的 cursor 通过 `/events`、`/sync` 或重新连接
`/ws/events?cursor=...` 恢复。

投递语义是：

```text
at-least-once delivery
+
idempotent processing
```

不声称 exactly-once。客户端必须能重复收到同一个 event，并以 event_id、
idempotency_key、cursor 防止业务副作用重复发生。

每个连接使用 bounded asyncio queue。队列满时：

- 不无限增长内存；
- 标记 `SLOW_CONSUMER`；
- 关闭连接；
- 要求客户端回到 Event Store cursor replay。

## 11. Offline Sync 与 Durable Outbox

`POST /sync` 使用统一请求结构：

```text
device_id
actor_id
idempotency_key
last_ack_cursor
client_time
events[]
capabilities[]
session_info
```

响应包含：

```text
server_time
latest_cursor
events_after_cursor[]
acknowledged_event_ids[]
device_status
sync_status
```

同步流程：

```text
Client local queue
→ batch upload
→ server event idempotency
→ acknowledged_event_ids
→ replay missing server events
→ client advances ack cursor
```

`client_time` 仅用于诊断，不参与 cursor、lease 或冲突排序。

`DurableOutbox` 使用现有 `PersistenceAdapter`，不直接 import `sqlite3`。状态：

```text
PENDING
→ IN_FLIGHT
→ ACKED

IN_FLIGHT → PENDING       (process recovery)
PENDING → DEAD_LETTER     (retry bound exceeded)
```

它只承诺本地持久化和 at-least-once retry，不承诺网络 exactly-once。成功上传但
客户端未收到 ACK 时，重传同一 event 不会在 Event Store 产生重复事件。

## 12. Audit

每次成功 mutation 追加：

1. Business Event；
2. `security.audit` Event。

Audit 至少记录：

- actor_id；
- device_id optional；
- scope；
- operation；
- target_type；
- target_id；
- server timestamp；
- result；
- reason_code optional。

拒绝的已认证请求也尽力追加 denied audit；未认证请求没有可验证 actor 时不伪造
审计主体。

Audit payload 不写入 token、password、Cookie、Secret、原始 DOM、HTML 或平台
执行命令。

## 13. Error Contract 与 Request ID

HTTP 错误统一为：

```json
{
  "error_code": "SCOPE_DENIED",
  "message": "requested scope is not authorized",
  "request_id": "req_...",
  "details": null
}
```

重要错误码：

- `UNAUTHENTICATED`
- `FORBIDDEN`
- `SCOPE_DENIED`
- `NOT_FOUND`
- `CONFLICT`
- `INVALID_TRANSITION`
- `IDEMPOTENCY_CONFLICT`
- `STALE_CURSOR`
- `LEASE_CONFLICT`
- `VALIDATION_ERROR`
- `EXECUTION_DISABLED`

`request_id` 是一次网络请求关联号，不代替业务 `idempotency_key`。客户端可以
通过 `X-Request-ID` 提供安全格式的 request ID；无效或缺失时由服务生成。

## 14. Persistence 与未来迁移

当前继续复用 Phase 2B-2：

- `InMemoryPersistenceAdapter`：合同和本地测试；
- `SQLitePersistenceAdapter`：WAL、事务、版本迁移和可重启本地持久化。

Control Plane collections 当前包括：

- `cp_tasks`
- `cp_alerts`
- `cp_approvals`
- `cp_device_sessions`
- `cp_workers`
- `cp_leases`
- `cp_events`
- `cp_idempotency`
- `cp_offline_outbox`

未来 PostgreSQL：

1. 实现同一个 `PersistenceAdapter` port；
2. 将 collection 映射到 typed tables；
3. 对 events 增加 append-only 约束、sequence 和审计索引；
4. 对组织、shop、actor、scope 增加 foreign key / RLS；
5. 双读校验后切换写入；
6. 通过 cursor、event_id、idempotency_key 对账。

未来 Redis：

- 只承担短期 broker fan-out、连接 presence、backpressure 辅助状态；
- 不能替代 Event Store；
- 不能成为历史 recovery source。

## 15. Non-goals

本阶段禁止：

- 正式云服务器部署；
- PostgreSQL、Redis、Kafka、Celery、Kubernetes、RabbitMQ；
- Browser Runtime / Browser Observer；
- 抖店、千川真实 API；
- 真实账号自动登录；
- Owner Mobile UI；
- Live Audio；
- Laya 新推理功能；
- Hermes 新 Agent；
- 自动执行 Approved proposal；
- 真实预算、ROI、价格、库存、上下架、退款或发消息修改。

## 16. 本地运行与验证

创建 app：

```python
from control_plane import create_app

app = create_app()
```

默认 AuthProvider fail closed。测试或本地 fixture 显式注入
`LocalAuthProvider`，并预先在 Context Registry 注册 Actor、RoleAssignment、
ScopeGrant、Organization、Shop 等记录。

专项测试：

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
PYTHONPATH=. ../.venv/bin/pytest -q test_control_plane.py
```

专项测试覆盖：

- 未认证拒绝；
- capability / scope / DENY precedence；
- cross-shop read 和 WebSocket subscription 拒绝；
- Task 状态流和幂等冲突；
- Alert cooldown；
- Approval state-only；
- secret payload fail closed；
- revoked device；
- duplicate sync upload；
- stale cursor；
- WebSocket replay/live delivery；
- slow consumer；
- durable outbox retry/recovery；
- SQLite restart；
- worker health；
- lease conflict；
- worker offline 不改变 task business state。
