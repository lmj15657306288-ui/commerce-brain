# Phase 2B-4 Cloud Control Plane Foundation

更新时间：2026-09-27
阶段：Phase 2B-4，Production-ready Cloud Control Plane Foundation

## 1. 本阶段边界

本阶段把 Phase 2B-3 的 Control Plane 接到 production-like 基础设施：

- PostgreSQL：共享业务状态、Context Registry、Task/Alert/Approval、Event
  Store、幂等记录和持久 Lease 的事实源；
- Redis：WebSocket transient fanout、presence、heartbeat cache、短期 rate
  limit，故障时允许 degraded；
- EdDSA JWT：Bearer 验证、issuer/audience/时间/jti/actor/org/roles 检查；
- Docker Compose：Control Plane、PostgreSQL、Redis、可选反向代理；
- Caddy：HTTPS/WebSocket reverse proxy 模板；
- Mac Brain Worker client：只主动发起 HTTPS/sync 请求，持久化 cursor 并在
  断线后 replay。

本阶段没有进入 Browser Observer、Browser Runtime、Owner Mobile UI、Phase 2C，
也没有接入抖音/千川真实 API或任何平台写操作。`APPROVED` 仍不等于
`EXECUTED`。

## 2. 部署拓扑

```text
Internet
   │ 80/443 only
   ▼
Caddy / HTTPS WebSocket proxy
   ▼
FastAPI Control Plane (127.0.0.1:8840 diagnostics)
   ├── PostgreSQL  (authoritative truth)
   └── Redis       (ephemeral only)

Mac Brain Worker ── outbound HTTPS/WSS/sync ──► Control Plane
```

Shop、Cloud Server 和 Brain Worker 没有 1:1 绑定。一台 Mac Worker 可以服务多
个明确 scope 的 Brain Context。Mac 不需要公网 IP，Cloud 不反向连接 Mac。

生产 Compose 文件位于：

- `deploy/docker-compose.yml`
- `deploy/Dockerfile`
- `deploy/Caddyfile`
- `deploy/.env.example`
- `deploy/backup.sh`
- `deploy/restore.md`

`deploy/docker-compose.local-test.yml` 仅把 Control Plane 绑定到
`127.0.0.1:18000`，用于本机 smoke，不是公网部署配置。
生产 Compose 同时把 Control Plane 绑定到服务器回环地址
`127.0.0.1:8840`，仅供本机诊断；公网入口仍只有 Caddy 的 `80/443`。

## 3. PostgreSQL

`bridge/core/postgres_persistence.py` 实现现有 `PersistenceAdapter`，上层
`ContextRegistry`、Repository、Service 和 FastAPI endpoint 不直接 import
PostgreSQL driver。

Migration 由 adapter 在 fresh database 启动时执行，版本记录在
`schema_migrations`。当前版本为 `1`，重复启动只检查已应用版本，不重复创建
数据结构。

也可以通过容器内 CLI 显式检查/升级：

```bash
python -m control_plane.migrations current
python -m control_plane.migrations upgrade
```

两条命令都使用 `DATABASE_URL`，不会要求手动复制 SQL。

PostgreSQL 保存：

- registry records 和 external reference mapping；
- Task、Alert、Approval、Device、Worker、Lease；
- Event Store；
- idempotency records；
- audit/business event。

Event Store 使用 `cp_event_cursor_seq` 分配 monotonic cursor，并对
`event_id`、`idempotency_key` 做 unique 约束。`occurred_at`、`received_at`、
scope 字段有索引。没有使用 `MAX(cursor)+1` 生成新 cursor。

```text
occurred_at = 业务发生时间
received_at = Control Plane 接收时间
cursor      = 服务器同步顺序
```

Event Store 继续 append-only。更正通过追加 correction event，不更新历史
payload，也不删除历史。WebSocket 不是 source of truth，Event Store 是断线
恢复来源。

## 4. Redis

Redis 只用于：

- transient WebSocket fanout；
- device/worker presence；
- heartbeat cache；
- short-lived fixed-window rate limit。

Redis 不保存 Task、Alert、Approval、Event history、Finance 或 Knowledge
事实。Redis `PING` 或 publish 失败时，健康状态为 `degraded`，核心 PostgreSQL
读写仍继续；Redis client 禁用自动重试并使用有限超时，失败会快速回退，不会让
核心写请求无限等待。WebSocket fanout 也是 best-effort 异步操作，不会阻塞
PostgreSQL 事务或 API 响应。HTTP 限流和健康检查也在有界线程调用中执行，
超时即按 degraded 处理。不会先写 Redis 再假设稍后补回 PostgreSQL。

`/health` 会分别显示 PostgreSQL 和 Redis 状态。`/readiness` 要求 PostgreSQL
可用和生产 AuthProvider 已配置，Redis 是 optional degraded dependency。

## 5. Auth / Bootstrap / Device

生产服务使用 `JWTAuthProvider`，只接受：

```text
Authorization: Bearer <JWT>
alg: EdDSA
iss
aud
jti
actor_id
organization_id
roles
iat / issued_at
exp / expires_at
device_id optional
```

缺少 key、issuer/audience 不匹配、签名错误、过期、未来生效时间、身份字段
不完整时 fail closed。Token 不进入日志或错误响应。

带 `device_id` 的 JWT 还必须匹配 PostgreSQL 中 ACTIVE 且未过期的
`cp_device_sessions`。REVOKED 设备即使 JWT 尚未过期，也不能继续访问受保护
接口。

首个 Owner 不通过公开 HTTP endpoint 创建。使用显式 CLI：

```bash
cd deploy
docker compose --env-file .env exec control-plane \
  python -m control_plane.bootstrap_owner \
  --organization-id org_demo \
  --organization-name "Your Organization" \
  --actor-id actor_owner \
  --actor-role OWNER
```

CLI 只创建明确指定的 Organization、Actor、RoleAssignment、ScopeGrant，并追加
`security.bootstrap_owner` audit event；不会创建默认管理员、密码或公开 setup
路由。

## 6. 配置和 Secret

生产配置通过 environment 或 mounted secret：

```text
DATABASE_URL
REDIS_URL
JWT_PUBLIC_KEY / JWT_PUBLIC_KEY_PATH
JWT_ISSUER
JWT_AUDIENCE
CONTROL_PLANE_ENV
PUBLIC_BASE_URL
CORS_ALLOWED_ORIGINS
APP_VERSION
GIT_SHA
```

`deploy/.env.example` 只有示例值。真实 `.env` 不提交 Git。JWT 私钥不需要
部署到 Control Plane，只在签发端保存；Control Plane 只挂载公钥。Secret 文件
建议：

```bash
chmod 600 deploy/.env deploy/secrets/*.pem
```

未来可以迁移到 Vault 或 Cloud Secret Manager，本阶段不引入 Vault。

## 7. 网络、CORS、限流

生产 Compose 不公开 PostgreSQL、Redis 或 FastAPI backend 端口；只由 Caddy
暴露 `80/443`。PostgreSQL/Redis 位于 internal Docker network。

`CORS_ALLOWED_ORIGINS` 是明确 allowlist，不接受 `*`。WebSocket 也检查 Origin
allowlist。未配置 allowlist 时不会自动放行任意 Origin。

Redis-backed fixed-window rate limit 保护 `/sync`、事件、写接口、heartbeat、
device、worker 和 lease 路径。Redis 故障时限流进入 degraded 但不阻断事实写入；
仍由反向代理和云防火墙承担第一层流量边界。

## 8. Mac Brain Worker

客户端位于 `bridge/worker_client/`：

- `state.py`：`worker_id`、last ack cursor、server time 的 0600 原子 JSON；
- `client.py`：Bearer HTTPS transport、register、heartbeat、事件 replay；
- `sync.py`：断线后 register + heartbeat + cursor replay；
- `heartbeat.py`：可停止的周期 heartbeat loop。

默认要求 HTTPS；只有 `127.0.0.1` local smoke 显式允许 HTTP。Worker 使用指数
退避 `1s → 2s → 4s ...`，上限可配置；`run_forever()` 在传输失败时重连，
成功后按 heartbeat interval 工作，不进行高速无限重连。Worker 只报告能力，不获得
Laya/Hermes 高风险执行权限，也不运行任务 scheduler。

`POST /sync` 要求批次级 `idempotency_key`。同一个 key 和同一请求内容会返回
原同步结果；同一个 key 改变请求内容会返回 `IDEMPOTENCY_CONFLICT`。批次幂等
与事件自身的 `event_id` / `idempotency_key` 去重同时生效。

## 9. 健康、离线和恢复

```text
GET /health
GET /readiness
```

`/health` 表示进程仍可响应，并分别报告 database、Redis、Auth 配置。数据库
不可用时状态为 `degraded`，不会伪造最新 cursor。`/readiness` 在 PostgreSQL
不可用或生产 Auth 未配置时返回 `503`。

Mac 离线时：

- Task、Alert、Approval 和 Event history 仍从 PostgreSQL 可读；
- Worker 状态由服务端 heartbeat 计算为 DEGRADED/OFFLINE；
- 不删除业务状态；
- 不自动把业务 Task 标为 FAILED；
- 读取方必须展示 `last_seen_at`、freshness 和 worker offline。

## 10. Backup / Restore / Rollback

`deploy/backup.sh` 使用 `pg_dump -Fc`，默认固定使用 Compose project
`commerce-brain`（也可通过 `COMPOSE_PROJECT_NAME` 覆盖），备份文件权限为
`600`，默认保留最近 7 个 daily backup，并用 `pg_restore --list` 验证 archive
可读。完整恢复步骤在 `deploy/restore.md`，恢复测试必须使用隔离数据库，不直接
覆盖生产库。

镜像使用 `APP_VERSION` 和 `GIT_SHA`，不使用 `latest` 作为回滚依据。迁移当前
没有 destructive change；先部署可兼容的新 schema，再更新应用，可回滚旧镜像。

## 11. 本地 production-like smoke

准备 `.env`、JWT 公钥和仅本地 smoke 使用的私钥后：

```bash
cd deploy
cp .env.example .env
chmod 600 .env
# 生成 Ed25519 keypair，公钥放 deploy/secrets/jwt_public_key.pem
# 私钥仅放 deploy/secrets/jwt_private_key.pem，权限 600
./smoke_local.sh
```

smoke 会：

1. 启动 PostgreSQL、Redis 和 Control Plane；
2. 检查 `/readiness`；
3. 通过 CLI bootstrap 明确指定的 Owner；
4. Mac Worker register + heartbeat；
5. 读取初始 cursor；
6. 创建本地测试 Task；
7. Worker 断开语义下再次 replay cursor；
8. 验证 task event 到达且同一 cursor 不产生重复。

当前机器 Docker daemon 未运行，因此本轮没有声称这条 Docker smoke 已通过。

## 12. 当前实际部署状态

截至 2026-09-27：

- 已完成 `APP_VERSION=phase2b-4a-0d0993a` 基础镜像部署，并在生产验收修复后
  重新构建；最终运行时 `APP_VERSION` / `GIT_SHA` 以服务器 `deploy/.env`
  和容器 image label 为准；
- PostgreSQL、Redis、Control Plane、Caddy 使用独立 Compose 项目和持久卷；
- Caddy 已取得真实 ACME 证书，HTTP→HTTPS 和 WSS 均已验证；
- Mac Brain Worker 已完成注册、heartbeat、OFFLINE 判定和 cursor replay；
- 已完成 Redis/PostgreSQL failure drill、服务重启持久化、真实备份和隔离恢复测试；
- 服务器现有业务目录、容器、数据库、Redis、Cloudflare Tunnel、Nginx 业务配置未修改；
- 生产 JWT 私钥只保留在签发端 Mac，服务器仅挂载公钥；密码和 token 未写入 Git 或日志。

因此本记录对应真实生产验收与可审计的部署结果；真实平台写操作仍保持关闭。

## 13. Non-goals

本阶段禁止：

- Browser Observer / Browser Runtime；
- Platform Map / Hybrid Collector；
- Owner Mobile UI；
- Live Audio、Profit、Inventory、Customer、Creative、Opportunity Brain；
- 真实抖店/千川 API；
- 预算、ROI、广告启停、价格、库存、上下架、退款、客服消息或直播控制；
- 自动执行 APPROVED proposal。
