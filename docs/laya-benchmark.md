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
- `LayaProvider`：本地 Laya 适配边界；当前机器已接入 `127.0.0.1:8765` 的 typed-decisions checkpoint，失败时明确使用 `fallback_from_laya`，不伪造 Laya 成功。

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

结果：Step 4 provider 测试通过，Step 5 fixture contract 测试 `2/2` 通过。

覆盖：

- ROI 风险触发 `CHECK_CAMPAIGN`
- 库存风险触发 `CHECK_PRODUCT`
- 缺失指标保持 `null` 并返回 `OBSERVE`
- Laya 真实映射与 rules fallback 边界
- 非法 action 和执行字段拒绝

## Local Service Run

2026-09-26 在 Apple Silicon MPS、本机 `laya 0.3.20`、`typed-decisions` checkpoint 上完成：

| 次数 | service success | schema-valid | fallback | p50 | p95 | p99 | RSS | 吞吐 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 100% | 100% | 0% | 226.32ms | 260.67ms | 291.72ms | 86.73MB | 4.35 req/s |
| 1000 | 100% | 100% | 0% | 225.71ms | 263.23ms | 269.17ms | 82.47MB | 4.47 req/s |

独立预加载 cold start：`8670.37ms`。1000 次运行耗时 `223.52s`。报告中的 `accuracy_claim=false`，不代表业务准确率。

## 当前限制

这不是业务准确率评估。它是本机 provider 合同、HTTP 服务和 schema 稳定性基线；仍必须保持 proposal-only、schema validation 和 `can_execute=false`。
