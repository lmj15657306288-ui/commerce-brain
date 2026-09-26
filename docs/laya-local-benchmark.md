# Laya Local Benchmark

更新时间：2026-09-26  
状态：Step 5 已完成

## Scope

该 benchmark 只衡量本机 Laya 服务的工程可靠性：

- service success rate
- mapped provider/schema-valid rate
- fallback rate
- p50 / p95 / p99 latency
- cold start / first warm request
- resident memory
- throughput

它不衡量业务准确率，不把 synthetic fixture 的 choice 当成经营结论。

## Fixtures

目录：

```text
tests/fixtures/laya/
```

当前包含：

```text
stable_live
traffic_up_ctr_down
roi_falling
high_cvr_low_traffic
missing_metrics
stale_state
extreme_values
mixed_cn_en
ambiguous
inventory_risk
```

fixture 只包含归一化状态特征，不包含网页 HTML、Cookie、Token、订单明细或真实身份。

## Commands

先确保 Laya 以 `127.0.0.1:8765` 运行，再执行：

```bash
cd /Users/linmengjiang/Projects/commerce-brain
PYTHONPATH=bridge .venv/bin/python scripts/benchmark_laya.py --count 100 \
  --output /tmp/commerce-brain-laya-100.json

PYTHONPATH=bridge .venv/bin/python scripts/benchmark_laya.py --count 1000 \
  --output /tmp/commerce-brain-laya-1000.json
```

脚本默认会在独立的 `127.0.0.1:18766` 进程上测一次预加载 cold start；使用 `--skip-cold-start` 可跳过。报告不会写入 API key。

## Acceptance

- [x] 至少 10 组 synthetic fixtures
- [x] 100 / 1000 warm request 参数
- [x] success / schema / fallback / latency / RAM / cold-warm 指标
- [x] benchmark 明确不代表业务准确率
- [x] 在当前运行服务上完成 100 次报告
- [x] 在当前运行服务上完成 1000 次报告

## Verified Results

2026-09-26，Apple Silicon MPS，`laya 0.3.20`，`typed-decisions`：

| 次数 | service success | schema-valid | fallback | p50 | p95 | p99 | RSS | 吞吐 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 100% | 100% | 0% | 226.32ms | 260.67ms | 291.72ms | 86.73MB | 4.35 req/s |
| 1000 | 100% | 100% | 0% | 225.71ms | 263.23ms | 269.17ms | 82.47MB | 4.47 req/s |

独立预加载 cold start：`8670.37ms`；1000 次运行耗时 `223.52s`。该结果只证明本机链路可靠性，不证明业务准确率。
