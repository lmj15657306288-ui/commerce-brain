# Laya / Rules Benchmark

更新时间：2026-09-26

## Provider Boundary

`bridge/decision_provider.py` 定义：

```python
class DecisionProvider:
    def decide(self, state, questions):
        ...
```

当前实现：

- `RulesProvider`：确定性本地规则，零网络依赖。
- `LayaProvider`：本地 Laya 适配边界；当前机器没有接入 Laya runtime，因此明确使用 `rules-fallback` checkpoint，不伪造本地模型已加载。

两者都只返回闭合结构：

- `choice`
- `binary`
- `score`
- `confidence`
- `reason_code`
- `provider`
- `model`
- `checkpoint`
- `state_hash`
- `latency_ms`

输出禁止 shell、Python、JavaScript、CSS selector、DOM selector 和任意执行代码。

## Fixture Run

执行：

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest test_decision_provider
```

结果：`5/5` 通过，其中包含 `100` 次非敏感 fixture 决策。

覆盖：

- ROI 风险触发 `CHECK_CAMPAIGN`
- 库存风险触发 `CHECK_PRODUCT`
- 缺失指标保持 `null` 并返回 `OBSERVE`
- Laya 本地 adapter 的 fallback 边界
- 非法 action 和执行字段拒绝

## 当前限制

这不是 Laya 模型性能基准，也没有宣称 Laya checkpoint 已在本机加载。它是第一阶段的本地 provider 合同与稳定性基线；接入真实 Laya runtime 前，必须继续保持 proposal-only、schema validation 和 `can_execute=false`。
