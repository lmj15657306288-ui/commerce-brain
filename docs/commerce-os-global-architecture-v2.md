# Commerce OS Global Architecture v2

更新时间：2026-09-26
阶段：Architecture Rebase
分支：`codex/phase2-laya-hermes-browser`

最高层项目目标来源：[GLOBAL_GOAL.md](../GLOBAL_GOAL.md)。本文件负责记录
Architecture Rebase 的系统分解与当前阶段边界；长期有效的项目定位、原则和
最终成功标准以 `GLOBAL_GOAL.md` 为准。

## 0. 定位与不可变原则

Commerce Brain 从单店本地决策工具重新定义为：

> **Multi-Store / Multi-Category Commerce Operating System**

它是一个可以承载多个 Store Brain Context 的 Brain Runtime，而不是
“一台 Mac 对应一个店铺、一个云服务器或一个 Laya/Hermes 实例”的单体工具。

本阶段只完成全局架构定义，暂停旧 `CODEX_GOAL_PHASE2.md` 的剩余 Step 顺序。
本文审核通过前，不继续实现 Hermes、Browser Runtime 或 Cloud Gateway。

不可变原则：

1. 一个 Brain Runtime 可以承载多个相互隔离的 Store Brain Context。
2. Store 不与 Mac、Cloud Server、Laya 或 Hermes 实例做 1:1 绑定。
3. 多店铺、多品类默认不共享未经验证的业务知识。
4. 核心业务数据和知识必须支持以下作用域：
   `organization_id`、`brand_id`、`category_id`、`shop_id`、
   `channel_id`、`product_id`、`sku_id`。
5. Knowledge 支持 `global`、`category`、`brand`、`shop`、
   `product` 层级。
6. Shared capability 可以跨店复用，但不能因此跨店泄漏业务事实或策略。
7. 继续保持 `proposal-only`、`shadow-first`、`fail-closed`。
8. 当前所有真实平台写操作继续关闭。
9. 已完成的 V1、`LayaClient`、`LayaProvider`、Rules fallback 和
   Fast Brain MCP 保留，不重写。

当前业务闭环仍然是：

```text
StateSnapshot
→ Laya / Rules
→ DecisionProposal
→ Safety
→ Shadow
→ Action Card
→ Human Action
→ Outcome
```

这条链路之后会在 Context 作用域内运行，不能因为增加多店能力而产生绕过
Safety、人工确认或 Outcome 记录的旁路。

## A. 系统层级

Commerce OS 的业务层级如下：

```text
Organization
└── Brand
    └── Category
        └── Shop
            └── Channel
                └── Product
                    └── SKU
                        └── Campaign / Live / Customer
```

### A.1 层级定义

| 层级 | 含义 | 典型边界 |
| --- | --- | --- |
| `Organization` | 企业或经营主体 | 成员、租户、合规与全局安全策略 |
| `Brand` | 组织下的品牌集合 | 品牌定位、品牌知识与品牌级经营约束 |
| `Category` | 商品/业务类目 | 类目指标口径、行业知识、类目策略 |
| `Shop` | 一个可运营的店铺或经营单元 | 店铺数据、店铺政策、店铺历史与店铺负责人 |
| `Channel` | 店铺接入的销售、内容或投放渠道 | 渠道数据源、渠道账号、渠道状态与采集方式 |
| `Product` | 商品主实体 | 商品事实、素材、商品知识与跨店映射 |
| `SKU` | 可交易库存单元 | SKU 库存、成本、履约与供应约束 |
| `Campaign / Live / Customer` | 业务活动、直播场次与客户实体 | 活动效果、直播状态、客户互动与后续 Outcome |

业务对象必须带有可验证的层级引用。不能仅依赖名称、页面位置或模糊文本
推断对象归属。跨层级关联必须有稳定 ID、来源证据和作用域校验。

### A.2 核心作用域对象

所有核心数据、知识、策略、任务、告警、实验和 Outcome 都应能表达：

```json
{
  "organization_id": "org_<safe-id>",
  "brand_id": "brand_<safe-id>",
  "category_id": "category_<safe-id>",
  "shop_id": "shop_<safe-id>",
  "channel_id": "channel_<safe-id>",
  "product_id": "product_<safe-id>",
  "sku_id": "sku_<safe-id>"
}
```

字段可以按对象实际层级为空，但不得用另一个层级的 ID 代替。未能确认的
层级必须保持 `null` 或进入待确认状态，不能猜测归属。

## B. Runtime 与 Context 分离

### B.1 Brain Runtime

Brain Runtime 是可复用的计算、编排和通信运行时。它可以运行在 Mac、
未来的其他 Brain Worker 或受控服务环境中，承载：

- **Laya**：低延迟 Fast Brain 推理或分类能力。
- **Hermes**：慢速、可审阅的深度分析能力。
- **GPT**：经授权的模型网关能力，遵守脱敏和数据发送边界。
- **Feature Engine**：从规范化状态生成可解释的特征。
- **Knowledge Engine**：按作用域检索、合并和审计知识。

Runtime 提供 shared capability，但本身不拥有某个店铺的默认经营事实。
同一个 Runtime 可以先选择或路由到多个 Context，再在 Context 约束内执行
读取、推理、建议和 Outcome 归档。

### B.2 Brain Context

Brain Context 是具体组织、品牌、类目、店铺、渠道或商品的业务上下文，
包括：

- shop/category specific data
- knowledge
- policy
- history
- experiment
- outcome

Context 还应记录当前有效的 scope、数据新鲜度、证据引用、模型/规则版本、
人工确认记录和隔离策略。Context 不是运行时实例，也不应通过本机路径、
进程名或某个模型实例名来标识。

### B.3 调用边界

```text
Context selector
→ scope validation
→ data / knowledge retrieval
→ Feature Engine
→ Laya / Rules / GPT / Hermes
→ DecisionProposal
→ Safety
→ Shadow / Action Card
→ Human Action
→ Outcome
```

任何模型或 shared capability 的调用都必须显式携带已校验的 Context scope。
模型输出不能扩大读取范围，不能把跨 Context 的相似文本当作共享业务知识，
也不能直接产生平台写请求。

## C. 多店隔离

### C.1 Scope model

作用域由从宽到窄的层级组成：

```text
organization
→ brand
→ category
→ shop
→ channel
→ product
```

`campaign`、`live` 和 `customer` 作为业务实体引用其所属的最小必要 scope。
每次读写、检索、任务投递、事件发布和 Outcome 归档都必须带 scope binding。

默认规则：

- 没有明确 scope 的业务数据不得进入店铺决策链。
- 不能从 `shop_id` 反推未知的 `brand_id` 或 `category_id`。
- 不能把同名店铺、同名商品或同名活动自动合并。
- 跨店聚合只允许产生组织级聚合事实或脱敏 benchmark，不回写单店知识。
- 跨 category/shop 的数据共享必须有显式授权、证据和可撤销记录。

### C.2 Knowledge retrieval scope

知识检索按当前 Context 的最小必要范围执行，顺序为：

```text
product
→ shop
→ brand
→ category
→ global
```

检索结果必须标注：

- knowledge scope
- owner scope
- version
- effective_at / expires_at
- source and evidence
- confidence
- inheritance decision

更窄作用域的知识可以覆盖同一规则键的更宽作用域默认值，但不得静默删除
通用安全规则。知识引擎应能解释某条知识为什么被选中、继承自哪一层、
以及为什么拒绝另一条候选知识。

未验证的店铺经验默认只留在原店铺 Context；不能因为被模型总结过，就自动
提升为 brand、category 或 global 知识。

### C.3 Policy inheritance

Policy 继承与 Knowledge 继承分开处理：

```text
global safety policy
→ organization policy
→ brand policy
→ category policy
→ shop policy
→ channel / product operating policy
```

继承规则：

1. 全局安全策略不可被下层关闭或弱化。
2. 下层策略只能收紧边界，不能扩大真实平台写权限。
3. 作用域冲突时 fail closed，进入人工复核或 `OBSERVE`。
4. Policy 版本、有效期、审批者和适用 scope 必须进入 proposal 与审计。
5. Policy 未加载、过期、校验失败或作用域不匹配时，不生成可行动作。

### C.4 RBAC boundaries

RBAC 至少区分：

| 角色 | 可见范围 | 主要能力 |
| --- | --- | --- |
| `org_admin` | 组织内授权范围 | 管理组织成员、scope、全局策略与设备 |
| `brand_operator` | 被授权品牌及下属 Context | 查看、复核和维护品牌/类目运营内容 |
| `category_operator` | 被授权类目 | 查看类目数据、知识和实验结果 |
| `shop_operator` | 被授权店铺/渠道 | 处理店铺任务、人工动作和 Outcome |
| `reviewer` | 明确分配的审阅范围 | 审阅 proposal、告警、证据和影子结果 |
| `observer` | 只读范围 | 读取脱敏状态、报表和审计结果 |
| `service_runtime` | 通过短时授权的 Context | 运行受限计算，不获得人类管理权限 |

角色权限必须同时满足身份、scope、动作类型和当前 policy。拥有某个
Runtime 的访问权，不等于拥有所有 Store Context 的数据权。

### C.5 Data access boundaries

- 数据访问使用 allow-list scope，默认拒绝跨店读取。
- Edge Client 只接收被分配的 task、alert、approval、action card 摘要和必要证据。
- Cloud Gateway 只路由经过验证的合同消息，不成为业务事实源。
- Brain Worker 可以承载多个 Context，但每次任务只能绑定明确 Context。
- 日志、缓存、离线队列和事件都要继承原始 scope，禁止脱离 scope 存储。
- 任何原始 Cookie、Token、Secret、Session、完整 DOM 或未脱敏订单明细
  都不进入共享通信链路。

## D. 三层部署

### D.1 Cloud Control Plane

Cloud Control Plane 是未来的组织级控制面，负责：

- organization、brand、category、shop、channel 的目录和授权关系；
- 用户、RBAC、设备注册、session 生命周期和审计索引；
- Context 路由、任务/告警/审批/事件同步和离线投递协调；
- 版本化合同、配置、policy 和知识版本元数据；
- 对 Brain Worker 和 Edge Client 的健康状态、连接状态和能力目录。

当前 8C8G 云服务器的定位是控制平面、同步、任务、告警、审批、事件和
系统健康承载，不以重型模型推理为第一职责。

它不应成为默认的业务推理事实源，也不应拥有抖店、千川或其他平台的写权限。
真实平台写入当前全部关闭，未来若有受控能力也必须保留
`proposal-only`、人工授权、回读和 fail-closed 约束。

### D.2 Mac / Brain Worker

当前只部署一台 Mac 作为 Brain Worker。它负责：

- 运行已有 V1、LayaClient、LayaProvider、Rules fallback 和 Fast Brain MCP；
- 按 Context 读取本地或授权来源的脱敏状态；
- 运行 Feature Engine、Knowledge Engine 和 proposal/shadow 链路；
- 维护本地历史、实验、Outcome 和可恢复的离线任务；
- 主动连接 Cloud Control Plane / Gateway，不要求 Mac 暴露公网入站端口。

Brain Worker 是部署角色，不是店铺身份。未来可以增加多个 Brain Worker，
按能力、区域、数据驻留或负载路由多个 Context；同一 Context 的并发写入、
事件顺序和租约需要由控制面协调。

### D.3 Employee Edge Client

员工 Edge Client 当前以 Chrome MV3 Extension 为主，负责：

- 建立受控 device/session；
- 接收分配到的 task、alert、approval、action card 和必要的 evidence reference；
- 展示人工复核入口并回传人工动作、理由和 Outcome；
- 在短时断网期间保存脱敏离线队列；
- 通过现有 Extension → localhost Bridge 边界读取浏览器页面状态。

Edge Client 不持有 Central Brain 的全量数据，不拥有平台写权限，不通过
Cloud Gateway 绕过现有 Safety，也不上传原始网页 HTML、完整 DOM 或凭证。

部署关系：

```text
Cloud Control Plane
        ↕ authenticated WSS / HTTPS
Mac Brain Worker (current: 1)
        ↕ authenticated WSS / HTTPS
Employee Edge Client (Chrome Extension)
```

Gateway 可以作为 Cloud Control Plane 的接入层或独立 relay，但它不改变
业务 payload 的 scope 和安全语义。

### D.4 Experience Layer

Commerce OS 的正式产品面为：

```text
Employee Web Workbench
Browser Side Panel
Live Host Panel
Owner Mobile Cockpit
```

四个产品面共享同一套 Context、scope、Task、Alert、Approval、Evidence 和
Outcome 合同，不各自建立业务事实。它们的职责不同：

- **Employee Web Workbench**：员工处理任务、商品、直播、投放、客服和异常；
- **Browser Side Panel**：在当前授权页面提供观察、数据质量和 Action Card 入口；
- **Live Host Panel**：在直播中提供低延迟、证据门控的 Host Guidance；
- **Owner Mobile Cockpit**：老板查看跨店状态、影响、审批、异常和系统健康。

员工工作流按“现在发生什么、为什么重要、下一步做什么、做到什么算完成”
组织，不按平台数据源组织一级导航。罗盘、千川、联盟等是数据源或渠道，
不是默认的产品信息架构。

### D.5 Owner Mobile Cockpit

Owner Mobile Cockpit 是一等产品面，第一阶段优先采用 Responsive PWA，
不以原生 iOS、原生 Android 或小程序作为前置条件。它不复制桌面工作台，
只聚合：

- `Need Decision`
- `Need Approval`
- `Need Awareness`

老板应能在短时间内看到店群健康、利润/库存/直播影响、待审批事项、处理中
任务、系统异常和各店关键状态。MVP 至少预留以下模块：

- 多店总览：health、GMV、estimated/realized profit、ad spend、ROI、
  refund risk、inventory risk、live status 和 high-priority issues；
- 老板待办：需要决定、批准或知晓的事项；
- 高优先级异常：发生了什么、为什么重要、业务影响、建议动作、负责人和状态；
- 审批队列：approve、reject、request revision、assign、acknowledge；
- 直播简版：live status、online、GMV、ROI、current product、AI live health、
  重要 Host Guidance 及确认状态；
- 24 小时摘要：最大正向变化、最大负向变化、重要已完成动作、未解决风险和
  需要老板关注的前三项。

移动端显示的数字必须继承 source、freshness、confidence、verified 和
`last_updated`；离线时明确显示 last known state，不伪装实时。

## E. Foundation Roadmap

### Phase 2A：Fast Brain / Laya

保留当前成果：

- V1 contracts、Rules、安全校验、Shadow 和 Outcome；
- `LayaClient` 与 `LayaProvider`；
- Laya 失败时真实可识别的 Rules fallback；
- Fast Brain MCP：`fast_choice`、`fast_binary`、`fast_score`、
  `route_task`、`classify_live_state`；
- Codex 注册和在线/停止后的 fallback 验证。

所有输出仍然是 proposal-only，`execution_allowed=false`、
`can_execute=false`、`execution_performed=false`。

### Phase 2B：Multi-store Context

本阶段的下一条基础路线：

- 多店、多类目 Context 与 scope contract；
- Central Brain API；
- Cloud Gateway；
- Device Session；
- Task / Alert / Approval / Event；
- WebSocket 实时同步；
- Offline Queue。

当前已完成的 Central Brain 本机合同、身份、事件、WebSocket fixture 和离线
队列是这条路线的通信基础，但不代表已经部署生产 Cloud Gateway。

### Phase 2B.5：Owner Mobile Cockpit

- Responsive PWA MVP；
- 店群摘要、老板待办、审批队列和高优先级异常；
- 直播简版、24 小时摘要和系统健康；
- 离线/过期状态的明确展示。

移动端只消费受 scope 和 evidence 约束的合同，不直接执行高风险平台动作。

### Phase 2C：Data Plane / Browser Observer

- Browser Observer；
- Platform Map；
- Hybrid Collector；
- Data Quality；
- Metric Reconciliation；
- Data Planner；
- Browser Runtime 的只读观察边界。

这一阶段只建设授权浏览器观察和数据质量边界，不开放平台写操作。

### Phase 2D：Business Truth

- Finance Fact Layer；
- Profit Engine；
- Inventory / Supply Engine；
- Master Product 与 Store Listing 映射。

经营利润、退款、库存和供应数据必须以可追溯事实层为基础，不能让单一
广告指标替代真实利润或履约事实。

### Phase 2E：Live Audio Agent

- Live Audio Agent；
- Streaming ASR；
- TalkState；
- TrafficState；
- Evidence Gate；
- Host Guidance。

直播话术建议只进入 proposal、提示或人工复核，不直接修改投放、商品或库存。

### Phase 2F：Hermes Slow Brain

Hermes 负责慢速分析、跨时间窗口复盘、策略草案、研究报告和可审阅的
Action Card 草案。Hermes 不进入直播秒级 Fast Loop，也不拥有平台写权限。

### Phase 3：Product / Customer / Creative

- Product Brain；
- Customer Brain；
- Creative Brain；
- Creative Prompt Compiler；
- External Reach。

这些能力必须在各自 Context scope 内运行，跨店学习先形成脱敏 benchmark，
再经过验证和人工批准才可能提升为共享知识。

### Phase 4：Opportunity Brain

- Pricing；
- Demand Forecast；
- Portfolio Brain。

这些能力的预测或建议不等于自动执行。涉及价格、库存、组合或投放时，仍
必须经过证据门、Safety、人工动作和结果回读。

## E.1 Shared Core and Category Pack

Shared capability 可以跨店复用，但业务结论不能因此自动共享。基础能力包括：

- Data Engine；
- Feature Engine；
- Decision Engine；
- Experiment Engine；
- Profit Engine framework；
- Inventory Engine framework；
- Task / Alert / Approval Engine；
- Safety Engine；
- Model Gateway；
- Knowledge Engine；
- Browser Runtime framework；
- Audit Engine。

Category Pack 按类目提供业务解释，不复制一套跨类目硬编码规则。Category
Pack 可以定义：

- business metrics；
- customer intents；
- product attributes；
- creative patterns；
- refund patterns；
- opportunity features；
- risk thresholds；
- pricing considerations；
- supply rules。

知识从 `shop` 提升到 `brand/category` 或 `global` 前，必须检查证据数量、
样本量、一致性、类目适配、流量上下文、实验质量、置信度和 Outcome 稳定性。
单店经验不得直接指导另一类目；跨店结果首先只能作为 benchmark、
portfolio analysis 或 cross-shop experiment 的脱敏输入。

## E.2 System-1 and Agent Boundaries

高频经营判断优先拆解为 small、typed、bounded 的 classification、
routing 或 scoring 问题：

```text
大量状态
→ Laya / JEV / Sentinel 并行筛选
→ Rules / Evidence Gate
→ 少量重要 Case
→ Hermes / GPT 深度分析
```

Laya / JEV 是 Reflex Layer / System-1，输出应包含 action 或 state、
confidence、reason_code、TTL 和 scope。典型结果包括 `HOLD`、
`PROMPT_HOST`、`CHECK_PRODUCT`、`CHECK_CAMPAIGN`、
`ESCALATE_SLOW_BRAIN` 以及 `NORMAL`、`WATCH`、`RISK`。

Hermes 是 Slow Brain，负责 daily review、live review、product review、
campaign review、strategy analysis、cross-domain synthesis、policy draft、
feature suggestion 和 data quality review。Hermes 默认只能生成 DRAFT；
policy 生命周期为：

```text
DRAFT → REVIEWED → SHADOW → APPROVED → ACTIVE → RETIRED
```

Codex 是 Engineering Agent，负责代码、测试、迁移、集成、文档、部署和
仓库维护。Rules / Safety / Evidence Gate 的优先级高于 Laya、Hermes 和 GPT，
模型不能自行提升 policy、扩大 scope 或开放平台写权限。

## E.3 Task, Alert, Approval and Notification

Task、Alert、Approval 是跨产品面的统一一等对象：

```text
Task      → 谁处理、截止时间、优先级、状态、Outcome
Alert     → 问题、影响、证据、建议动作
Approval  → proposal、risk、scope、reason、human decision
```

通知必须支持去重、冷却和聚合，避免用提醒数量替代经营优先级：

- `P0`：利润底线、严重缺货、重大直播异常、关键系统离线或高风险审批；
- `P1`：活动劣化、重要价格 proposal 或退款异常；
- `P2`：普通任务完成、轻微指标波动和低风险建议。

任务排序优先参考：

```text
Expected Impact × Confidence ÷ Effort
```

业务影响可包含 `gmv_impact`、`profit_impact`、`inventory_impact`、
`customer_impact`、`live_impact` 和 `confidence`，老板端优先展示影响而不是
技术指标数量。

## F. Live architecture

### F.1 组件与数据流

```text
Live Audio Agent
  → Streaming ASR
  → TalkState

Browser Observer
  → TrafficState
  → ProductState
  → AdState

TalkState + TrafficState + ProductState + AdState
  → State Fusion
  → Evidence Gate
  → Host Guidance
  → DecisionProposal / Action Card
  → Human Action
  → Outcome
```

### F.2 Live Audio Agent

Live Audio Agent 在直播电脑的 Edge 侧采集主播麦克风或推流前音频，目标是
获得可控、低延迟、可解释的语音输入。采集优先级为主播麦克风直采、
OBS/直播伴侣监听音轨、系统音频、浏览器回放音频。它只应输出经授权、必要
且脱敏的音频特征或转写片段引用；原始音频是否保存、保存多久以及谁可访问
必须由组织和店铺 policy 决定。

它不是直播平台执行器，不直接发送消息、修改商品或调整广告。

### F.3 Streaming ASR

Streaming ASR 提供低延迟实时转写，并带有：

- segment id 和时间窗口；
- partial / final 状态；
- 语言与置信度；
- 丢包、延迟和 freshness；
- 可撤销的音频片段引用。

实时阶段以约 1-3 秒级 partial transcript 为目标；直播结束后可使用离线
高精度对齐与说话人分析，但这些结果仍受 Context、权限和保留策略约束。

ASR 文本不能自动被解释为经营结论。TalkState 需要结合直播上下文、商品
作用域和证据质量，保留不确定性。

### F.4 Browser Observer

Browser Observer 读取直播后台指标和必要的只读页面状态，优先复用现有
Extension → localhost Bridge 边界。它输出规范化的 `TrafficState`、
`ProductState` 和 `AdState`，而不是把原始 HTML、完整 DOM、Cookie 或 Token
送入模型。

Browser Observer 的数据应包含来源、采集时间、页面/接口范围、freshness、
confidence 和是否经过校验。无法稳定识别账号、店铺、计划或指标口径时，
必须降级为未知或 `OBSERVE`。

### F.5 State Fusion

State Fusion 合并：

- `TalkState`：主播话题、节奏、商品讲解和转化意图等可审阅特征；
- `TrafficState`：曝光、进房、点击、停留等流量事实；
- `ProductState`：商品、SKU、价格显示、库存和履约相关只读状态；
- `AdState`：投放消耗、转化、ROI、计划状态等只读状态。

Fusion 必须按同一时间窗、同一 Context scope 和同一证据版本对齐。时间不
一致、scope 不一致或来源冲突时，保留冲突并降低置信度，不能静默覆盖。

### F.6 Evidence Gate

Evidence Gate 的状态为：

- `EVIDENCE_NONE`：没有足够证据，只允许记录观察或请求补充数据。
- `EVIDENCE_WEAK`：有局部或低新鲜度证据，只允许弱建议、继续观察或人工复核。
- `EVIDENCE_OK`：来源、时间、scope 和指标口径满足 policy，才允许生成
  有限的 proposal/action card。

关键规则：

> 没有足够流量证据时，禁止把经营结果差归因为主播话术差。

当 `TrafficState` 缺失、过期、冲突或样本不足时，TalkState 只能作为
待验证假设，不能成为“主播话术导致经营结果差”的结论依据。

### F.7 话术评价拆分

话术评价必须拆成两个维度：

1. **Content Quality**：内容清晰度、商品信息完整性、表达节奏、合规风险和
   观众可理解性。
2. **Business Effectiveness**：在同一 Context、同一时间窗、足够流量证据
   和可比商品/活动条件下，话术与点击、停留、加购、成交等业务结果的关系。

Content Quality 可以在业务结果不完整时给出谨慎的内容观察；Business
Effectiveness 必须经过 Evidence Gate，不能用内容质量评分直接替代经营效果
归因。

### F.8 Browser Observer 与 Browser Runtime

Browser Observer 与 Browser Runtime 是两个不同边界：

- **Browser Observer** 负责看：DOM、结构化 XHR/JSON、accessibility、page
  state、直播指标和平台只读状态；
- **Browser Runtime** 负责受控地做：`NAVIGATE`、`SNAPSHOT`、`CLICK`、
  `TYPE`、`PRESS`、`SCROLL`、`WAIT`、`BACK`、`VERIFY`、`DONE`、
  `BLOCKED`。

Observer 优先级为：

```text
structured DOM/data → XHR/JSON → accessibility → vision/screenshot → OCR
```

Browser Runtime 不是中央大脑，也不能绕过 proposal、Safety、approval、
scope 和 audit。当前只允许 read、navigate、filter、export 等低风险能力；
预算、ROI、广告启停、价格、库存、上下架和高风险表单提交继续禁止。
每个 Browser Action 必须记录 session、actor、tool、origin、page hash、
action、target、risk、approval、timestamp、result 和 verification，不记录
password、secret 或 raw auth token。

## G. Data trust

### G.1 来源分层

| 来源 | 可信等级 | 用途 | 默认限制 |
| --- | --- | --- | --- |
| Official API / Push | truth | 权威事实、关键回读和对账 | 仍需 scope、时间窗和口径校验 |
| Authorized Browser | high-frequency perception | 高频观察、页面状态和实时感知 | 不能替代权威事实，需记录采集上下文 |
| Third-party | benchmark / estimate | 基准、估计和外部比较 | 不得直接驱动高风险动作 |
| External Web | market signal | 市场信号、趋势和研究 | 不得写回店铺经营事实或自动策略 |

`Official API / Push` 是 truth，但“官方来源”也必须经过账号、scope、时间
窗口、版本和口径核验。没有这些元数据时，结果只能降级。

### G.2 Metric metadata

所有 metric 必须带有以下元数据：

```json
{
  "source": "official_api|authorized_browser|third_party|external_web",
  "source_type": "api|push|browser_observation|benchmark|market_signal",
  "captured_at": "2026-09-27T00:00:00+00:00",
  "freshness": "fresh|aging|stale|unknown",
  "confidence": 0.0,
  "verified": false
}
```

实际合同还应绑定：

- organization / brand / category / shop / channel / product scope；
- metric definition、unit、denominator 和 time window；
- source reference、evidence hash 和 collector version；
- observed_at 与 captured_at 的区别；
- reconciliation status 和 conflict reason。

没有 `source`、`source_type`、`captured_at`、`freshness`、`confidence` 或
`verified` 的 metric 不得进入高风险决策。缺少关键元数据时，系统应返回
`OBSERVE`、`BLOCK` 或要求人工补证，而不是用默认值补齐。

### G.3 Data plane and canonical metrics

Data Plane 支持：

```text
Official API / Push
Official Export
Browser Observer
Browser Runtime Read
Third-party
External Web
Manual Import
```

每个 metric 应由 Data Planner 声明：

- `preferred_source`；
- `verification_source`；
- `poll_interval`；
- `max_age`；
- `cost_class`；
- `required_for_execution`。

多个来源冲突、缺失、过期、漂移、重复或时间未对齐时，Data Quality Engine
负责生成带冲突说明的 Canonical Metric。Brain 默认消费 Canonical Metric，
不直接把所有原始值当作同等事实。原始数据按短期采集、中期事实、持续更新
特征、长期 Decision / Action / Outcome 和长期蒸馏 Knowledge 分层保存，
避免无限保存原始大文件。

核心事实与决策对象至少包括：

```text
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
```

其中 `feature_snapshot`、`decision`、`action` 和 `outcome` 是跨系统追溯的
核心链路对象，必须保留 Context、scope、source、版本、证据和时间窗口。

### G.4 Profit, Inventory and Master Product truth

Profit Engine 必须基于确定性的 Finance Fact，不由 GPT 或其他模型计算利润
真值。至少区分：

```text
Order Estimated
→ Delivered Estimated
→ After-sales Adjusted
→ Final Settled Realized
```

成本事实应覆盖 product cost、platform/payment fee、affiliate commission、
ad cost、logistics、return logistics、refund loss、compensation、packaging
和其他 variable cost，输出 `estimated_profit`、`realized_profit`、margin、
`break_even_roi`、`safety_roi`、`target_roi` 和 `scale_floor_roi`。

Inventory / Supply Engine 至少表达：

- `physical_stock`；
- `reserved_stock`；
- `available_to_promise`；
- `in_transit`；
- `return_pending`；
- `lead_time`；
- `days_of_supply`；
- `stockout_risk`；
- `overstock_risk`；
- `reorder_point`。

多店环境必须区分 `Master Product` 与 `Store Listing`。同一真实商品可以有
不同 Shop Product ID 和 Channel Item ID，但应通过 `master_product_id` 关联
成本、库存、素材、跨店比较、供应链和商品知识。名称相同不构成自动映射。

### G.5 Audit, recovery and cost

所有 Decision 长期记录：

```text
model
model_version
prompt_version
feature_version
policy_version
```

Task、Alert、Approval 是一等对象：

- Task 记录负责人、截止时间、优先级、状态和 Outcome；
- Alert 记录问题、影响、证据和建议动作；
- Approval 记录 proposal、risk、scope、reason 和人类决定。

Mac 不能成为唯一恢复点。长期部署必须考虑 database、knowledge、policy、
config 和 object storage backup。Mac 离线时，Cloud 只能展示带
`last_updated`、freshness 和 offline 标记的 last known state、tasks、
alerts 和 approvals，不能伪装实时。

系统还应记录 model cost、API cost、storage cost、creative cost 和
third-party cost，并支持按 Brain、Shop、Product 和 Task 归属成本。

## H. Non-goals

当前阶段明确禁止：

- 真实预算修改；
- 真实 ROI 修改；
- 自动暂停广告；
- 自动价格修改；
- 自动库存修改；
- 自动上下架；
- 自动发消息；
- 自动退款；
- 自动批量商品修改。

同时保持以下边界：

- 不把 proposal、shadow 或 action card 当作平台执行成功；
- 不让 Gateway、Edge Client、Laya、Hermes 或外部模型绕过 Safety；
- 不把浏览器 Cookie、Token、Secret、Session 原文写入仓库、日志、事件、
  离线队列或共享消息；
- 不把完整 HTML、DOM、截图全文或未脱敏订单明细送入 Brain Runtime；
- 不把 benchmark、第三方估计或市场信号伪装为官方事实；
- 不在本阶段实现复杂业务 UI、自动策略发布或真实生产平台写入。

## 当前实施门槛

本文审核通过后，下一阶段才允许细化并实现：

1. Context / scope contracts；
2. Central Brain API 与 Gateway 边界；
3. Device / session、task / alert / event 和 WebSocket；
4. Edge Client 离线队列；
5. Browser Observer 与 Data Quality。

在审核通过前，当前仓库保留未跟踪的 Browser Runtime 草稿，不将其视为已交付
实现，也不继续扩展。
