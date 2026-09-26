# Mac MVP Acceptance

更新时间：2026-09-26  
分支：`codex/commerce-brain-mvp`

## Scope

第一阶段严格限定为：

- `L0` 只读
- `A1` 影子决策
- `A2` 本机模拟/合成 fixture

禁止真实修改千川预算、ROI、计划启停、商品价格、库存和上下架。

## Acceptance Matrix

| 验收项 | 状态 | 证据 |
|---|---|---|
| Mac 环境报告 | 通过 | `docs/environment-report.md` |
| 上游独立分支 | 通过 | `codex/commerce-brain-mvp` |
| 上游 macOS Agent 自检 | 通过 | `docs/upstream-baseline.md` |
| Extension ↔ localhost 健康接口 | 通过 | `/health/live` 返回 `status=ok` |
| StateSnapshotV1 | 通过 | `test_commerce_contracts.py` |
| DecisionProposalV2 | 通过 | `test_commerce_contracts.py` |
| ActionRequestV1 | 通过 | `test_commerce_contracts.py` |
| OutcomeRecordV1 | 通过 | `test_outcome.py` |
| stale proposal 拒绝 | 通过 | `PROPOSAL_EXPIRED` |
| snapshot 变化拒绝 | 通过 | `SNAPSHOT_CHANGED` |
| evidence hash 不一致拒绝 | 通过 | `EVIDENCE_HASH_MISMATCH` |
| shop/account scope 不一致拒绝 | 通过 | `SCOPE_MISMATCH` |
| 非法 action 拒绝 | 通过 | `ILLEGAL_ACTION` |
| 缺失值不补 0 | 通过 | contracts/live/outcome tests |
| RulesProvider | 通过 | `test_decision_provider.py` |
| Laya provider 接口 | 通过 | 本地 `rules-fallback`，未伪造模型已加载 |
| 100 次 fixture 决策 | 通过 | `test_decision_provider.py` |
| Shadow loop 持久化 | 通过 | `test_shadow_loop.py` |
| Shadow Action Card 展示 | 通过 | Side Panel 及静态回归测试 |
| 人工确认/忽略记录 | 通过 | `/commerce/shadow-cards/human-action` |
| Outcome 本地回填 | 通过 | `/commerce/shadow-cards/outcome` |
| 真实平台写动作 | 关闭 | 全部新 MVP 记录固定 `can_execute=false` |
| 真实凭证输出 | 通过 | `.env.example` 只含假值；测试无真实凭证 |
| 全量 Python 测试 | 部分通过 | 784 项中 776 通过、5 跳过、3 个已记录上游/macOS 基线失败；新增 MVP 测试全部通过 |
| 全量 Extension 测试 | 通过 | 77/77 通过；新增关键 Side Panel 回归也通过 |

## Full Test Commands

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest discover -s . -p 'test_*.py'

cd /Users/linmengjiang/Projects/commerce-brain
for f in extension/test-*.js; do node "$f"; done
```

## Known Limits

1. 当前没有真实 Laya runtime；`LayaProvider` 使用明确标记的本地 `rules-fallback`。
2. 当前没有接入真实直播数据；LiveState 只接受声明为 synthetic 的 fixture adapter。
3. Python 上游基线有 3 个已记录失败，分别是 macOS `/private/var` 路径别名 2 项，以及 `program_update_mode` 测试预期与 macOS 当前发布策略不一致 1 项。
4. Docker daemon 未运行；本 MVP 不依赖 Docker。
5. Chrome 已安装，Edge 未安装；当前没有进行浏览器 GUI 自动化验收。

## Overall Result

本地第一阶段闭环已跑通：

```text
StateSnapshot
→ Rules/Laya boundary
→ DecisionProposalV2
→ Safety
→ Shadow
→ Action Card
→ Human Action
→ Outcome
```

真实平台写能力保持关闭。上游 3 个基线失败未被掩盖，不能宣称全量测试全绿。
