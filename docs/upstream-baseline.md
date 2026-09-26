# Upstream Baseline

探测日期：2026-09-26  
上游仓库：`https://github.com/3132882323-cyber/douyin-agent`  
基线提交：`06784b411739b619d762efa3ef6b89aa3709e38b`  
工作分支：`codex/commerce-brain-mvp`

## Step 1 验收条件

- [x] 在 `~/Projects/commerce-brain` 独立目录克隆上游仓库。
- [x] 不直接修改上游 `main`，已创建独立分支 `codex/commerce-brain-mvp`。
- [x] 阅读 `LICENSE`、`README.md`、`README-MAC.md`、`PRODUCT.md`、`SECURITY.md`。
- [x] 找到现有 Bridge、浏览器扩展、规则、Action Protocol、影子/模拟能力和测试入口。
- [x] 创建项目隔离 Python 环境 `.venv`，使用本机已有 Apple Silicon Python 3.11。
- [x] 安装 `bridge/requirements.txt` 中的锁定依赖。
- [x] 运行 Python Bridge 基线测试。
- [x] 运行 Chrome 扩展 Node 测试。
- [x] 验证 macOS 本地 Agent 自检。
- [x] 验证 loopback Agent 健康接口。
- [x] 保持测试和验证使用非敏感/隔离数据，不执行平台写操作。

## 发现的可复用能力

- `bridge/http_receiver.py`：本地 loopback Agent、快照接收、健康探针、读写边界和本地状态接口。
- `bridge/local_store.py`：SQLite 权威快照存储与 JSON 兼容迁移。
- `bridge/rule_engine.py`：本地确定性经营规则。
- `bridge/ai_decision.py`、`bridge/ai_context.py`、`bridge/ai_provider.py`：脱敏 AI 上下文、结构化提案和 provider 适配边界。
- `bridge/action_protocol.py`：动作草稿、状态转换和自动化就绪检查。
- `bridge/chengfang_demo.py`、`bridge/promotion_mode.py`：A2 本机模拟及投放模式边界。
- `extension/sidepanel.*`、`extension/background.js`、`extension/content-*.js`：Chrome MV3 Side Panel、Bridge 连接、页面采集和回读路径。
- `bridge/test_*.py`、`extension/test-*.js`：现有安全、存储、AI、动作、回读、扩展 UI 和恢复测试。

## 验证结果

### Python Bridge

执行：

```bash
cd /Users/linmengjiang/Projects/commerce-brain/bridge
../.venv/bin/python -m unittest discover -s . -p 'test_*.py'
```

Step 1 初始基线结果：`759` 项运行，`756` 通过，`5` 跳过，`3` 失败。

失败均来自上游基线与当前 macOS/测试约定的不一致，没有修改以掩盖：

1. `test_initialize_creates_separated_layout_and_schema`：macOS 临时目录通过 `/private/var` 解析，而断言使用 `/var` 词法路径。
2. `test_legacy_migration_preserves_existing_sqlite_path`：同一 `/var` 与 `/private/var` 别名导致已有 SQLite 路径被识别为不同路径。
3. `test_system_status_is_ready_without_ai`：测试预期 `offline_bundle`，但 macOS 代码按 `README-MAC.md` 的当前发布策略返回 `manual_reinstall`。

这些失败不阻塞当前本地 Agent 启动；后续如触及 `LocalStore` 或系统状态，可将它们作为明确的 macOS 兼容性修复项处理。Step 2-8 新增合同、provider、shadow、LiveState、Action Card 和 Outcome 测试均通过。

### Extension

执行：逐个运行 `node extension/test-*.js`。  
结果：`77/77` 通过。

### macOS Agent

使用隔离临时数据目录运行：

```bash
DIAN_AGENT_DATA_DIR=<temporary-directory> \
DIAN_AGENT_SELF_TEST=1 \
../.venv/bin/python http_receiver.py
```

结果：通过。输出确认：

- SQLite schema：`4`
- knowledge pack：`2026.08.02.1`
- Agent 自检成功

随后以 `BRIDGE_PORT=18765` 启动隔离 Agent，并访问：

```text
http://127.0.0.1:18765/health/live
```

结果：通过，返回 `status=ok`、`platform=macos`、`architecture=arm64`、`mode=local`；启动日志确认“方案确认模式（不执行千川操作）”。

## Step 1 结论

Step 1 的仓库、分支、依赖、扩展测试和本地 Agent 验证通过。Python 基线仍有 3 个已记录的上游/macOS 测试失败，后续进入 Step 2 前需要保持这些问题可追踪，不得将失败报告为全绿。

## 安全边界

- 未读取或输出真实 API Key、Token、Cookie、Secret、Session。
- 未启动真实平台登录流程。
- 未执行千川预算、ROI、启停、商品价格、库存或上下架写操作。
- Docker daemon 未启动，因此没有执行任何容器操作。
