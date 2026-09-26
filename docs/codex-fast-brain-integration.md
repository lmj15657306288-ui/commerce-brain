# Codex Fast Brain Integration

更新时间：2026-09-26

## 范围

Step 6.5 将现有 Commerce Brain MCP 以追加方式注册到本机 Codex。注册不覆盖既有 MCP，不改变 `origin/main`，也不开放任何真实平台写入。

注册项：

```text
Name: commerce_brain
Command: /Users/linmengjiang/Projects/commerce-brain/.venv/bin/python
Args: /Users/linmengjiang/Projects/commerce-brain/scripts/run_commerce_brain_mcp.py
Transport: stdio
```

Codex 配置变更前已保存到本机备份目录：

```text
~/.codex/phase2-audit-backups/20260926/codex-config-before-commerce-brain-mcp.toml
~/.codex/phase2-audit-backups/20260926/codex-config-before-commerce-brain-mcp-launcher.toml
```

launcher 只在 MCP 子进程内存中读取本机 Laya key 文件，并通过
`COMMERCE_BRAIN_LAYA_API_KEY` 传给 `LayaClient`。凭证不写入仓库、Codex 配置、
日志或 MCP 响应；缺少凭证时仍可使用 rules fallback。

## 实测方法

工具注册后，使用 MCP stdio 会话列出工具并实际调用：

- `fast_choice`
- `fast_binary`
- `fast_score`
- `route_task`
- `classify_live_state`

测试输入只使用脱敏的 synthetic metrics 和 `LiveStateSnapshot` fixture。响应必须保持：

```json
{
  "mode": "proposal_only",
  "execution_allowed": false,
  "can_execute": false,
  "execution_performed": false
}
```

## 验证结果

### Laya 正常

- MCP 可发现 8 个工具，5 个 Fast Brain 工具均可实际调用。
- `fast_choice`、`fast_binary`、`fast_score`、`route_task` 使用 Laya 时：
  - `provider=laya`
  - `checkpoint=typed-decisions`
  - `execution_allowed=false`
  - `can_execute=false`
  - `execution_performed=false`
- `classify_live_state` 使用既有 rules 合同，不绕过本地校验。

### Laya 停止

停止本机 Laya 后重复调用同一组 synthetic 输入：

- `provider=rules`
- `checkpoint=fallback_from_laya`
- `execution_allowed=false`
- `can_execute=false`
- `execution_performed=false`

该结果表示明确的 provider fallback，不将 rules 结果伪装为 Laya 成功。Laya 恢复后，
`GET http://127.0.0.1:8765/health` 返回 200，loaded checkpoint 为
`typed-decisions`，设备为 `mps`。

## 回归

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest \
  test_mcp_fast_tools \
  test_mcp_ai_tools \
  test_decision_provider \
  test_live_state \
  test_mcp_laya_env
```

结果：`27/27` 通过。另已通过 `py_compile`、`git diff --check` 和仓库内敏感字段扫描。

## 边界

- MCP 只返回建议、路由和分类，不启动 Hermes，不调用浏览器，不提交平台写请求。
- 不读取或传输原始 HTML、完整 DOM、截图全文、订单明细、Cookie、Token、Secret 或 Session。
- 本文不记录任何真实 API key、token、cookie、secret 或 session；如日志需要展示凭证状态，只能使用 `<REDACTED>`。
