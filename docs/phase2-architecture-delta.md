# Phase 2 Architecture Delta

更新时间：2026-09-26  
分支：`codex/phase2-laya-hermes-browser`  
V1 基线：`v0.1.0-v1-baseline` (`f654bc7`)  
目标：在不破坏 V1、不开大真实平台写权限的前提下，增量接入本地 Laya、Hermes Slow Loop 和共享 Browser Runtime。

## Audit Evidence

### Repository

- 工作副本：`/Users/linmengjiang/Projects/commerce-brain`
- `origin`：`3132882323-cyber/douyin-agent`
- `personal`：`lmj15657306288-ui/commerce-brain`
- 当前分支：`codex/phase2-laya-hermes-browser`
- V1 工作树在审计前干净；Phase 2 分支从 `f654bc7` 创建。
- 已建立本地 tag：`v0.1.0-v1-baseline`。

### Mac

| 项目 | 已确认 |
| --- | --- |
| macOS | 15.1 (24B83) |
| 架构 | `arm64` |
| 系统 Python | 3.9.6，不兼容当前 V1 的 `dataclass(slots=True)` |
| 项目 Python | `.venv/bin/python`，3.11.15 |
| 可用 Python | 3.11.15、3.12.13 |
| Node.js | v22.22.3 |
| Git | 2.39.5 |
| Docker | CLI 已安装；daemon 未启动 |
| Chrome | 已安装；Edge 未发现 |

V1 的正确运行入口是项目 `.venv`。在该运行时复核到 784 项 Python 测试中 781 项通过、5 项跳过、3 项既有失败；失败是 macOS 临时目录 `/var` 与 `/private/var` 别名的 2 项，以及系统状态中 `manual_reinstall` 与旧测试期望 `offline_bundle` 的 1 项。Phase 2 不把这些既有失败伪装成全绿，也不在本阶段顺手改动 V1。

### Codex

- Codex CLI：`0.158.0-alpha.2.1`
- 现有 MCP 配置已检查，包含 `node_repl`、`cua_repl`、影策插件和 Codex 内置工具。
- 当前没有 Commerce Brain MCP 注册。
- `~/.codex/config.toml` 已备份到：
  `/Users/linmengjiang/.codex/phase2-audit-backups/20260926/codex-config.toml`
- Phase 2 不删除、不重排、不覆盖现有 MCP 配置；后续只新增独立 Commerce Brain server。

### Hermes

- Hermes Agent：`0.19.1+35934.gd0288be`，安装方式为已有 git checkout。
- 安装目录：`/Users/linmengjiang/.hermes/hermes-agent`
- 当前 Hermes desktop、gateway 均在运行；gateway 由 launchd 管理。
- 当前 Hermes 配置使用 `custom:grok`，terminal backend 为 `local`，sudo 关闭。
- Hermes 已有浏览器工具族，包括导航、快照、点击、输入、滚动、返回和按键；源码还存在 CDP/browser runtime 路径。
- Hermes 当前没有被重装、卸载、迁移或清空。
- `~/.hermes/config.yaml` 已备份到：
  `/Users/linmengjiang/.codex/phase2-audit-backups/20260926/hermes-config.yaml`
- Hermes 不进入 Fast Loop；Slow Loop 先采用现有 default/auto runtime。Codex app-server 只作为工程修改运行时，不设计 Hermes↔Codex 循环编排。

## KEEP

### V1 contracts and safety

保留并作为 Phase 2 的唯一业务合同基础：

- `bridge/commerce_contracts.py`
  - `StateSnapshotV1`
  - `DecisionProposalV2`
  - `ActionRequestV1`
  - `OutcomeRecordV1`
  - evidence hash、scope binding、proposal TTL
  - `can_execute=False`
- `bridge/decision_provider.py`
  - `DecisionProvider`
  - `RulesProvider`
  - `validate_decision_result`
  - executable field deny-list
- `bridge/shadow_loop.py`
  - proposal-only shadow flow
  - human action / outcome audit envelope
- `bridge/rule_engine.py`
- `bridge/local_store.py`
- `bridge/live_state.py` 的 fixture adapter 和 synthetic trust gate
- `bridge/ai_context.py`、`bridge/ai_decision.py`、`bridge/ai_gateway.py`、`bridge/ai_provider.py`
- Chromium MV3 Extension、现有 Bridge auth、OceanEngine / 抖店 / 千川只读适配边界
- 现有 MCP server 的 proposal-only AI tool exposure

### V1 user-facing flow

继续使用：

```text
StateSnapshot
→ Rules / Fast Provider
→ DecisionProposalV2
→ Safety
→ Shadow Action Card
→ Human Action
→ Outcome
```

Phase 2 的 Laya、Hermes 和 Browser Runtime 都只能接入这条链路，不能创建旁路执行链。

## EXTEND

### Fast Brain provider seam

在 `bridge/decision_provider.py` 保留上层调用关系，把 `LayaProvider` 的内部实现从明确标记的 rules fallback 扩展为：

```text
normalized state
→ LayaClient
→ schema mapping
→ validate_decision_result
→ fallback RulesProvider
```

失败必须可识别为 timeout、offline、malformed JSON、invalid action、invalid confidence 或 checkpoint error，并回退为真实 metadata：

```json
{
  "provider": "rules",
  "model": "deterministic-rules-v1",
  "checkpoint": "fallback_from_laya"
}
```

Rules 结果不得冒充 Laya 成功。

### Action space

在现有 `OBSERVE`、`PROMPT_HOST`、`CHECK_PRODUCT`、`CHECK_CAMPAIGN` 之外，仅增加：

```text
ESCALATE_SLOW_BRAIN
```

本阶段不增加预算、ROI、启停、价格、库存、上下架、删除或提交动作。

### Live State

保留 `FixtureLiveStateAdapter`，新增 source trust metadata、freshness、趋势 feature 和真实只读 adapter seam。旧 synthetic 安全检查不能删除；stale state 必须进入 `OBSERVE` 或 `BLOCK`，不能送入可行动作。

### Outcomes and learning

保留当前 30s、2m、5m、30m 兼容格式，增量支持多 horizon 并存和分析数据导出。旧数据读取必须兼容，不能覆盖既有 outcome。

### HTTP boundary

不先重写 `bridge/http_receiver.py`。新增 Phase 2 route module 时优先使用：

```text
bridge/routes/fast_brain.py
bridge/routes/hermes.py
bridge/routes/browser.py
```

旧 endpoint 维持原位置，只有新增功能需要时才接入。

## SPLIT LATER

以下事项记录为后续治理，不作为本阶段前置阻塞：

- 拆分 `bridge/http_receiver.py` 的 legacy route。
- 将 `local_store.py` 迁移到 Redis/Postgres。
- 把浏览器采集器与 Extension 进一步统一成单一 runtime。
- 多租户远程服务、云端队列和跨机器 Browser Runtime。
- Laya 业务准确率、校准和 fine-tune；本阶段 benchmark 只测服务可靠性。
- Hermes 10+ agent 群聊、自由协作和自动 policy promotion。

## NEW

按 GOAL 顺序新增，且每项单独测试和 commit：

1. `bridge/laya_client.py`
   - localhost HTTP health/systemone
   - timeout、latency、Bearer header、safe JSON parsing
   - 不包含业务规则、浏览器动作和 Proposal 构造
2. `bridge/provider_health.py`
   - provider health、latency percentiles、fallback/schema counters
   - 不保存 key、prompt 或完整 state
3. `tests/fixtures/laya/`、`scripts/benchmark_laya.py`
   - synthetic fixtures、100/1000 warm requests、cold/warm 指标
4. `bridge/mcp_fast_tools.py` 或现有 `bridge/server.py` 的最小扩展
   - `fast_choice`、`fast_binary`、`fast_score`、`route_task`、`classify_live_state`
5. `bridge/hermes/`
   - strict SlowTask contract
   - Coordinator、Data、Live Review、Product、Strategy 五角色最小设计
   - Hermes 只能生成 DRAFT review/report/action card/policy draft/feature suggestion
6. `bridge/browser_runtime/`
   - shared driver/snapshot/actions/safety/audit/MCP seam
   - 第一阶段仅 L0/L1；L3 永久默认 blocked
7. learning dataset exporter
   - 关联 provider/model/checkpoint/policy/human action/outcomes

## DO NOT TOUCH

- 不改 `origin` 上游 `main`。
- 不覆盖 `~/.codex/config.toml` 或 `~/.hermes/config.yaml`。
- 不重装、卸载、迁移、清空 Hermes，不删除 Hermes memory/session。
- 不执行真实账号重新授权。
- 不接收、打印、提交或记录真实 API Key、Token、Cookie、Secret、Session。
- 不把 Laya key、Hermes provider credential 或 Codex credential 写入仓库。
- 不打开任何真实平台生产写权限。
- 不增加 `SET_BUDGET`、`SET_ROI`、`PAUSE_CAMPAIGN`、`EDIT_PRODUCT`、`CHANGE_PRICE`、库存、上下架、删除、提交等动作。
- 不把原始网页 HTML、完整 DOM、Cookie、Token、截图全文、订单明细发送给 Laya/Hermes。
- 不把 Hermes 放入直播秒级 Fast Loop。
- 不用 Browser Runtime 绕过 Extension、Safety、proposal TTL、scope 或人工确认。
- 不把 benchmark 结果称为业务准确率。

## Step 0 / Step 1 Acceptance

- [x] 读取 `CODEX_GOAL_PHASE2.md`，按其顺序和边界执行。
- [x] 审计当前仓库、Mac、Codex、Hermes。
- [x] 建立 V1 tag `v0.1.0-v1-baseline`。
- [x] 创建独立分支 `codex/phase2-laya-hermes-browser`。
- [x] 备份 Codex 和 Hermes 配置到用户目录，未进入 Git。
- [x] 完成 Architecture Delta，明确 KEEP / EXTEND / SPLIT LATER / NEW / DO NOT TOUCH。
- [x] 复核 V1 测试基线并记录已知失败。
- [x] Laya 服务部署与真实路由探测。
- [ ] Fast Brain / Hermes / Browser Runtime 实现。

## Next Gate

Step 2 已确认 Laya 可安装，实际版本为 `0.3.20`，并在本机以 `127.0.0.1:8765` 启动。`/health` 和 `/v1/systemone` 已真实探测；认证、畸形 JSON 和预加载 checkpoint 均已验证。详见 `docs/laya-local-install.md`。

Step 3 已完成：`bridge/laya_client.py` 只负责 loopback HTTP、Bearer header、timeout、延迟、响应大小和 JSON object 校验；7/7 单测及一次真实本机 Laya 调用通过。

Step 4 已完成：`LayaProvider` 通过客户端调用真实 `/v1/systemone`，只映射到有限 action space，并对非法 action、低置信度、超时、离线和协议错误回退到真实标记的 Rules metadata。新增动作只有 `ESCALATE_SLOW_BRAIN`；29 项 Step 4 相关测试和一次真实 provider 集成调用通过。下一步进入 Step 5，建立 Laya fixture benchmark。

Step 5 已完成：10 组 synthetic fixtures、100/1000 warm requests 和独立 cold start 均已运行。1000 次 service success/schema-valid 均为 100%，fallback 为 0%，p50 225.71ms、p95 263.23ms、p99 269.17ms；benchmark 明确不作业务准确率声明。详见 `docs/laya-local-benchmark.md`。

Step 6 已完成：现有 MCP server 增量暴露 5 个 Fast Brain proposal-only 工具。工具只接受有界脱敏状态或 `LiveStateSnapshot` fixture，provider 失败时 fail closed，所有响应强制 `execution_allowed=false`、`can_execute=false`、`execution_performed=false`。`test_mcp_fast_tools`、`test_mcp_ai_tools`、`test_decision_provider`、`test_live_state` 共 `25/25` 通过，详见 `docs/mcp-fast-tools.md`。

Step 6.5 进入实现：Commerce Brain MCP 已追加注册到 Codex，真实调用和 Laya 在线/fallback 状态验证记录在 `docs/mcp-fast-tools.md`。Hermes SlowTask 草稿暂不作为本阶段完成项；按用户新指令暂停原 GOAL 的 Hermes 顺序，转入 Phase 2B Central Brain 设计。
