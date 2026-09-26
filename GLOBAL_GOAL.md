# GLOBAL_GOAL.md
# Commerce Brain / Multi-Store Multi-Category Commerce Operating System

## 0. 最高目标

本项目不再定义为“单店抖音经营工具”。

最终目标是构建：

Multi-Store / Multi-Category Commerce Operating System

中文定位：

多店铺、多品类、多员工、多设备协同的 AI 电商经营操作系统。

系统需要支持：

- 多店铺
- 多品类
- 多品牌
- 多渠道
- 多员工
- 多设备
- 多 Brain Worker
- 老板手机端
- 实时直播辅助
- 商品经营
- 投放经营
- 客服经营
- 利润经营
- 库存/供应链
- 商机发现
- 创意生产
- 经营复盘
- 持续学习
- 可审计
- 可回滚
- 可恢复
- 可扩展到几十家店

项目不是为了做一个“万能聊天机器人”。

目标是：

把经营状态持续转化为：
State
→ Decision
→ Action
→ Outcome
→ Learning

并让系统越来越了解每个店、每个类目、每个商品和每类经营场景。

---

# 1. 核心架构原则

## 1.1 店铺不是一台机器

严格禁止将以下概念绑定成 1:1：

Shop
=
Mac
=
Cloud Server
=
Laya Instance
=
Hermes Instance

正确模型：

一套 Shared Brain Runtime
可以承载多个 Brain Context。

定义：

Brain Runtime
=
Laya
Hermes
GPT / Other LLM
Feature Engine
Knowledge Engine
Decision Engine
Safety Engine

Brain Context
=
某个 organization / category / shop / product 的：

- 数据
- 知识
- 策略
- 历史
- 规则
- 实验
- Outcome
- Prompt/Pattern
- Business State

系统必须支持：

1 Brain Runtime
→ N Brain Contexts

未来也支持：

N Brain Workers
→ 调度不同 Context

---

# 2. 多店、多品类 Scope 模型

所有核心业务对象必须支持明确 Scope。

最小层级：

Organization
→ Brand
→ Category
→ Shop
→ Channel
→ Product
→ SKU
→ Campaign / Live / Customer / Order

核心 ID：

organization_id
brand_id
category_id
shop_id
channel_id
product_id
sku_id

不是所有对象都必须填满所有层级，
但必须明确自身 scope。

---

# 3. 知识边界

系统必须坚持：

共享能力
≠
共享业务结论

不同店铺、不同品类的业务知识默认隔离。

Knowledge Scope：

global
category
brand
shop
product

默认知识检索优先级：

Product Knowledge
+
Shop Knowledge
+
Brand Knowledge
+
Category Knowledge
+
Global Knowledge

禁止默认读取其他 Shop 的私有经营知识。

跨店知识只能在明确的：
benchmark
portfolio analysis
cross-shop experiment
knowledge promotion

场景中使用。

---

# 4. Knowledge Promotion

某条经营规律不能因为在一家店有效，
就直接成为全局知识。

知识升级应该有层级：

shop
→ brand/category
→ global

升级必须考虑：

- evidence count
- sample size
- consistency
- category fit
- traffic context
- experiment quality
- confidence
- outcome stability

例如：

店A：
“羊毛大衣真人上身首镜效果好”

不能自动指导食品店。

最多可以抽象成：

“真实使用/展示证据可能提升信任”

然后由各 Category Pack 再解释。

---

# 5. Shared Core vs Category Pack

系统底层能力共享。

共享：

- Data Engine
- Feature Engine
- Decision Engine
- Experiment Engine
- Profit Engine Framework
- Inventory Engine Framework
- Task Engine
- Alert Engine
- Approval Engine
- Safety Engine
- Model Gateway
- Knowledge Engine
- Browser Runtime Framework
- Audit Engine

业务知识按 Category Pack 扩展。

例如：

category_packs/
  apparel/
  food/
  beauty/
  general/

Category Pack 可定义：

- business metrics
- customer intents
- product attributes
- creative patterns
- refund patterns
- opportunity features
- risk thresholds
- pricing considerations
- supply rules

---

# 6. 三层部署架构

## 6.1 Cloud Control Plane

Cloud 负责：

- Auth
- User
- Role
- Device Registry
- Session
- Task
- Alert
- Approval
- Event
- WebSocket / SSE
- Offline Queue
- Shared State
- API Gateway
- Snapshot
- System Health
- Brain Worker Registry

Cloud 不承担重型模型推理为第一目标。

当前 8C8G 云服务器主要承担控制平面。

---

## 6.2 Brain Worker

当前主要 Brain Worker：

Apple Silicon Mac

运行：

- Laya
- Hermes
- GPT / Model Gateway
- Feature Engine
- Knowledge Processing
- Slow Analysis
- Local Models
- Creative Planning
- Codex Engineering

未来允许新增：

Brain Worker 02
Brain Worker 03
Cloud Worker
GPU Worker

Shop 不与 Worker 绑定。

通过 Brain Scheduler 分配任务。

---

## 6.3 Edge Client

员工设备负责：

- Browser Extension
- Browser Observer
- Browser Runtime Connector
- Live Audio Agent
- Local Device Signal
- Local File/Export Handling

原则：

采集在边缘
判断在中央

Edge 是：

眼睛
耳朵
手

不是主脑。

---

# 7. Experience Layer

系统正式包含四个一等产品面：

Experience Layer
├── Employee Web Workbench
├── Browser Side Panel
├── Live Host Panel
└── Owner Mobile Cockpit

---

# 8. Employee Web Workbench

员工端不是“平台工具集合”。

目标是回答：

现在发生什么
为什么重要
下一步做什么
做到什么算完成

一级导航优先按工作目标，而不是数据来源：

首页
任务
商品
直播
投放
客服
异常

老板/负责人可额外看到：

利润
库存
商机
系统

不要把：
罗盘
千川
联盟
灰豚

当成一级产品结构。

它们是数据源，不是用户目标。

---

# 9. Owner Mobile Cockpit

老板需要一个正式手机端。

第一阶段：

Responsive PWA

不优先做：

- 原生 iOS
- 原生 Android
- 微信小程序

后续可封装。

目标：

老板在 30 秒内知道：

1. 全店群是否正常
2. 哪些事情需要拍板
3. 哪些异常影响利润/库存/直播
4. 哪些重要任务正在处理中
5. 各店核心状态
6. 哪些事项需要审批
7. 系统是否有异常节点

移动端不复制桌面工作台。

只展示：

Need Decision
Need Approval
Need Awareness

---

# 10. Owner Mobile MVP

必须包括：

## 10.1 多店总览

每店最少：

- health
- GMV
- estimated profit
- realized profit if available
- ad spend
- ROI
- refund risk
- inventory risk
- live status
- high priority issues

## 10.2 老板待办

只显示：

需要老板决定
需要老板批准
需要老板知晓

## 10.3 高优先级异常

每条异常：

what happened
why it matters
business impact
recommended next action
owner
status

## 10.4 审批队列

支持：

approve proposal
reject
request revision
assign
acknowledge

第一阶段禁止直接执行高风险平台动作。

## 10.5 直播简版

老板看：

- live status
- online
- GMV
- ROI
- current product
- AI live health
- latest important host guidance
- guidance acknowledged or not

不展示完整字幕流。

## 10.6 24h Summary

展示：

- biggest positive change
- biggest negative change
- completed important actions
- unresolved risks
- top 3 owner attention items

---

# 11. 通知系统

分级：

P0
立即推送

例如：

- profit floor breach
- severe stockout
- major live anomaly
- critical system offline
- high-risk approval

P1
短时间内推送

例如：

- campaign degradation
- important pricing proposal
- refund anomaly

P2
日报/汇总

例如：

- normal task complete
- mild CTR fluctuation
- low-risk suggestion

必须：

- deduplicate
- cooldown
- aggregate

避免通知泛滥。

---

# 12. System-1 First 原则

这是项目核心原则之一。

所有高频经营判断，
优先拆解成：

small
typed
bounded
classification / routing / scoring

问题。

如果问题可以压缩成：

2~20 个明确选项

优先交给：

Laya / JEV

如果问题需要：

开放式推理
多日历史
跨领域综合
复杂因果
策略分析

才升级：

Hermes / GPT

---

# 13. Laya / JEV 定位

Laya / JEV
=
Reflex Layer
=
System-1

不是长文本分析器。

典型输出：

HOLD
PROMPT_HOST
CHECK_PRODUCT
CHECK_CAMPAIGN
ESCALATE_SLOW_BRAIN

或者：

NORMAL
WATCH
RISK

或者：

EVIDENCE_NONE
EVIDENCE_WEAK
EVIDENCE_OK

所有实时动作优先 typed。

输出必须包含：

action/state
confidence
reason_code
ttl
scope

---

# 14. 并行 Sentinel 模型

未来系统应支持大量轻量 Sentinel。

例如：

Product Sentinel
Campaign Sentinel
Live Sentinel
Inventory Sentinel
Customer Risk Sentinel
Traffic Sentinel
Talk Sentinel

它们不是独立重型 Agent。

而是逻辑上的快速判定单元。

例如：

Product Sentinel 输入：

CTR
CVR
refund
inventory
profit
search
traffic

输出：

state
action
confidence
ttl

目标：

大量状态
↓
Laya/JEV 并行筛选
↓
少量异常候选
↓
Rules/Evidence
↓
真正重要 Case
↓
Hermes/GPT 深度分析

---

# 15. Hermes 定位

Hermes
=
Slow Brain / Agent OS

负责：

- daily review
- live review
- product review
- campaign review
- strategy analysis
- cross-domain synthesis
- policy draft
- feature suggestion
- data quality review

Hermes 不在实时秒级循环。

Hermes 默认：

proposal-only
draft-only

Policy 生命周期：

DRAFT
→ REVIEWED
→ SHADOW
→ APPROVED
→ ACTIVE
→ RETIRED

Hermes 只能生成 DRAFT。

---

# 16. Codex 定位

Codex
=
Engineering Agent

负责：

- coding
- testing
- migration
- integration
- documentation
- deployment
- repository maintenance

避免：

Hermes
→ Codex
→ Hermes
无限嵌套。

---

# 17. Rules / Safety 定位

Rules / Safety
=
Authoritative Constraint Layer

优先级高于：

Laya
Hermes
GPT

包括：

- cooldown
- max step
- min sample
- hysteresis
- profit floor
- inventory safety
- stale reject
- TTL
- approval requirement
- write permission
- shop scope
- risk gating

---

# 18. Data Plane

支持：

Official API
Push
Official Export
Browser Observer
Browser Runtime Read
Third-party
External Reach
Manual Import

---

# 19. 数据源可信度

原则：

Official API / Push
=
Fact Truth

Authorized Browser
=
High-frequency Perception

Official Export
=
Historical Verification

Third-party
=
Estimate / Benchmark

External Web
=
Market Signal

所有 metric 必须带：

source
source_type
captured_at
freshness
confidence
verified
shop_id
category_id

---

# 20. Data Quality Engine

Data Quality 必须是一等基础设施。

处理：

missing
stale
outlier
conflict
drift
duplication
time misalignment

多个来源冲突时：

生成 Canonical Metric。

Brain 默认消费 Canonical Metric，
而不是直接消费所有原始值。

---

# 21. Data Planner

每个 metric 定义：

preferred_source
verification_source
poll_interval
max_age
cost_class
required_for_execution

原则：

API 负责真相
浏览器负责感知

---

# 22. Storage Lifecycle

采用：

Collect
→ Compress
→ Distill
→ Learn
→ Forget

Raw Data：
短期保存

Normalized Fact：
中期/长期

Feature：
持续更新

Decision / Action / Outcome：
长期保留

Knowledge：
长期蒸馏

不要无限保存所有原始大文件。

---

# 23. 核心数据实体

重点表/对象：

raw_event
fact_order
fact_product
fact_live
fact_ads
fact_content

feature_snapshot

decision
action
outcome

policy
knowledge

task
alert
approval

其中最重要：

feature_snapshot
decision
action
outcome

---

# 24. Profit / Finance Truth

Profit Engine 在自动经营之前必须完成。

GPT 不负责计算利润真值。

使用确定性 Finance Fact + Profit Engine。

至少支持：

Order Estimated
Delivered Estimated
After-sales Adjusted
Final Settled Realized

生命周期：

ORDER_CREATED
→ PAID
→ SHIPPED
→ SIGNED
→ AFTERSALE_WINDOW
→ SETTLED

成本包含：

product cost
platform fee
payment fee
affiliate commission
ad cost
logistics
return logistics
refund loss
compensation
packaging
other variable cost

输出：

estimated_profit
realized_profit
margin
break_even_roi
safety_roi
target_roi
scale_floor_roi

---

# 25. Inventory / Supply Engine

必须是一等基础设施。

至少支持：

physical_stock
reserved_stock
available_to_promise
in_transit
return_pending
lead_time

输出：

days_of_supply
stockout_risk
overstock_risk
reorder_point

后续支持：

replenishment
allocation
forecast
supplier performance

---

# 26. Master Product

多店环境必须区分：

Master Product
vs
Store Listing

同一真实商品可能存在：

不同 Shop Product ID
不同 Channel Item ID
不同 Listing

但统一映射：

master_product_id

用于：

- 成本
- 库存
- 素材
- 跨店比较
- 供应链
- 商品知识

---

# 27. Live Intelligence

实时直播系统必须包含：

Live Audio Agent
Streaming ASR
TalkState
TrafficState
ProductState
AdState
State Fusion
Evidence Gate
Host Guidance

---

# 28. Live Audio

实时话术不能只依赖浏览器直播回放音频。

音频优先级：

主播麦克风直采
>
OBS/直播伴侣监听音轨
>
系统音频
>
浏览器直播回放音频

Live Audio Agent 运行在直播电脑 Edge。

---

# 29. Streaming ASR

实时阶段：

低延迟优先。

目标：

1~3 秒级 partial transcript

实时 ASR 可使用：

faster-whisper
whisper.cpp
其他 streaming ASR

直播结束后：

WhisperX

用于高精度：

word timestamp
alignment
diarization

---

# 30. TalkState

不要把长字幕一直丢给模型。

实时维护：

current_topic
last_30s_summary
last_2m_summary
covered_points
missing_points
repeat_score
talk_speed
CTA state
proof state

TalkState 是实时模型输入。

---

# 31. Evidence Gate

这是直播系统核心。

状态：

EVIDENCE_NONE
EVIDENCE_WEAK
EVIDENCE_OK

原则：

先判断当前流量是否足以评价话术，
再评价经营效果。

---

# 32. 话术评价拆分

主播评价必须拆成：

Content Quality

和

Business Effectiveness

Content Quality 可以在低流量时判断：

- 是否重复
- 是否漏卖点
- 是否无CTA
- 是否无证明
- 是否节奏拖

Business Effectiveness 必须有足够：

流量
曝光
点击
成交

证据才能判断。

---

# 33. 关键原则

没有足够流量证据时：

禁止写：

“主播话术效果差”

只能写：

“当前流量不足，无法判断经营效果”

这条原则必须长期保留。

---

# 34. Host Guidance

系统不应频繁给主播逐字稿。

只做：

1. 补缺失
2. 打断低效重复
3. 经营状态变化时切策略

示例：

补一次显瘦证明

价格权益 4 分钟未提

材质已重复 3 次

流量上涨，当前卖点继续 30 秒

---

# 35. Live Guidance Cooldown

避免主播被干扰。

支持：

cooldown
deduplicate
priority

P1：
立即提醒

P2：
下一个话术节点

P3：
只给中控，不打断主播

---

# 36. Browser Observer

负责：

- DOM
- XHR/JSON
- accessibility
- page state
- live metrics
- platform state

优先级：

structured DOM/data
→ XHR/JSON
→ accessibility
→ vision/screenshot

OCR 最后使用。

---

# 37. Browser Runtime

Browser Runtime 负责：

NAVIGATE
SNAPSHOT
CLICK
TYPE
PRESS
SCROLL
WAIT
BACK
VERIFY
DONE
BLOCKED

Browser Runtime 不等于 Browser Observer。

Observer
=
看

Runtime
=
做

---

# 38. Browser Runtime Safety

当前只允许：

read
navigate
filter
export
low-risk operations

禁止：

budget change
ROI change
pause ads
price update
inventory write
submit high-risk forms

所有 Browser Action 必须审计。

---

# 39. Audit

记录：

session
actor
tool
origin
page hash
action
target
risk
approval
timestamp
result
verification

禁止记录：

password
secret
raw auth token

---

# 40. Automation Layer

执行层必须可插拔。

Automation Router：

Official API Executor
Browser Runtime
Desktop RPA Worker
Human Task

原则：

有 API
→ API

无 API但网页可结构化操作
→ Browser Runtime

必须操作桌面软件
→ RPA

高风险/异常
→ Human

---

# 41. RPA 定位

RPA / 影刀不是核心大脑。

定位：

Legacy / Desktop Automation Adapter

适合：

- Excel
- 本地ERP
- Windows客户端
- 文件管理
- 定时报表导出
- 打印
- 无API老系统

不负责：

核心财务真相
实时高频直播
高风险自主决策

系统不得绑定影刀品牌。

RPA 必须可替换。

---

# 42. Customer Brain

基础意图分两层：

General Intent

例如：

ORDER_STATUS
LOGISTICS
REFUND
AFTERSALE

Category Intent

例如服装：

SIZE
FIT
FABRIC
CARE

食品：

INGREDIENT
ALLERGEN
SHELF_LIFE
STORAGE

低风险可逐步自动。

高风险始终人工：

退款
赔偿
地址修改
已发货取消
法律争议
平台投诉
高金额
疑似欺诈

---

# 43. Product Brain

负责：

- 商品健康
- CTR/CVR
- refund
- inventory
- price
- listing
- title
- main image
- detail page
- product experiment

标题、主图、详情页可以半自动生成。

原则：

Brain 判断是否值得改
→ 生成候选
→ 人工确认
→ 实验
→ Outcome

不高频自动改线上商品。

---

# 44. Creative Brain

长期目标：

真人素材优先
+
AI 补充

不追求全 AI。

但自动剪辑当前不是优先任务。

Creative Brain 未来负责：

benchmark
pattern
creative brief
prompt compiler
experiment
outcome

---

# 45. Opportunity Brain

输入：

search growth
competitor growth
refund pain
customer demand
market trend
margin
supply capability
content capability

输出：

opportunity
why
audience
price
design
risk
validation plan

不同 Category 使用不同 Category Pack。

---

# 46. Pricing

未来支持：

minimum sustainable price
normal price
campaign price
affiliate price
clearance price

必须结合：

profit
refund
commission
ads
market
inventory

---

# 47. Demand Forecast

未来支持：

product demand
SKU
color
size
seasonality
promotion

Opportunity 不只回答：

做什么

还要回答：

做多少

---

# 48. Portfolio Brain

老板级跨店分析。

可以跨店比较：

profit
cash efficiency
inventory risk
ads efficiency
execution status
system health

但禁止简单把一个品类的经营策略复制给另一个品类。

Portfolio Brain 负责：

resource allocation
cross-shop comparison
executive planning

---

# 49. Experiment Engine

所有重要修改尽量进入 Experiment。

记录：

baseline
treatment
traffic context
confounders
minimum sample
confidence
outcome

避免：

“改了主图后CTR涨了”
就直接认为是因果。

---

# 50. Model / Prompt / Policy Version

所有 Decision 长期记录：

model
model_version
prompt_version
feature_version
policy_version

保证：

可追溯
可比较
可回滚

---

# 51. Identity / RBAC

角色：

Owner
Manager
Operator
CustomerService
LiveControl
Logistics
Viewer

权限必须同时考虑：

organization
brand
shop
role

AI 也是 Actor。

例如：

actor=laya
actor=hermes
actor=employee_x

---

# 52. Task / Alert / Approval

必须是一等对象。

Task：

谁处理
截止时间
优先级
状态
Outcome

Alert：

问题
影响
证据
建议动作

Approval：

proposal
risk
scope
reason
decision

---

# 53. Owner Mobile Contracts

建议预留：

GET /owner/summary
GET /owner/tasks
GET /owner/alerts
GET /owner/approvals
GET /owner/live-status
GET /owner/shops
GET /owner/system-health

POST /owner/approvals/{id}/approve
POST /owner/approvals/{id}/reject
POST /owner/approvals/{id}/request-revision
POST /owner/tasks/{id}/ack
POST /owner/tasks/{id}/assign

---

# 54. System Health / Doctor

参考多工具项目的 doctor 模式。

未来：

commerce-brain doctor

检查：

Laya
Hermes
Cloud
Model Gateway
Browser Runtime
Canvas
Doudian
Compass
Qianchuan
Alliance
Third-party
External Reach
Database
Queue
Object Storage

---

# 55. Disaster Recovery

Mac 不能成为唯一单点。

必须考虑：

database backup
knowledge backup
policy backup
config backup
object storage backup

Mac offline 时：

Cloud 继续展示：

last known state
tasks
alerts
approvals

并明确：

last_updated
freshness
offline

不能伪装实时。

---

# 56. AI Cost Ledger

长期记录：

model cost
API cost
storage cost
creative cost
third-party cost

支持按：

Brain
Shop
Product
Task

查看成本。

---

# 57. Business Impact

重要任务/异常尽量带：

gmv_impact
profit_impact
inventory_impact
customer_impact
live_impact
confidence

老板端优先看影响，
不是看技术指标。

---

# 58. Action Priority

未来全店群问题很多。

Task 排序优先考虑：

Expected Impact
× Confidence
÷ Effort

目标：

不是给老板 200 个提醒。

而是：

今天最值得处理的 10 件事。

---

# 59. Data / Knowledge 学习闭环

长期核心：

State
→ Decision
→ Action
→ Outcome

External Teacher：

竞品
第三方
市场

Platform Intelligence：

罗盘
千川
联盟

Proprietary Truth：

自己的 State/Action/Outcome

长期：

Proprietary Knowledge 权重逐步提高。

---

# 60. 当前 Non-goals

当前阶段禁止：

真实预算修改
真实 ROI 修改
自动暂停广告
自动价格修改
自动库存修改
自动上下架
自动发客服消息
自动退款
自动跨店调拨
自动直播控制
自动批量商品修改
高风险自动执行

长期原则：

proposal-only
shadow-first
human approval
fail-closed

---

# 61. Roadmap

## Phase 2A
Fast Brain / Laya

包括：

Laya Client
Provider
Rules fallback
Fast Brain MCP
typed decision
proposal-only

状态：
已有基础成果

---

## Phase 2B
Multi-store Foundation

包括：

Multi-store Context
Scope model
Central Brain API
Cloud Control Plane
Device Session
Task
Alert
Approval
WebSocket
Offline Queue

---

## Phase 2B.5
Owner Mobile Cockpit

Responsive PWA MVP

---

## Phase 2C
Data Plane

包括：

Browser Observer
Platform Map
Hybrid Collector
Data Quality
Metric Reconciliation
Data Planner

---

## Phase 2D
Business Truth

包括：

Finance Fact
Profit Engine
Inventory / Supply Engine
Master Product

---

## Phase 2E
Live Intelligence

包括：

Live Audio Agent
Streaming ASR
TalkState
TrafficState
State Fusion
Evidence Gate
Host Guidance

---

## Phase 2F
Hermes Slow Brain

正式连接真实 Slow Brain。

---

## Phase 3
Domain Brains

包括：

Product Brain
Customer Brain
Creative Brain
Creative Prompt Compiler
External Reach

---

## Phase 4
Advanced Business Intelligence

包括：

Opportunity Brain
Pricing
Demand Forecast
Portfolio Brain
Cross-shop experiments

---

# 62. 工程原则

每次开发前：

先确认：

GLOBAL_GOAL.md
当前 Architecture Doc
当前 Phase Goal

不得因为某个局部功能方便，
破坏长期架构。

---

# 63. 兼容已有成果

不得无理由重写已经完成的：

V1 Contracts
Laya Client
Laya Provider
Rules fallback
Fast Brain MCP
Hermes Slow Contracts
Safety Envelope
Shadow Loop

新架构应该向后兼容或渐进迁移。

---

# 64. 新增功能判断原则

任何新功能先回答：

1. 它属于 Perception / Reflex / Reasoning / Action / Learning 哪一层？
2. 它是否需要 shop/category scope？
3. 它的数据是否可信？
4. 它是否会产生真实写操作？
5. 它是否需要 approval？
6. 它是否能被替换？
7. 它是否会造成跨店知识污染？
8. 它是否会增加单点故障？
9. 它是否值得进入实时循环？
10. 它的 Outcome 怎么记录？

回答不清楚之前，不急于编码。

---

# 65. 最终系统心智模型

整个 Commerce OS 应理解成：

Perception Layer
API / Push / Browser / Audio / External Data

↓

Reflex Layer
Laya / JEV / Sentinel

↓

Constraint Layer
Rules / Safety / Evidence Gate

↓

Reasoning Layer
Hermes / GPT

↓

Action Layer
Task / Approval / API / Browser Runtime / RPA / Human

↓

Outcome Layer
Business Result

↓

Learning Layer
Experiment / Knowledge / Policy / Feature Update

形成持续闭环。

---

# 66. 最重要的长期原则

不要造一个“大而全、每件事都让GPT思考”的系统。

应该造：

大量极快、极窄、类型安全的经营反射

+
少量真正值得慢思考的问题

+
严格的事实层

+
严格的安全层

+
持续 Outcome 学习

这是 Commerce Brain 的核心架构方向。