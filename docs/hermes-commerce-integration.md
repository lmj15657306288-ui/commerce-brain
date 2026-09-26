# Hermes Commerce Brain Integration

更新时间：2026-09-26

## 审计结果

- Hermes：`v0.19.1+35934.gd0288be`
- 安装方式：已有 git checkout，未重装、未覆盖 `~/.hermes`
- Codex：`0.158.0-alpha.2.1`
- `~/.hermes/config.yaml` 与 `~/.codex/config.toml` 已存在；仅做本机备份和脱敏读取，没有写入新配置。

## 集成边界

Commerce Brain 通过 `bridge/hermes/adapter.py` 定义后端接口，但本阶段不直接启动 Hermes CLI 或修改 Hermes runtime。默认后端为
`UnavailableHermesBackend`，因此“未连接”会明确返回 `UNAVAILABLE`，不会伪造 Slow Brain 成功。

本地合同：

```text
SlowTaskV1
  -> HermesCoordinator
  -> Coordinator
  -> one of Data / Live Review / Product / Strategy
  -> SlowResultV1
  -> human review
```

Fast Loop 仍然只走 Commerce Brain MCP、Laya/Rules、Safety 和 Shadow；Hermes 不参与秒级或实时直播决策。

## 允许能力

- 读取脱敏店铺、直播、商品、计划、决策、Outcome、数据质量和实验摘要。
- 创建本地 `review_report`、`action_card`、`policy_draft`、`feature_suggestion` 草稿。
- 任何 policy 只能进入 `DRAFT`，必须人工审阅后才可进入后续生命周期。

## 明确禁止

- 预算、ROI、计划启停、商品价格、库存、上下架、删除、订单提交。
- 浏览器点击、任意 JS、shell、Python、selector、Cookie、Token、Secret、Session。
- 自动将 policy 变为 `ACTIVE`。
- Hermes 与 Codex 互相递归委派。

## 验收

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest \
  test_hermes_contracts \
  test_hermes_adapter \
  test_hermes_slow_task
```

当前实现使用 fixture/unavailable adapter，不声称真实 Hermes 已连接。运行测试结果和提交记录以当前仓库日志为准。
