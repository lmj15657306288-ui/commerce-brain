# Phase 2B Central Brain Architecture

更新时间：2026-09-26  
分支：`codex/phase2-laya-hermes-browser`

## 目标

建立三个节点之间的最小通信底座：

```text
Mac Brain Server  <--- outbound WSS --->  Cloud Gateway  <--- WSS --->  Chrome Edge Client
       authoritative state                    relay/session hub              local cache + offline queue
```

Phase 2B 只实现 contracts、device/session、task/alert/event、WebSocket 实时同步和离线队列。它不实现复杂业务 UI，不提交任何生产平台写请求，也不替代现有 `StateSnapshot → DecisionProposal → Safety → Shadow` 链路。

## 节点职责

### Mac Brain Server

- 唯一的中央业务状态源和决策编排入口。
- 读取本机 Bridge、Rules、Laya 和后续 Slow Loop 的脱敏结果。
- 主动向 Gateway 建立长连接，避免要求 Mac 暴露公网入站端口。
- 持久化任务、告警、事件游标和待发送消息。
- 只发送脱敏聚合指标、状态引用、proposal/action card/outcome 摘要。

### Cloud Gateway

- 公网 WSS 接入点、设备路由器和连接生命周期管理器。
- 保存最少的设备目录、session 元数据、事件 cursor 和有限期离线转发队列。
- 不成为业务事实源，不运行 Rules/Laya/Hermes，不拥有抖店/千川写权限。
- 只按 `recipient_device_id` 或受控主题转发已验证 envelope。

### Chrome Edge Client

- 负责员工浏览器连接、局部通知、任务/告警展示和人工动作回传。
- 只保存自己的 session、cursor、幂等键和短期离线队列。
- 不持有平台 Cookie 的通信权限，不执行平台写动作。
- 浏览器采集仍走现有 Extension → localhost Bridge 边界。

## 不在本阶段

- 不开放预算、ROI、计划启停、商品价格、库存、上下架或删除写操作。
- 不把 Gateway 作为远程执行器。
- 不同步原始网页 HTML、完整 DOM、截图全文、订单明细、Cookie、Token、Secret、Session 原文。
- 不引入复杂业务 UI、远程 Agent 群聊、自动策略发布或云端模型调用。

## Versioned Contracts

所有消息均使用 `CentralEnvelopeV1`：

```json
{
  "schema_version": 1,
  "message_id": "msg_<safe-id>",
  "event_id": "evt_<safe-id>",
  "message_type": "event|task|alert|ack|heartbeat|resume",
  "sender_device_id": "device_<safe-id>",
  "recipient_device_id": "device_<safe-id>|broadcast",
  "session_id": "session_<safe-id>",
  "sent_at": "2026-09-26T15:00:00+00:00",
  "correlation_id": "corr_<safe-id>",
  "idempotency_key": "idem_<safe-id>",
  "cursor": 42,
  "payload": {}
}
```

字段约束：

- `message_id`、`event_id`、`session_id`、`correlation_id`、`idempotency_key` 只能是有界安全引用。
- `cursor` 为单调递增的逻辑游标，不使用客户端时间排序替代。
- `payload` 必须通过消息类型专属 schema；未知字段拒绝。
- `sent_at` 只用于审计和过期判断，不作为顺序依据。
- 每个可重放消息必须能用 `idempotency_key` 去重。

### DeviceV1

```json
{
  "schema_version": 1,
  "device_id": "device_mac_1",
  "device_type": "brain|gateway|edge",
  "platform": "macos|linux|chrome",
  "client_version": "0.1.0",
  "capabilities": ["brain_state", "task_sync"],
  "trust_state": "pending|active|revoked",
  "last_seen_at": "..."
}
```

设备能力只描述通信/展示能力，不描述平台写权限。`revoked` 设备不能恢复未确认的任务。

### SessionV1

```json
{
  "schema_version": 1,
  "session_id": "session_edge_1",
  "device_id": "device_edge_1",
  "role": "brain|gateway|edge",
  "scope": {"workspace_id": "workspace_local_1"},
  "issued_at": "...",
  "expires_at": "...",
  "resume_cursor": 42,
  "state": "active|expired|revoked"
}
```

服务端只保存 token 的摘要或外部 secret-store 引用，不把 session token 原文写入日志、事件或队列。

### TaskV1

```json
{
  "schema_version": 1,
  "task_id": "task_<safe-id>",
  "kind": "review|sync|human_action|outcome",
  "status": "queued|assigned|acknowledged|in_progress|completed|blocked|cancelled",
  "priority": 0,
  "created_by": "device_<safe-id>",
  "assigned_to": "device_<safe-id>|null",
  "idempotency_key": "idem_<safe-id>",
  "payload": {},
  "can_execute": false
}
```

任务 payload 只能表达本地审阅、数据同步、人工动作记录或 Outcome 回填，不表达平台命令。状态转换必须由服务端校验；重复提交同一幂等键返回原任务。

### AlertV1

```json
{
  "schema_version": 1,
  "alert_id": "alert_<safe-id>",
  "severity": "info|warning|critical",
  "dedupe_key": "live:shop:product-click-rate",
  "status": "open|acknowledged|resolved|expired",
  "title": "需要人工复核",
  "evidence_refs": ["evidence_<safe-id>"],
  "created_at": "...",
  "expires_at": "..."
}
```

告警是通知和人工复核入口，不是执行授权。`critical` 也不能绕过 Safety。

### EventV1

事件是不可变的同步记录：

```json
{
  "schema_version": 1,
  "event_id": "evt_<safe-id>",
  "topic": "task.updated|alert.updated|state.updated|outcome.recorded",
  "aggregate_id": "task_<safe-id>",
  "aggregate_version": 2,
  "origin_device_id": "device_mac_1",
  "payload": {},
  "created_at": "..."
}
```

事件按 `topic + aggregate_id + aggregate_version` 保序；客户端可从 `cursor` 断点恢复，不能用本地时间覆盖服务端事件。

## WebSocket Protocol

连接建立后必须按以下顺序：

```text
CONNECT
→ HELLO(device, session/resume cursor)
→ WELCOME(session, current cursor, heartbeat interval)
→ EVENT / TASK / ALERT / ACK
↔ HEARTBEAT / HEARTBEAT_ACK
→ CLOSE(reason)
```

规则：

1. 未通过 device/session 校验的连接不能订阅主题。
2. 服务端先持久化事件和出站队列，再发送 `EVENT`。
3. 接收方先写入幂等记录，再返回 `ACK`；重复消息返回同一 ACK，不重复产生业务副作用。
4. 连接断开后客户端以 `resume_cursor` 重新连接，服务端按游标重放未确认事件。
5. 心跳超时只改变连接状态，不改变任务或告警状态。
6. Gateway 与 Brain 之间同样使用该协议，Gateway 不改写业务 payload。

## Offline Queue

离线队列采用 SQLite append-only 表，至少保存：

- `queue_id`
- `message_id`
- `idempotency_key`
- `destination`
- `payload_json`
- `created_at`
- `next_attempt_at`
- `attempt_count`
- `state`：`pending|in_flight|acked|dead_letter`
- `last_error_code`

语义：

- 至少一次投递；不承诺 exactly-once 网络传输。
- 业务去重由 `idempotency_key` 和服务端事件表共同保证。
- 同一 `aggregate_id` 的消息按 sequence 顺序发送；不同 aggregate 可并行。
- 失败使用有上限的指数退避；超过上限进入 `dead_letter`，不得静默丢弃。
- 重连先发送 resume cursor，再 flush queue，避免本地新消息覆盖服务端历史。
- 队列中只允许脱敏合同对象；写入前做 JSON、大小、敏感字段和 `can_execute=false` 校验。

## Security and Trust

- Gateway 使用 WSS；Mac 和 Edge 都主动出站连接。
- 每个 device 有独立身份和可撤销 session；不共享浏览器 Cookie。
- 日志只记录 `device_id`、`session_id` 的安全引用和错误码，不记录 token 原文或完整 payload。
- Brain、Gateway、Edge 都必须拒绝 `execution_allowed=true`、`can_execute=true` 或平台写字段。
- Edge 的人工动作只记录“已处理/忽略/理由/Outcome”，不会生成平台提交请求。

## Phase 2B Acceptance

编码后必须逐项验证：

- [x] `CentralEnvelopeV1`、`DeviceV1`、`SessionV1`、`TaskV1`、`AlertV1`、`EventV1` schema 校验和敏感字段拒绝
- [x] device 注册、session 建立、过期、撤销和 resume cursor
- [x] task/alert/event 状态转换、幂等去重和事件顺序
- [x] 本机 Brain ↔ Gateway ↔ Edge 的 WebSocket fixture 三节点闭环
- [x] 断线后事件和任务进入离线队列，重连后按 cursor/sequence 恢复
- [x] 重复消息不会重复产生任务、告警或人工动作
- [x] dead-letter 可查询且不会静默丢失
- [x] 全部测试中 `platform_write_attempted=false`、`can_execute=false`
- [x] 不读取、不提交、不记录真实 API Key、Token、Cookie、Secret、Session

## Phase 2B 首版实现

实现位于 `bridge/central_brain/`：

- `contracts.py`：六类版本化合同、边界大小、敏感字段和执行禁用校验。
- `identity.py`：内存设备注册表与可撤销 session，支持过期和 resume cursor。
- `events.py`：Brain 侧任务、告警、事件状态，按 aggregate version 保序并按幂等键重放。
- `offline_queue.py`：SQLite append-only 风格的 pending/in-flight/acked/dead-letter 队列。
- `websocket.py`：可注入 endpoint 的 asyncio WebSocket 适配边界；当前使用不开放端口的 fixture，覆盖 HELLO/WELCOME、转发、ACK、断线和 cursor 重放。

相关测试：

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest \
  test_central_brain_contracts \
  test_central_brain_identity \
  test_central_brain_events \
  test_central_brain_queue \
  test_central_brain_websocket
```

结果：`10/10` 通过。

本阶段实现的是本机可验证的传输适配器和协议闭环，不启动公网监听、不配置 WSS 证书、不连接云服务器，也不把 Gateway 变成业务事实源。`JsonWebSocketAdapter` 接受外部 WebSocket 客户端对象，因此下一阶段可在不改变合同的前提下接入独立的 WSS/TLS 库、设备认证存储、持久事件日志和运行监控。
