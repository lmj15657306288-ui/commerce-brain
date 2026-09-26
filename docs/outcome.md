# Outcome MVP

更新时间：2026-09-26

## Allowed Observation

影子 Action Card 只能在人工反馈后关联本地 Outcome：

- `30s`
- `2m`
- `5m`
- `30m`

来源只允许：

- `fixture`
- `manual`

Outcome 固定关联原始 `decision_id` 与 `action_id`，保存：

- `metrics_before`
- `metrics_after`
- `delta`
- `captured_at`
- `source`

缺失值不会补 `0`；无法比较的 delta 保持 `null`。

## Safety

Outcome 接口只更新本地 shadow record，不读取或修改平台，不触发浏览器点击，不调用旧的预算执行/preflight 路径。

## Verification

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest test_outcome test_shadow_cards_http
```

结果：`4/4` 通过。
