# Fast Brain MCP Tools

更新时间：2026-09-26

Step 6 在现有 `bridge/server.py` 上增量暴露 5 个 Fast Brain MCP 工具。实现位于 `bridge/mcp_fast_tools.py`，不创建独立 MCP 项目。

Step 6.5 通过 `scripts/run_commerce_brain_mcp.py` 以 stdio 方式注册到 Codex。launcher 只在子进程内存中读取本机 Laya key 文件；key 不写入 `~/.codex/config.toml`、日志或仓库。

## 工具

| 工具 | 作用 | 默认 provider |
| --- | --- | --- |
| `fast_choice` | 输出有限 action space 中的一个建议 | `laya` |
| `fast_binary` | 判断是否需要人工复核 | `laya` |
| `fast_score` | 输出 0 到 1 的复核优先级 | `laya` |
| `route_task` | 路由到 Fast Brain、Slow Brain 或人工环节 | `laya`（有状态时） |
| `classify_live_state` | 按 `LiveStateSnapshot` fixture 合同分类直播状态 | `rules` |

允许的 Fast Brain action 仍为：

- `OBSERVE`
- `PROMPT_HOST`
- `CHECK_PRODUCT`
- `CHECK_CAMPAIGN`
- `ESCALATE_SLOW_BRAIN`

## 安全边界

- MCP 白名单只包含原有 3 个 proposal-only AI 工具和上述 5 个 Fast Brain 工具。
- 输入只接受有界脱敏 metrics 和已验证的 `LiveStateSnapshot`。
- 原始 HTML、Cookie、Token、Secret、Session、脚本字段会被拒绝。
- provider 错误、输入越界和协议异常均 fail closed，不产生执行动作。
- 每个响应都强制包含：

```json
{
  "mode": "proposal_only",
  "execution_allowed": false,
  "can_execute": false,
  "execution_performed": false
}
```

- MCP 层不导出原始快照读取、平台写入、浏览器点击、授权、确认或执行工具。
- `route_task` 的 `slow_brain` 和 `human` 只是路由建议，不会启动 Hermes、浏览器或平台操作。

## 验收

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest \
  test_mcp_fast_tools \
  test_mcp_ai_tools \
  test_decision_provider \
  test_live_state
```

结果：`25/25` 通过。另已通过 `py_compile` 与 `git diff --check`。

## Codex Integration

注册命令：

```bash
codex mcp add commerce_brain -- \
  /Users/linmengjiang/Projects/commerce-brain/.venv/bin/python \
  /Users/linmengjiang/Projects/commerce-brain/scripts/run_commerce_brain_mcp.py
```

实际 stdio 调用已验证 8 个工具可见，其中 Fast Brain 5 个工具均可调用。Laya 在线时结果必须带 `provider=laya`、`checkpoint=typed-decisions`；Laya 停止时结果必须带 `provider=rules`、`checkpoint=fallback_from_laya`。两种状态都固定 `execution_allowed=false`、`can_execute=false`、`execution_performed=false`。
