# Commerce Decision Center MVP Architecture

更新时间：2026-09-26

## Current Boundary

第一阶段只允许：

- `L0` 只读
- `A1` 影子决策
- `A2` 本机模拟

所有真实平台写能力关闭。预算、ROI、计划启停、商品价格、库存和上下架不会由本项目提交。

## Reused Upstream Layers

```text
Chrome MV3 extension
        |
        v
127.0.0.1 Bridge / http_receiver.py
        |
        +--> LocalStore / SQLite snapshots
        +--> RuleEngine
        +--> AI context and proposal validation
        +--> Action Protocol (proposal-only)
        +--> Shadow / A2 demo boundaries
```

本项目在上游已有能力之上增加最小的 `bridge/commerce_contracts.py`，不替换现有 Bridge、Side Panel、规则引擎或动作协议。

## Versioned Contracts

### `StateSnapshotV1`

统一当前状态和证据：

- `schema_version`
- `snapshot_id`
- `captured_at`
- `source`
- `source_quality`
- `scope.shop_id` / `scope.account_id`
- `entity_ids`
- `metrics`
- `evidence_hash`

缺失指标保留 `null`。证据哈希覆盖状态主体，篡改或重算不一致时拒绝解析。

### `DecisionProposalV2`

只表达结构化建议，不携带可执行脚本：

- `decision_id`
- `snapshot_id`
- `scope`
- `decision_type`
- `action`
- `value`
- `confidence`
- `reason_code`
- `policy_version`
- `evidence_hash`
- `expires_at`
- `can_execute=false`

首阶段 action space 仅允许 `OBSERVE`、`PROMPT_HOST`、`CHECK_PRODUCT`、`CHECK_CAMPAIGN`。

### `ActionRequestV1`

从 proposal 生成可审计的人工动作请求；它不是平台执行请求，固定 `can_execute=false`。

### `OutcomeRecordV1`

关联 `decision_id` 和 `action_id`，保存观察窗口、前后指标、可计算 delta 和来源。任一侧缺少指标时，该指标 delta 保持 `null`。

## Safety Rules

`validate_proposal_safety` 在本地返回显式阻断码：

- `PROPOSAL_EXPIRED`
- `SNAPSHOT_CHANGED`
- `EVIDENCE_HASH_MISMATCH`
- `SCOPE_MISMATCH`
- `ILLEGAL_ACTION`
- `EXECUTION_DISABLED`

校验通过只表示“可以进入人工审阅/影子记录”，不代表可以执行平台写操作。

## Step 2 Verification

```text
bridge/test_commerce_contracts.py
bridge/test_ai_decision.py
bridge/test_action_protocol.py
```

结果：`34/34` 通过。
