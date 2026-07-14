# 高级 Agent 治理闭环技术设计

> 文档版本：1.0  
> 最后更新：2026-07-14  
> 适用范围：Enterprise Ticket Agent 的 Agent Identity、信息流安全、Online Eval 与 Counterfactual Replay  
> 实现状态：核心链路已落地；生产增强项在本文末尾单独列出

## 1. 文档目标

本文描述四项高级 Agent 治理能力如何接入现有的 LangGraph、TaskSpec、PlanGraph、Evidence Graph、Verifier、Policy-as-Code、Tool Gateway 与 Simulation Lab：

1. **Agent Identity + 委托授权**：证明谁授权哪个 Agent，在什么租户、资源、工具和业务约束下执行任务。
2. **Taint Tracking 信息流安全**：阻止用户输入、RAG 文本、模型生成值和未验证工具结果直接控制高风险写操作。
3. **Online Eval + Feedback Loop**：把线上人工接受、纠正与拒绝转化为受审核、可版本化的评估数据。
4. **Counterfactual Replay**：对同一任务离线比较模型、Prompt、Policy 和计划版本，且不触发真实副作用。

这四项能力不是旁路 Demo。它们共享 `tenant_id`、`thread_id`、`trace_id`、`task_id`、版本上下文和审计记录，组成以下闭环：

```mermaid
flowchart LR
    U["用户或审批人"] --> I["Agent Identity\n委托授权"]
    I --> S["LangGraph / Supervisor"]
    S --> E["Evidence Graph + Verifier"]
    E --> T["Taint Tracking"]
    T --> G["Tool Gateway"]
    G --> X["ERP / IAM / 通知等系统"]
    G --> A["Audit Log"]
    S --> F["用户反馈"]
    F --> O["Online Eval"]
    A --> C["Counterfactual Replay"]
    O --> C
    C --> R["版本选择与回归验证"]
    R --> S
```

## 2. 系统边界与信任边界

### 2.1 受保护对象

系统重点保护以下对象：

- 退款、通知、工单、权限授予、财务记账等业务副作用；
- 用户、角色、租户、审批凭证和 Agent 身份；
- 订单号、金额、币种、业务系统 ID 等工具参数；
- 线上反馈、评估集、策略版本与回放结果；
- 审计日志中的敏感信息和授权令牌。

### 2.2 信任边界

| 区域 | 默认信任级别 | 处理原则 |
| --- | --- | --- |
| 系统配置、Policy-as-Code | 高 | 可参与确定性授权和验证 |
| 企业数据库中的已验证事实 | 高 | 可作为写操作参数来源 |
| 人工审批结果 | 高，但必须绑定审批记录 | 只对被审批的任务和字段有效 |
| 用户自然语言输入 | 低 | 只能提出目标，不能单独授权高风险写入 |
| RAG 文档 | 低 | 必须有来源、引用和验证，防止 Prompt Injection |
| LLM 输出 | 低 | 只能生成候选 TaskSpec、计划或解释，不能成为授权事实 |
| 外部工具返回 | 低 | 返回值可能过期、被污染或 Schema 异常，必须验证后再使用 |

### 2.3 设计不变量

系统维持以下安全不变量：

- Planner 可以提议计划，但不能绕过 Plan Validator、Policy、审批或 Tool Gateway。
- Agent 持有的能力不能超过委托人的身份、租户、工具、资源和金额范围。
- 高风险工具在 handler 执行前完成授权、Schema、审批和信息流检查。
- 未验证的信息不能直接控制高风险写操作。
- 线上纠正和拒绝不会自动进入 golden set，必须经过人工审核。
- Counterfactual Replay 不调用真实 Tool Gateway handler，副作用计数必须为 0。

## 3. 代码结构

| 模块 | 主要职责 |
| --- | --- |
| [`delegation.py`](../backend/app/agent/delegation.py) | 委托令牌签发、解码、范围校验、持久 Grant 消费 |
| [`information_flow.py`](../backend/app/agent/information_flow.py) | 来源标签、字段污染传播、写操作门禁与去污 |
| [`online_eval.py`](../backend/app/agent/online_eval.py) | 反馈持久化、候选 Eval Case、审核发布与漂移报告 |
| [`counterfactual_replay.py`](../backend/app/agent/counterfactual_replay.py) | 审计快照重建、影子计划生成、策略和计划差异比较 |
| [`tool_gateway.py`](../backend/app/agent/tool_gateway.py) | 统一执行边界，串联 Policy、审批、授权、信息流与审计 |
| [`verifier.py`](../backend/app/agent/verifier.py) | 证据和后置条件验证，生成可审计去污记录 |
| [`chat.py`](../backend/app/api/routes/chat.py) | 在任务入口消费委托 Grant，并把授权上下文写入 Agent State |
| [`agent_governance.py`](../backend/app/api/routes/agent_governance.py) | 四项治理能力的 HTTP API |
| [`models.py`](../backend/app/db/models.py) | 治理数据库模型 |
| [`3c4d5e6f7081_add_agent_governance_loop.py`](../backend/alembic/versions/3c4d5e6f7081_add_agent_governance_loop.py) | 四张治理表、索引与 PostgreSQL RLS |
| [`page.tsx`](../frontend/app/admin/simulation/page.tsx) | Simulation Lab 中的 Online Eval 与反事实回放界面 |

## 4. Agent Identity 与委托授权

### 4.1 为什么不能只使用登录态

普通登录态只能证明“当前用户是谁”，不能完整回答：

- 用户是否明确授权这个 Agent 执行当前任务；
- Agent 可以调用哪些工具；
- 授权是否只适用于某个订单、系统或金额范围；
- 授权是否绑定特定审批凭证；
- 授权是否已经被撤销、过期或超过使用次数。

因此系统采用“**持久 Grant + 短期 Capability Token**”双层模型：

- `AgentDelegationGrant` 是授权事实源，负责状态、撤销、过期和次数控制；
- JWT 是在 LangGraph State 和 Tool Gateway 之间传递的短期能力证明；
- 数据库只保存 JTI 哈希，不保存令牌原文；
- 审计脱敏器会屏蔽 `delegation_token` 与 `principal_token`。

### 4.2 委托令牌 Claims

| Claim | 含义 | 校验位置 |
| --- | --- | --- |
| `iss` | 固定为 `enterprise-ticket-agent` | JWT 解码 |
| `aud` | 固定为 `tool-gateway` | JWT 解码 |
| `typ` | 固定为 `agent-delegation+jwt` | JWT 解码 |
| `jti` | 令牌唯一标识 | 请求入口与持久 Grant 关联 |
| `grant_id` | 持久授权 ID | 请求入口 |
| `sub` | 被委托 Principal | 请求入口、Tool Gateway |
| `role` | 被委托角色 | Tool Gateway |
| `tenant_id` | 租户边界 | 请求入口、Tool Gateway、RLS |
| `agent_id` | 被授权 Agent 身份 | Tool Gateway |
| `allowed_tools` | 工具白名单 | Tool Gateway |
| `resource_scopes` | 订单、用户、系统等资源范围 | Tool Gateway |
| `constraints` | 金额、币种、Specialist 等业务约束 | Tool Gateway |
| `approval_id` | 可选审批证据绑定 | Tool Gateway |
| `iat` / `nbf` / `exp` | 签发、生效和过期时间 | JWT 解码 |

### 4.3 授权生命周期

```mermaid
sequenceDiagram
    participant U as 用户
    participant API as Governance API
    participant DB as AgentDelegationGrant
    participant Chat as Chat API
    participant LG as LangGraph
    participant TG as Tool Gateway
    participant ERP as 企业系统

    U->>API: 创建委托范围
    API->>DB: 保存 Grant 和 JTI Hash
    API-->>U: 返回短期 Capability Token
    U->>Chat: 提交任务 + delegation_token
    Chat->>DB: 行锁校验并消费一次使用额度
    DB-->>Chat: active / expiry / max_uses 结果
    Chat->>LG: 注入 Agent、Principal、Tenant 和 Token
    LG->>TG: 调用写工具
    TG->>TG: 校验签名、身份、工具、资源、约束、审批
    TG->>ERP: 仅在全部允许后调用 handler
    ERP-->>TG: 返回结果
    TG-->>LG: 结果 + 授权审计事件
```

任务入口使用数据库行锁消费一次 Grant，可避免并发请求同时突破 `max_uses`。每次工具调用仍会重新验证 JWT 中的身份和作用域，防止 LangGraph 节点或配置错误扩大权限。

### 4.4 创建委托

接口：`POST /api/agent/governance/delegations`

认证：普通用户登录态。只有 `MANAGER`、`SECURITY` 或 `ADMIN` 可以为其他 Principal 签发委托。

```json
{
  "principal_id": "user_1001",
  "principal_role": "USER",
  "agent_id": "enterprise-ticket-agent",
  "allowed_tools": ["lookup_order", "execute_refund"],
  "resource_scopes": {
    "order_id": ["ERP-ORD-1002"]
  },
  "constraints": {
    "max_amount": 1000,
    "allowed_currencies": ["CNY"],
    "allowed_specialists": ["finance_agent"]
  },
  "purpose": "处理 ERP-ORD-1002 的已审批退款",
  "approval_id": "approval_20260714_001",
  "max_uses": 1
}
```

响应中的 `delegation_token` 只应保存在当前任务的短期客户端状态中，不应写入日志、URL、分析平台或长期记忆。

```json
{
  "delegation": {
    "grant_id": "dlg_...",
    "status": "active",
    "agent_id": "enterprise-ticket-agent",
    "max_uses": 1,
    "use_count": 0
  },
  "delegation_token": "eyJ...",
  "token_type": "Agent-Delegation"
}
```

### 4.5 Chat 请求携带授权

```json
{
  "messages": [
    {
      "role": "user",
      "content": "为订单 ERP-ORD-1002 退款 299 元"
    }
  ],
  "thread_id": "thread_refund_001",
  "user_id": "user_1001",
  "delegation_token": "eyJ..."
}
```

委托 Chat 不使用普通 Chat Cache，避免复用未绑定当前授权状态的历史响应。

### 4.6 撤销与查询

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/agent/governance/delegations?status=active` | 查询当前租户的授权 |
| `POST` | `/api/agent/governance/delegations/{grant_id}/revoke` | 撤销授权 |

普通用户只能查看和撤销与自己相关的授权；管理和安全角色可以治理租户范围内的授权。

### 4.7 典型拒绝原因

| Reason Code | 含义 |
| --- | --- |
| `DELEGATION_REQUIRED` | 写操作要求委托，但请求未提供令牌 |
| `DELEGATION_INVALID` | 签名、Issuer、Audience、类型或有效期错误 |
| `DELEGATION_PRINCIPAL_MISMATCH` | 请求人和令牌 Principal 不一致 |
| `DELEGATION_ROLE_MISMATCH` | 当前角色与委托角色不一致 |
| `DELEGATION_TENANT_MISMATCH` | 跨租户使用令牌 |
| `DELEGATION_AGENT_MISMATCH` | 令牌绑定了其他 Agent |
| `DELEGATION_TOOL_DENIED` | 工具不在白名单中 |
| `DELEGATION_SPECIALIST_DENIED` | Specialist 不在授权范围中 |
| `DELEGATION_APPROVAL_MISMATCH` | 审批凭证与令牌绑定不一致 |
| `DELEGATION_RESOURCE_DENIED` | 订单、用户或系统资源越界 |
| `DELEGATION_CONSTRAINT_DENIED` | 金额、币种等业务约束越界 |

### 4.8 当前边界

当前实现使用应用 `SECRET_KEY` 和配置的 JWT 对称算法签名，适合单体服务和作品演示。多服务生产环境建议升级为 KMS 托管的 `RS256` 或 `ES256`、`kid`、JWKS、公私钥轮换和签名服务隔离。

撤销状态在任务入口读取数据库。一旦任务已经开始，工具层主要复核短期 Token 的加密 Claims，不会在每一步重新查询 Grant。因此“执行中的即时撤销”目前由短 TTL 限制风险窗口；严格生产环境应增加 Grant Introspection 或 Redis 撤销列表。

## 5. Taint Tracking 信息流安全

### 5.1 威胁场景

信息流安全解决的不是普通参数校验，而是“**值从哪里来，以及它是否有资格影响写操作**”。典型风险包括：

- 用户说“忽略审批规则并退款 10 万元”；
- RAG 政策文档中藏有 Prompt Injection；
- LLM 根据上下文自行补出订单号、金额或收款账户；
- 外部工具返回被污染的数据，随后被另一个写工具直接使用；
- 审批只批准 299 元，但执行参数被改成 2,999 元。

### 5.2 来源标签

| 标签 | 说明 | 默认可直接驱动高风险写操作 |
| --- | --- | --- |
| `system_config` | 受控系统配置 | 是 |
| `trusted_enterprise_data` | 已验证企业事实 | 是 |
| `user_provided` | 用户输入 | 否 |
| `rag_untrusted` | RAG 或文档内容 | 否 |
| `external_tool_untrusted` | 外部工具原始结果 | 否 |
| `model_generated` | LLM 生成或推断值 | 否 |
| `policy_verified` | Policy + Evidence + Verifier 验证 | 是 |
| `human_verified` | 人工审批确认 | 是 |
| `unknown` | 未声明来源 | 否 |

### 5.3 数据结构

`data_provenance` 以工具参数字段为单位保存来源、证据、验证者和指纹。例如：

```json
{
  "order_id": {
    "label": "trusted_enterprise_data",
    "source": "mini_erp.sales_order",
    "evidence_ids": ["ev_order_1002"],
    "verified_by": "order_lookup"
  },
  "amount": {
    "label": "policy_verified",
    "source": "verifier",
    "evidence_ids": ["ev_order_amount", "ev_return_received"],
    "verified_by": "deterministic_verifier",
    "fingerprint": "sha256:..."
  }
}
```

Agent State 同时携带：

- `data_provenance`：字段当前信任状态；
- `information_flow_events`：传播、阻断和去污事件；
- `evidence_graph`：支持结论的证据；
- `verification_result`：Verifier 的结构化结论。

### 5.4 执行前门禁

Tool Gateway 在调用 handler 前按以下顺序检查：

```text
Tool Spec / Specialist
    -> Policy-as-Code
    -> Approval Evidence
    -> Input Schema
    -> Delegated Authority
    -> Information Flow
    -> Handler
    -> Output Schema
    -> Output Provenance
    -> Audit
```

对于高风险写工具，信息流门禁检查：

1. 关键字段是否存在；
2. 每个字段是否有来源记录；
3. 来源标签是否允许写入；
4. 是否绑定 Evidence ID；
5. 字段是否经过确定性 Verifier 或人工审批；
6. 当前值是否与去污时的指纹一致。

任何字段在验证后被修改，旧指纹都会失效，必须重新验证。

### 5.5 去污不是字符串清洗

这里的“去污”不是移除敏感词，而是把低信任值提升为可执行事实。只有以下条件满足时才允许提升：

- Verifier 明确返回通过；
- 存在支持该字段的 Evidence ID；
- 记录执行验证的模块或人工审批人；
- 保存字段值指纹，绑定验证时的具体值；
- 去污事件进入审计链。

退款流程中的关键字段由 [`verifier.py`](../backend/app/agent/verifier.py) 在证据验证通过后标记为 `policy_verified` 或 `human_verified`。

### 5.6 三种运行模式

| 模式 | 行为 | 用途 |
| --- | --- | --- |
| `off` | 不进行信息流检查 | 紧急兼容，不建议长期使用 |
| `audit` | 记录违规但不阻断 | 初次部署、字段覆盖观测 |
| `enforce` | 在 handler 前阻断违规 | 预生产和生产 |

Dry-run 会报告信息流违规，但不会调用有副作用的业务 handler。

### 5.7 输出污染传播

外部 Tool 输出默认标记为 `external_tool_untrusted`。这意味着“工具调用成功”不等于“结果可信”。结果必须经过 Schema、业务不变量、Evidence Graph 和 Verifier 校验后，才能作为后续写操作的可信输入。

### 5.8 当前边界

当前字段追踪以工具参数的顶层字段为主，尚未实现任意深度 JSONPath、数组元素级和跨对象别名传播。复杂企业数据建议后续增加：

- JSONPath 级 Provenance；
- 派生字段依赖图；
- 数据合并时的最小信任级别传播；
- Prompt、RAG Chunk、Tool Result 到最终参数的跨节点血缘；
- 与 OpenTelemetry Span Attribute 或独立 Data Lineage Store 集成。

## 6. Online Eval 与反馈闭环

### 6.1 目标

离线 Eval 只能回答“固定样本是否通过”，Online Eval 还需要回答：

- 真实用户是否接受结果；
- 人工修改了什么；
- 哪个模型、Prompt、Policy 或 Plan 版本退化；
- 线上错误是否进入下一轮回归测试；
- 用户反馈是否经过审核，避免数据污染。

### 6.2 生命周期

```mermaid
stateDiagram-v2
    [*] --> FeedbackRecorded
    FeedbackRecorded --> Closed: accept
    FeedbackRecorded --> Candidate: correct / reject
    Candidate --> Approved: 管理员审核通过
    Candidate --> Rejected: 管理员审核拒绝
    Approved --> VersionedDataset: 发布 dataset_version
    VersionedDataset --> RegressionEval
```

`accept` 只记录反馈指标；`correct` 和 `reject` 会创建 `candidate` Eval Case。候选样本不会自动成为 golden case。

### 6.3 提交反馈

接口：`POST /api/agent/governance/feedback`

认证：当前用户登录态。

```json
{
  "thread_id": "thread_refund_001",
  "trace_id": "trace_001",
  "task_id": "task_001",
  "scenario_id": "refund",
  "disposition": "correct",
  "rating": 2,
  "reason_codes": ["WRONG_AMOUNT", "MISSING_EVIDENCE"],
  "correction": {
    "expected_amount": 299,
    "expected_decision": "human_review"
  },
  "task_snapshot": {
    "order_id": "ERP-ORD-1002",
    "risk_level": "medium"
  },
  "version_context": {
    "model": "gemini-2.5-flash",
    "prompt_version": "refund-v7",
    "policy_version": "2026-07-14",
    "plan_version": "runtime-v3"
  }
}
```

### 6.4 审核 Eval Case

接口：`POST /api/agent/governance/online-eval/cases/{case_id}/review`

认证：管理员 API Key。

```json
{
  "status": "approved",
  "dataset_version": "production-feedback-2026-07",
  "reviewed_by": "risk-admin-01"
}
```

审核通过后写入 `dataset_version` 和 `published_at`；审核拒绝会保留记录用于审计，但不会进入发布数据集。

### 6.5 指标定义

设时间窗口内总反馈数为 `N`：

- `Acceptance Rate = accept_count / N`
- `Correction Rate = correct_count / N`
- `Rejection Rate = reject_count / N`
- `Human Override Rate = (correct_count + reject_count) / N`
- `Average Rating = 有评分记录的 rating 平均值`

报告按以下版本键聚合：

```text
model | prompt_version | policy_version | plan_version
```

接口：`GET /api/agent/governance/online-eval/report?window_hours=24`

报告比较当前窗口和等长上一窗口。当两个窗口样本数均达到 `ONLINE_EVAL_MINIMUM_SAMPLE_SIZE`，且接受率下降超过 `ONLINE_EVAL_DRIFT_THRESHOLD` 时，标记漂移。

### 6.6 数据治理原则

- Feedback、Candidate、Approved Dataset 三层分离；
- 用户纠正不直接修改生产 Prompt、Policy 或模型；
- `task_snapshot` 和 `correction` 在持久化前应持续经过敏感字段脱敏；
- 发布数据集必须记录 Reviewer 和 Dataset Version；
- 低样本窗口不应触发自动回滚；
- 版本效果对比不能只看接受率，还应结合场景、风险等级和任务成功率。

### 6.7 当前边界

当前漂移检测基于相邻时间窗口接受率差异，属于可解释的基础实现。生产环境可增加 Wilson 区间、显著性检验、分场景最小样本、告警抑制、延迟反馈归因和审计审批流程。

## 7. Counterfactual Replay

### 7.1 目标

反事实回放回答：在输入和业务事实相同的情况下，替换模型、Prompt、Policy 或计划后，系统是否会做出不同决定，以及这种变化是否更安全、更有效。

它与“重新执行真实任务”的区别是：

- 不调用真实 Tool handler；
- 不创建退款、凭证、权限或通知；
- 只比较候选计划、策略决策、证据覆盖和审批要求；
- 结果持久化，便于版本评审和审计。

### 7.2 基线来源

基线快照支持三种来源：

1. 显式传入 `baseline_snapshot`；
2. 使用 `thread_id` 从 Audit Log 重建；
3. Simulation Lab 未传快照时使用合成基线。

审计重建会提取 TaskSpec、PlanGraph、Evidence Graph、金额、币种、风险、用户历史和验证结果等关键字段。

### 7.3 Variant 类型

#### 预生成影子结果

外部模型或评测平台先生成候选 `plan_graph`，再通过 `model_output.plan_graph` 提交。系统只验证和比较，不再次调用模型。

#### 在线影子模型

设置 `run_model=true`、`provider`、`model` 和 `prompt_template`。LLM Gateway 仅允许模型生成结构化 PlanGraph，不能调用工具。

传给影子模型的输入会：

- 经过敏感字段脱敏；
- Evidence Claim 和 Relation 各限制最多 30 条；
- 使用温度 0；
- 受超时和候选模型约束；
- 返回延迟、Token 和估算成本。

### 7.4 请求示例

接口：`POST /api/agent/governance/counterfactual/replay`

认证：管理员 API Key。

```json
{
  "thread_id": "thread_refund_001",
  "task_id": "task_001",
  "requested_by": "simulation-lab",
  "scenario_id": "refund",
  "variants": [
    {
      "variant_id": "new-policy",
      "policy_document": {
        "version": "refund-policy-v8",
        "refund": {
          "auto_approve_max_amount": 200
        }
      }
    },
    {
      "variant_id": "new-planner",
      "run_model": true,
      "provider": "gemini",
      "model": "gemini-2.5-flash",
      "prompt_version": "planner-v8",
      "prompt_template": "Generate a bounded PlanGraph with read-before-write, approval, compensation and idempotency."
    }
  ]
}
```

### 7.5 回放执行链

```mermaid
flowchart TD
    B["Baseline Snapshot"] --> M["Materialize Variants"]
    M --> L["可选 Shadow LLM\n只生成 PlanGraph"]
    L --> V["Deterministic Plan Validator"]
    M --> P["Policy-as-Code Evaluator"]
    B --> E["Evidence Coverage Check"]
    V --> D["Decision Comparator"]
    P --> D
    E --> D
    D --> S["Persist CounterfactualExperiment"]
    S --> O["Simulation Lab"]
```

### 7.6 决策类型

| Decision | 含义 |
| --- | --- |
| `model_unavailable` | 影子模型调用失败 |
| `reject_plan` | 候选 PlanGraph 未通过确定性校验 |
| `block_missing_evidence` | 缺少证据或 Verifier 未通过 |
| `human_review` | Policy 要求人工审批 |
| `dry_run_execute` | 在模拟条件下可以执行，但不会调用真实工具 |

所有结果固定包含：

```json
{
  "mode": "counterfactual",
  "side_effects": "disabled",
  "dry_run": true,
  "variants": [
    {
      "variant_id": "new-policy",
      "side_effects_executed": 0
    }
  ]
}
```

### 7.7 差异字段

系统比较：

- `decision`；
- `requires_human_review`；
- `policy_version`；
- `plan_revision`；
- `plan_errors`；
- 命中的 Policy Rules；
- Evidence 数量；
- 影子模型延迟、Token 和成本。

### 7.8 当前边界

当前回放主要评估“计划和治理决策”，不是业务系统的完整数字孪生。它不会模拟 SAP 库存锁、会计期间、汇率变化、并发写冲突或外部系统瞬态错误。更完整的生产回放需要版本化数据快照、Mock Connector、时间冻结和故障注入。

## 8. Tool Gateway 中的统一执行顺序

四项治理能力最终在 Tool Gateway 汇合。对于写操作，推荐理解为以下分层：

```mermaid
flowchart TD
    C["Tool Call"] --> S["Tool Spec / Specialist Boundary"]
    S --> P["Policy-as-Code"]
    P --> H["HITL Approval Evidence"]
    H --> J["JSON Schema Validation"]
    J --> I["Agent Identity / Delegation"]
    I --> T["Taint Tracking"]
    T --> X["Idempotency / Connector / Handler"]
    X --> O["Output Schema + Output Provenance"]
    O --> A["Audit / Metrics / Online Feedback"]
```

这一顺序的核心含义是：LLM、Planner、Supervisor 和场景配置都不能直接获得业务系统写权限，最终执行权集中在确定性的 Gateway 中。

## 9. 数据库设计

迁移版本：`3c4d5e6f7081`，依赖 `2b3c4d5e6f70`。

| 表 | 用途 | 关键字段 |
| --- | --- | --- |
| `agent_delegation_grants` | 持久授权事实源 | `grant_id`、Principal、Agent、Tools、Scopes、Constraints、Status、JTI Hash、Expiry、Use Count |
| `agent_feedback_records` | 线上人工反馈 | Thread、Trace、Task、Disposition、Correction、Snapshot、Version Context |
| `online_eval_cases` | 候选和已发布评估样本 | Source Feedback、Expected Outcome、Status、Dataset Version、Reviewer |
| `counterfactual_experiments` | 回放实验 | Baseline、Variants、Result、Requester、Status |

### 9.1 多租户隔离

四张表均包含 `tenant_id`，并在 PostgreSQL 中启用：

- Row-Level Security；
- `FORCE ROW LEVEL SECURITY`；
- `USING` 和 `WITH CHECK` 租户策略；
- 租户、主体、状态、时间和任务相关索引。

SQLite 测试环境不会创建 PostgreSQL RLS，但应用层仍显式添加 `tenant_id` 条件。

### 9.2 数据保留建议

| 数据 | 建议保留策略 |
| --- | --- |
| Delegation Grant | 过期后至少保留至审计周期结束 |
| Feedback | 按隐私政策脱敏并设置用户删除流程 |
| Approved Eval Case | 版本化长期保存，删除时保留不可逆统计 |
| Counterfactual Snapshot | 默认脱敏，按实验和合规周期清理 |
| Token | 不持久化原文，只保存 JTI Hash |

## 10. API 总览

所有路径前缀为 `/api/agent/governance`。

| 方法 | 路径 | 鉴权 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/delegations` | 用户登录态 | 创建委托并返回短期 Token |
| `GET` | `/delegations` | 用户登录态 | 查询授权 |
| `POST` | `/delegations/{grant_id}/revoke` | 用户登录态 | 撤销授权 |
| `POST` | `/feedback` | 用户登录态 | 提交接受、纠正或拒绝反馈 |
| `GET` | `/online-eval/report` | Admin API Key | 获取线上评估报告 |
| `POST` | `/online-eval/cases/{case_id}/review` | Admin API Key | 审核候选 Eval Case |
| `POST` | `/counterfactual/replay` | Admin API Key | 创建反事实回放实验 |
| `GET` | `/counterfactual/{experiment_id}` | Admin API Key | 查询回放结果 |

前端通过 [`route.ts`](../frontend/app/api/agent/governance/%5B...path%5D/route.ts) 代理治理请求，并转发用户身份或管理员凭证，避免浏览器直接拼接后端内部地址。

## 11. 配置项

```env
AGENT_DELEGATION_REQUIRED_FOR_WRITES=true
AGENT_DELEGATION_TOKEN_MINUTES=15
AGENT_DELEGATION_DEFAULT_MAX_USES=1
AGENT_INFORMATION_FLOW_MODE=enforce
ONLINE_EVAL_DRIFT_THRESHOLD=0.10
ONLINE_EVAL_MINIMUM_SAMPLE_SIZE=10
COUNTERFACTUAL_MAX_VARIANTS=8
```

| 配置 | 默认值 | 生产建议 | 说明 |
| --- | ---: | ---: | --- |
| `AGENT_DELEGATION_REQUIRED_FOR_WRITES` | `false` | `true` | 是否强制写工具携带委托 |
| `AGENT_DELEGATION_TOKEN_MINUTES` | `15` | `5-15` | Token 有效期 |
| `AGENT_DELEGATION_DEFAULT_MAX_USES` | `1` | `1` | 默认可消费任务次数 |
| `AGENT_INFORMATION_FLOW_MODE` | `audit` | `enforce` | 信息流模式 |
| `ONLINE_EVAL_DRIFT_THRESHOLD` | `0.10` | 按业务校准 | 接受率下降阈值 |
| `ONLINE_EVAL_MINIMUM_SAMPLE_SIZE` | `10` | `30+` | 每个比较窗口的最小样本数 |
| `COUNTERFACTUAL_MAX_VARIANTS` | `8` | `4-8` | 单次实验 Variant 上限 |

## 12. 部署与发布步骤

### 12.1 数据库迁移

```powershell
cd backend
alembic upgrade head
alembic current
```

期望 Head 为 `3c4d5e6f7081`。

### 12.2 分阶段启用

#### 阶段 A：开发验证

```env
AGENT_DELEGATION_REQUIRED_FOR_WRITES=false
AGENT_INFORMATION_FLOW_MODE=audit
```

目标：检查所有写工具是否产生完整的 Identity、Approval、Provenance 和 Evidence 记录。

#### 阶段 B：预生产阻断

```env
AGENT_DELEGATION_REQUIRED_FOR_WRITES=true
AGENT_INFORMATION_FLOW_MODE=enforce
```

目标：使用 Seed Data、Simulation Lab、故障注入和 E2E 测试验证合法请求通过、越权请求被阻断。

#### 阶段 C：生产启用

生产启用前至少确认：

- 所有写工具都注册 Tool Spec、输入输出 Schema、风险级别和审批要求；
- 委托令牌不会进入应用日志、浏览器 URL 或第三方分析平台；
- PostgreSQL RLS 已启用并验证跨租户访问失败；
- Online Eval 报告有告警负责人；
- Counterfactual Replay 的模型成本和并发受限；
- 有恢复开关，但任何降级都写入审计并触发告警。

### 12.3 回滚策略

业务紧急情况下，可以先将信息流从 `enforce` 降为 `audit`，但不建议关闭 Policy、审批、租户隔离或 Tool Gateway。数据库迁移降级会删除四张治理表，执行前必须完成备份和数据导出。

## 13. 可观测性与审计

每次治理决策建议至少记录：

- `tenant_id`、`thread_id`、`trace_id`、`task_id`；
- Principal、Role、Agent、Specialist；
- Grant ID、Purpose、Reason Code，不记录 Token 原文；
- Tool、Risk Level、Side Effect、Approval ID；
- 输入来源标签、Evidence ID、Verifier、字段指纹；
- Policy、Prompt、Model、Plan 版本；
- 执行结果、延迟、Token、成本和错误类型；
- Feedback、Eval Case 和 Counterfactual Experiment ID。

重点告警建议：

| 告警 | 触发条件 |
| --- | --- |
| 委托拒绝突增 | `DELEGATION_*` 拒绝率超过基线 |
| 信息流违规 | `enforce` 阻断或 `audit` 违规持续增加 |
| 线上质量漂移 | 样本充足且接受率下降超过阈值 |
| 高人工接管率 | Human Override Rate 超过场景目标 |
| 回放决策变化 | 新版本相对基线出现高风险自动执行 |
| Shadow Model 成本异常 | Token、成本或超时超过预算 |

## 14. 威胁与控制映射

| 威胁 | 主要控制 | 剩余风险 |
| --- | --- | --- |
| Prompt Injection 驱动退款 | Taint Tracking、Policy、Verifier、HITL | 深层对象传播仍需增强 |
| 用户让 Agent 越权调用工具 | Capability Token、工具白名单、资源范围 | 对称密钥需升级 KMS 非对称签名 |
| 跨租户访问 | Tenant Claim、应用查询条件、PostgreSQL RLS | 需持续做 RLS 回归和渗透测试 |
| 重放已用令牌 | JTI Hash、Max Uses、入口行锁、短 TTL | 执行中即时撤销需 Introspection |
| 人工反馈污染 Eval | Candidate + Review + Dataset Version | Reviewer 权限和双人复核可加强 |
| 新 Prompt 导致行为退化 | Online Eval、Counterfactual Replay、Plan Validator | 回放不等同真实 ERP 数字孪生 |
| 敏感信息进入日志或模型 | Masking、影子输入脱敏、证据数量限制 | 需接企业 DLP 和保留策略 |
| Counterfactual 误触发写入 | 不调用 Tool handler，固定 dry-run | 后续扩展时必须维持执行隔离 |

## 15. 测试策略

### 15.1 已覆盖的核心场景

- 正确委托可访问被授权工具；
- 错误 Principal、Tenant、Agent、Tool、Resource、Amount、Currency 被拒绝；
- 撤销、过期和超过使用次数的 Grant 被拒绝；
- 高风险写入在缺少可信 Provenance 时被阻断；
- Verifier 通过后生成带 Evidence 和指纹的去污记录；
- 工具输出被标记为外部不可信；
- `correct` 和 `reject` 生成候选 Eval Case，`accept` 不生成；
- 候选样本经过审核后发布到版本化数据集；
- Online Eval 计算接受率、人工接管率和漂移；
- Counterfactual 比较 Policy、Prompt、Model Output 和 PlanGraph；
- 影子模型只能生成候选计划；
- 回放结果始终为 dry-run 且副作用为 0；
- 治理表迁移、索引和 PostgreSQL RLS 定义存在。

### 15.2 建议执行命令

```powershell
cd backend
pytest tests/test_advanced_agent_governance.py -q
pytest -q
```

```powershell
cd frontend
npm run type-check
```

当前仓库最近一次完整后端测试结果为：`307 passed, 1 skipped`；前端 TypeScript 类型检查通过。

### 15.3 生产前额外验证

- PostgreSQL 并发消费同一 Grant，只允许一个请求成功；
- 跨租户 RLS 查询和写入均失败；
- KMS 密钥轮换期间新旧 Token 行为符合预期；
- 影子模型超时、限流和供应商故障不会影响主流程；
- 反馈数据中的 PII 能按用户请求删除；
- 大量 Counterfactual Variant 不会挤占在线 Agent 资源；
- 信息流 `audit` 日志中的字段覆盖率达到上线门槛。

## 16. 故障排查

### 16.1 写操作返回 403

依次检查：

1. `AGENT_DELEGATION_REQUIRED_FOR_WRITES` 是否开启；
2. Chat 请求是否携带 `delegation_token`；
3. Grant 是否 active、未过期且 `use_count < max_uses`；
4. Principal、Role、Tenant 和 Agent 是否一致；
5. 工具、资源、金额、币种和 Specialist 是否在授权范围；
6. `approval_id` 是否与 Grant 绑定一致；
7. Audit 中的 `reason_code`。

### 16.2 合法写操作被信息流阻断

检查：

- Tool Spec 是否声明了正确风险和副作用；
- 关键输入是否存在 `data_provenance`；
- Evidence Graph 是否包含对应 Evidence ID；
- Verifier 是否通过并执行去污；
- 当前字段值是否在去污后被修改；
- `AGENT_INFORMATION_FLOW_MODE` 是否直接从 `off` 切到 `enforce`，导致历史路径没有覆盖。

### 16.3 Online Eval 无漂移结论

这通常不是错误。只有当前窗口和上一窗口都达到最小样本数时才判断漂移。还需要检查反馈是否写入正确 Tenant、时间窗口和版本上下文。

### 16.4 Counterfactual 返回 `model_unavailable`

检查 Provider API Key、Model 名称、超时、限流和结构化输出。即使影子模型失败，主业务流程也不会受影响，实验会保留失败原因。

### 16.5 回放显示 `block_missing_evidence`

确认 Audit Log 是否保存了 `evidence_graph` 和 `verification_result`。旧任务若没有这些字段，应使用显式脱敏 `baseline_snapshot`，不能把缺失证据误判为候选版本问题。

## 17. 面试讲解建议

### 17.1 三分钟版本

这个平台把 LLM 限制在理解、规划和解释层，实际业务写入统一经过 Tool Gateway。用户先签发一个短期委托，绑定 Principal、Tenant、Agent、Tool、Resource、金额、币种和审批凭证；任务入口消费持久 Grant，工具执行前再次检查能力范围。

系统不只验证参数格式，还追踪参数来源。用户输入、RAG、模型生成值和外部工具结果默认不可信，必须由 Evidence Graph 和独立 Verifier 验证，生成绑定 Evidence ID 和字段指纹的去污记录，才能驱动退款等高风险写操作。

线上结果支持 accept、correct、reject 反馈。纠正和拒绝只进入候选评估集，管理员审核后才发布到版本化数据集。上线前还可以用 Counterfactual Replay 在不执行任何真实工具的情况下，对比不同模型、Prompt、Policy 和 PlanGraph 的决策变化。这四层形成“授权、证据、执行、反馈、回放”的治理闭环。

### 17.2 面试官追问：为什么不让 Agent 自己判断权限

权限属于确定性安全边界，不能依赖概率模型。LLM 可以理解意图和生成计划，但授权必须由可审计的 Token Claims、数据库 Grant、Policy、审批记录和 Tool Gateway 共同决定。

### 17.3 面试官追问：这是不是完整商业生产能力

核心控制链路已经实现并有自动化测试，但商业生产还需要外部基础设施证明，包括 KMS 非对称签名、真实 PostgreSQL 并发和 RLS 压测、告警值班、隐私保留与删除、真实 ERP 沙箱和执行中即时撤销。这些不应仅凭代码仓库宣称已经完成。

## 18. 生产增强路线

### P0：上线前安全加固

- 将对称 JWT 升级为 KMS 托管的非对称签名、JWKS 和密钥轮换；
- 增加 Grant Introspection 或 Redis 撤销列表，实现执行中即时撤销；
- 为全部高风险 Tool 建立字段级 Provenance 覆盖率门禁；
- 对管理员 API Key 增加 RBAC、短期凭证和操作审计；
- 在真实 PostgreSQL 验证 RLS、并发消费和恢复流程。

### P1：治理运营化

- Online Eval 漂移接入告警、工单和版本回滚审批；
- Eval Case 增加双人复核、冲突处理和数据集血缘；
- Counterfactual Replay 增加定时批量回放和版本准入阈值；
- 将 Governance Event 接入 OpenTelemetry、SIEM 和审计报表；
- 建立 PII 保留、导出、删除和法务冻结策略。

### P2：高级信息流与数字孪生

- JSONPath 和数组元素级 Taint Tracking；
- 跨节点、跨 Agent、跨工具的数据依赖图；
- SAP/OData 版本化快照、时间冻结和故障注入；
- 对审批、库存、财务清账和补偿事务做完整数字孪生回放；
- 用统计显著性、风险加权收益和业务成本共同评估新版本。

## 19. 完成度说明

| 能力 | 当前状态 | 不能过度宣称的部分 |
| --- | --- | --- |
| Agent Identity + 委托授权 | 核心代码、持久 Grant、JWT、撤销、次数、范围和审计已完成 | KMS/JWKS、执行中即时撤销尚未接入 |
| Taint Tracking | 顶层字段来源、门禁、去污、指纹和输出污染传播已完成 | 深层 JSONPath 和全链路数据血缘尚未完成 |
| Online Eval | Feedback、Candidate、Review、Dataset Version、版本聚合和基础漂移已完成 | 统计显著性、自动告警和生产运营流程需外部系统 |
| Counterfactual Replay | 审计快照、预生成/在线影子计划、Policy/Plan 比较和零副作用持久化已完成 | 真实 ERP 数字孪生和全业务副作用模拟尚未完成 |

这份完成度边界既适用于技术评审，也适用于简历和面试表达：可以明确展示已经实现的工程深度，同时保留对真实生产依赖的诚实判断。
