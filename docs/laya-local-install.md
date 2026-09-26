# Laya Local Install

更新时间：2026-09-26  
状态：Step 2 已验证  
运行平台：Apple Silicon macOS

## Installation

Laya 不安装到仓库或 V1 `.venv`。当前隔离目录：

```text
~/.local/share/commerce-laya/
├── .venv/
├── config/api-key
└── logs/laya.log
```

已验证版本：

```text
laya 0.3.20
torch 2.14.0
transformers 5.17.0
fastapi 0.141.1
uvicorn 0.54.0
Python 3.12.13
```

安装方式：

```bash
python3.12 -m venv ~/.local/share/commerce-laya/.venv
~/.local/share/commerce-laya/.venv/bin/python -m pip install 'laya[serve]'
```

## Runtime Boundary

Laya 必须显式使用以下环境变量启动：

```bash
LAYA_HOST=127.0.0.1
LAYA_PORT=8765
LAYA_PRELOAD=1
LAYA_MODELS=typed-decisions
LAYA_DEVICE=mps
LAYA_THREADS=4
LAYA_API_KEY='<local ignored value>'
```

真实 key 只保存在：

```text
~/.local/share/commerce-laya/config/api-key
```

该文件权限为 `600`，不进入 Git、不写入日志、不返回浏览器、不发送给云模型。本文件只记录路径和协议，不记录 key 内容。

当前验证到的监听：

```text
127.0.0.1:8765
```

不得使用官方默认的 `0.0.0.0`，不得暴露 LAN。

## HTTP Contract

已从实际安装包源码和运行服务确认：

```text
GET  /health
POST /v1/systemone
```

推理请求使用：

```http
Authorization: Bearer <local-key>
Content-Type: application/json
```

输入至少包含：

```json
{
  "model": "typed-decisions",
  "state": {},
  "questions": {
    "next_action": {
      "type": "choice",
      "instructions": "Choose the safest next operational action.",
      "criteria": {
        "OBSERVE": "Continue monitoring.",
        "PROMPT_HOST": "Host messaging needs intervention.",
        "CHECK_PRODUCT": "Product/card performance needs operator review.",
        "CHECK_CAMPAIGN": "Paid traffic needs operator review.",
        "ESCALATE_SLOW_BRAIN": "Ambiguous or complex; request slow analysis."
      }
    }
  }
}
```

服务返回实际为 Jev/Laya 结构：

```json
{
  "model": "...",
  "answers": {},
  "routing": {},
  "usage": {}
}
```

Commerce Brain 必须在本地把 `answers` 映射到有限 action space，再通过既有 `validate_decision_result` 校验；不能把 Laya 原始响应直接视为 `DecisionProposalV2`。

## Verification

已通过：

- `import laya`，版本 `0.3.20`
- `GET http://127.0.0.1:8765/health` 返回 `200`
- 预加载健康返回 `loaded=["typed-decisions"]`
- 错误 Bearer 在 `/v1/systemone` 返回 `401`
- 畸形 JSON 返回 `400`
- 真实 `/v1/systemone` 返回 `200`
- 实际响应包含 `answers`、`model`、`routing`、`usage`
- MPS 可用，推理返回有限 choice `CHECK_PRODUCT`
- 监听地址为 `127.0.0.1:8765`
- `bridge/laya_client.py` 已通过 7/7 单测，并完成一次真实服务调用
- `LayaProvider` 已完成一次真实映射调用，结果为有限 action `CHECK_PRODUCT`，metadata 标记为 `provider=laya`、`checkpoint=typed-decisions`

## Commerce Brain Adapter

`bridge/decision_provider.py` 只向 Laya发送归一化 scalar features 和三类 typed questions：

- `next_action`：有限 choice
- `actionable`：binary / noul
- `priority`：有限 score

Laya 结果必须通过本地 action-space、confidence 和 `validate_decision_result` 校验。任何失败都回退：

```json
{
  "provider": "rules",
  "model": "deterministic-rules-v1",
  "checkpoint": "fallback_from_laya"
}
```

这条回退路径不伪装成 Laya 成功，也不开放平台写动作。

当前服务以受控前台进程验证；LaunchAgent、启动/停止脚本属于后续 Step 13，尚未宣称完成。

## Safety

Step 2 不连接抖店、巨量千川或任何真实平台账号，不读取浏览器 Cookie，不执行平台写入。Laya 只接收后续 Commerce Brain 生成的归一化 state/features 和有限 questions；不得发送完整 DOM、HTML、截图全文、Cookie、Token、订单明细或用户个人数据。
