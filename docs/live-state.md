# LiveState MVP

更新时间：2026-09-26

## Adapter Boundary

`bridge/live_state.py` 只定义读取合同和合成 fixture adapter：

- `online_users`
- `enter_rate`
- `leave_rate`
- `product_click_rate`
- `orders`
- `gmv`
- `ad_spend`
- `roi`

支持窗口：`30s`、`2m`、`5m`。当前 adapter 必须显式返回 `synthetic=true`；未接入真实平台连接，不伪造实时数据。

缺失指标不会补 `0`。部分样本缺失时仅对已有值聚合；全部缺失时保持 `null`。

## First Action Space

规则映射只返回：

- `OBSERVE`
- `PROMPT_HOST`
- `CHECK_PRODUCT`
- `CHECK_CAMPAIGN`

不包含预算、ROI、计划启停、商品价格、库存或上下架写动作。

## Verification

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest test_live_state test_decision_provider
```

结果：`6/6` 通过；Fast Brain MCP 与 provider 相关组合测试 `25/25` 通过，详见 `docs/mcp-fast-tools.md`。
