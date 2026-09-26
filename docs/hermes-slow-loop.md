# Hermes Slow Loop

更新时间：2026-09-26

Step 7 先建立本地合同和 adapter 边界，不修改已安装 Hermes，不写入 Hermes 配置，也不把 Hermes 放入 Fast Loop。

## SlowTask 合同

`bridge/hermes/contracts.py` 提供正式 `SlowTaskV1`，输入严格使用：

- `task_id`
- `task_type`：`DAILY_REVIEW`、`LIVE_REVIEW`、`PRODUCT_REVIEW` 或 `CAMPAIGN_REVIEW`
- `scope`：匿名 `shop_id` / `account_id` 等安全引用
- `period.from` / `period.to`
- `inputs.summary_metrics`、`decisions`、`outcomes`、`anomalies` 等脱敏聚合数据

兼容测试仍保留 `bridge/hermes/slow_task.py`，但新调用应使用正式合同。

原始 HTML、DOM、Cookie、Token、Secret、Session、脚本和执行字段会被拒绝。

## 五角色

| 角色 | 责任 |
| --- | --- |
| Coordinator | 选择下一步角色并收敛任务 |
| Data | 检查数据新鲜度、完整性和冲突 |
| Live Review | 复核直播状态和窗口指标 |
| Product | 复核商品侧风险 |
| Strategy | 形成策略草案和观察建议 |

`bridge/hermes/adapter.py` 只允许 `Coordinator → 一个子 Agent → Coordinator` 的有界顺序。五个角色固定为
`coordinator`、`data`、`live_review`、`product`、`strategy`，不会并行广播或自由群聊。

## 工具和策略生命周期

`bridge/hermes/tasks.py` 只声明 8 个读工具和 4 个本地草稿写工具。Hermes 可创建的 policy 生命周期永远是
`DRAFT`，不能生成 `ACTIVE`，也没有预算、ROI、启停、商品、库存或订单提交工具。

## 输出与安全

输出只能是 `DRAFT` 类型：`review`、`report`、`action_card`、`policy_draft`、`feature_suggestion`。

证据过期或质量低于 70 时，结果为 `blocked`，禁止总结。实际 Hermes 后端未连接时，结果为 `unavailable`，不伪造成功。所有结果固定：

```json
{
  "mode": "proposal_only",
  "execution_allowed": false,
  "can_execute": false,
  "execution_performed": false,
  "browser_action_allowed": false,
  "platform_write_attempted": false
}
```

`FixtureHermesBackend` 只用于本地合同测试和离线演练。当前 `UnavailableHermesBackend` 明确表示真实 Hermes 尚未接入；没有伪造成功。接入真实 Hermes 时必须实现同一 `HermesBackend` 接口，且仍然经过本地封装和 Safety。

## 验收

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest test_hermes_slow_task test_outcome test_shadow_loop
```

结果：正式合同和 adapter 测试通过；旧 `slow_task.py` 兼容测试也保留。当前没有修改 `~/.hermes/config.yaml`、没有启动新的 Hermes 会话，也没有把 Hermes 放入 Fast Loop。
