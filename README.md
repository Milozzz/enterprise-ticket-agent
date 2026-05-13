# Enterprise Ticket Agent — 企业级 AI 退款自动化系统

![CI](https://img.shields.io/badge/CI-ruff%20%7C%20pytest%20%7C%20evals%20%7C%20docker-brightgreen)
![Docker Compose](https://img.shields.io/badge/Docker%20Compose-postgres%20%7C%20redis%20%7C%20backend%20%7C%20frontend-blue)
![Agent Safety](https://img.shields.io/badge/Agent%20Safety-prompt%20injection%20%7C%20RBAC%20%7C%20PII-orange)

> 基于 LangGraph + Generative UI 的全栈企业退款工单系统，集成 Human-in-the-Loop 审批流、实时流式 UI、全链路可观测性与 RAG 政策问答。

---

## 目录

- [项目简介](#项目简介)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [模块详解](#模块详解)
  - [Agent 核心层](#agent-核心层)
  - [数据库层](#数据库层)
  - [API 层](#api-层)
  - [前端层](#前端层)
- [核心设计决策](#核心设计决策)
- [业务流程](#业务流程)
- [测试体系](#测试体系)
- [快速开始](#快速开始)
- [环境变量配置](#环境变量配置)
- [项目结构](#项目结构)
- [可观测性与调试](#可观测性与调试)
- [安全与权限](#安全与权限)

---

## 项目简介

本项目是一个**面向企业的 AI 退款工单处理系统**，旨在用 AI Agent 自动化处理电商退款申请的完整业务流程，包括：

- 自然语言意图识别（支持中英文）
- 订单数据查询与校验
- 风险评分与自动/人工分流
- Manager 角色审批高风险退款
- 自动执行退款并发送财务通知邮件
- 政策问答（基于 RAG）

系统的核心特点是**完全可审计**、**有状态可恢复**、**实时可视化**：每一步 Agent 决策都写入审计日志，通过 Graph 检查点实现进程重启后状态续传，前端通过 Server-Sent Events 实时渲染动态 UI 组件（Generative UI）。

### 当前工程状态

| 项目 | 状态 |
|------|------|
| 后端测试 | `139 passed` |
| 规则引擎 Evals | `20/20` pass，Intent / Order-ID / Reason Accuracy 均为 `100%` |
| CI 覆盖 | Ruff、Pytest、Rules-only Evals、Docker build check |
| 本地运行 | Docker Compose 一键启动 PostgreSQL、Redis、FastAPI、Next.js |

### 部署证明

| 项目 | 说明 |
|------|------|
| Docker Compose | `docker compose up --build` 会启动 PostgreSQL、Redis、Backend、Frontend，并在后端启动前执行 `alembic upgrade head && python seed.py` |
| Seed Data | 自动写入 demo 用户、3 个订单、3 条工单、RefundLog 与 AuditLog 样本，Dashboard 首屏有数据 |
| CI Badge | README 顶部展示 lint、pytest、evals、Docker build check 覆盖 |
| Screenshots | [Dashboard Observability](docs/screenshots/dashboard-observability.svg)、[Agent Refund Flow](docs/screenshots/agent-refund-flow.svg) |

线上部署推荐使用 Render + Vercel，步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。后端支持 Render Blueprint，前端支持 Vercel `frontend` 子目录部署。

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Next.js 15)                  │
│   ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  │
│   │ Chat UI  │  │ Dashboard    │  │ Generative UI    │  │
│   │ useChat  │  │ ChainReplay  │  │ OrderCard        │  │
│   │ Zustand  │  │ Analytics    │  │ ApprovalPanel    │  │
│   └────┬─────┘  └──────────────┘  │ RefundTimeline   │  │
│        │ SSE / REST               │ RiskAlert        │  │
└────────┼─────────────────────────────────────────────────┘
         │
┌────────▼─────────────────────────────────────────────────┐
│                   Backend (FastAPI)                        │
│   ┌───────────────────────────────────────────────────┐  │
│   │              LangGraph Agent                       │  │
│   │  classify → lookup → risk → [interrupt] →         │  │
│   │  human_review → execute_refund → notification     │  │
│   │  RAG policy: query_policy → answer_policy_node    │  │
│   └───────────────────────────────────────────────────┘  │
│   ┌──────────────┐  ┌──────────┐  ┌──────────────────┐  │
│   │  Audit Trail  │  │ Langfuse │  │ State Machine    │  │
│   │  (AuditLog)   │  │ Callback │  │ (9 States)       │  │
│   └──────────────┘  └──────────┘  └──────────────────┘  │
└──────────────────────────────┬───────────────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
┌────────▼──────┐   ┌──────────▼──────┐   ┌─────────▼──────┐
│  PostgreSQL   │   │     Redis        │   │   Langfuse     │
│  - Orders     │   │  - Cache         │   │   Cloud        │
│  - Tickets    │   │  - Checkpoints   │   │  (Traces)      │
│  - AuditLogs  │   │  - Sessions      │   │               │
└───────────────┘   └─────────────────┘   └────────────────┘
```

---

## 技术栈

### 后端

| 技术 | 用途 | 选型理由 |
|------|------|---------|
| **FastAPI** | HTTP 框架 | 原生异步、自动 OpenAPI 文档、Pydantic 深度集成，适合 SSE 流式响应 |
| **LangGraph** | Agent 编排 | 显式状态机图结构，支持 `interrupt_before` 人工介入，检查点持久化；相比 ReAct/AutoGPT 等方案，执行路径完全透明可审计，满足企业合规要求 |
| **Google Gemini 2.0 Flash** | LLM | 高性价比、结构化 JSON 输出稳定、中文理解能力强 |
| **LangChain** | LLM 工具层 | 标准化 Tool 接口、消息格式、Callback 系统，与 LangGraph 深度集成 |
| **SQLAlchemy 2.0 (async)** | ORM | 异步会话模式，配合 asyncpg 实现全链路非阻塞 DB 操作 |
| **asyncpg** | PG 驱动 | 比 psycopg2 性能高 3-5x，纯异步，适合 FastAPI 并发场景 |
| **Alembic** | 数据库迁移 | 与 SQLAlchemy 原生集成，支持版本化迁移，生产环境安全变更 |
| **Redis 7** | 缓存 + 检查点 | 用于 LangGraph 状态检查点（Graph 暂停后进程重启仍可恢复）和响应缓存 |
| **Upstash Redis** | Serverless 检查点 | 无服务器部署场景下替代本地 Redis，支持 HTTP 协议，无需长连接 |
| **Langfuse** | 可观测性 | 记录每个节点的 token 用量、延迟、输入输出，支持全链路 trace 关联，生产环境成本分析必备 |
| **NumPy** | 向量相似度 | 用于 TF-IDF + 余弦相似度实现轻量 RAG，10 条政策文档无需引入外部向量数据库 |
| **structlog** | 结构化日志 | JSON 格式输出，便于 ELK/Loki 采集，避免非结构化日志难以检索 |
| **Pydantic v2** | 数据验证 | 请求/响应模型自动校验，与 FastAPI 深度集成，类型安全 |

### 前端

| 技术 | 用途 | 选型理由 |
|------|------|---------|
| **Next.js 15 (App Router)** | 全栈框架 | Server Components、Route Handlers 充当 BFF 层，SSE 转发、协议适配与鉴权在服务端完成，避免前端直连后端泄露密钥 |
| **React 19** | UI | 新的并发特性，配合 Generative UI 动态渲染运行时组件 |
| **Vercel AI SDK v3** | 流式 AI 接入 | `useChat` hook 封装 SSE 流管理、重试、消息状态；`message_annotations` 机制传递 UI 组件指令 |
| **Zustand** | 全局状态 | 轻量、无 Provider 包裹，用于 auth 角色切换场景 |
| **Tailwind CSS v4** | 样式 | 原子类快速迭代，配合 shadcn/ui 实现一致的设计系统 |
| **shadcn/ui** | 组件库 | 基于 Radix UI 的可访问性组件，代码直接 ingest 到项目，完全可定制 |
| **Framer Motion** | 动画 | 流程图、时间线、状态指示器的动画效果，提升审批流程的可视化体验 |
| **Recharts** | 图表 | Dashboard 数据可视化，响应式、声明式 API |

### 基础设施

| 服务 | 用途 |
|------|------|
| **PostgreSQL 16 + pgvector** | 主数据库（pgvector 扩展预留，当前 RAG 用 NumPy） |
| **Redis 7** | Agent 检查点持久化、API 响应缓存（TTL 300s） |
| **Docker Compose** | 一键启动全栈（postgres、redis、backend、frontend） |
| **Gmail SMTP** | 财务团队退款通知邮件（开发环境无配置则 mock 输出到控制台） |

---

## 模块详解

### Agent 核心层

#### `backend/app/agent/state.py` — 状态定义

使用 LangGraph 的 `TypedDict` 定义全局 `AgentState`，所有节点共享同一状态对象，避免参数传递混乱。

关键字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `messages` | `list` | LangChain 消息历史，使用 `add_messages` reducer 累加 |
| `intent` | `str` | 分类结果：`refund` / `query_order` / `query_policy` / `other` |
| `order_detail` | `dict` | 查询到的订单详情 |
| `risk_score` | `int` | 风险评分 0-100 |
| `requires_human_approval` | `bool` | 是否需要人工审批 |
| `human_decision` | `str` | 审批决策：`approve` / `reject` |
| `ui_events` | `list` | 待发送到前端的 UI 组件指令列表 |
| `thread_id` | `str` | LangGraph 检查点 ID，每次对话唯一 |
| `trace_id` | `str` | 贯穿前后端的全链路追踪 ID |

#### `backend/app/agent/graph.py` — Agent 图编排

用 LangGraph `StateGraph` 构建显式有向图，7 个节点通过条件路由连接：

```
classify_intent
    │
    ├─ intent=refund ──→ lookup_order ──→ check_risk
    │                                          │
    │                              risk低/中 ──┤── risk高 ──→ [INTERRUPT] ──→ human_review
    │                                          │                                      │
    │                                    execute_refund ←── approve ←────────────────┘
    │                                          │         reject → END
    │                                    send_notification
    │                                          │
    │                                         END
    │
    ├─ intent=query_policy ──→ answer_policy_node ──→ END
    │
    └─ intent=other/query_order ──→ answer_node ──→ END
```

**关键设计**：`interrupt_before=["human_review"]` 使图在高风险场景下自动暂停，序列化状态到检查点，等待前端 `/resume` 请求后续传。

#### `backend/app/agent/nodes/` — 各处理节点

**`classifier.py`** — 意图与实体提取
- **主策略**：调用 Gemini 2.0 Flash，结构化 JSON 输出，提取 intent、order_id、reason、user_description
- **降级策略**：当 LLM 不可用时切换到正则规则引擎（支持"破损"、"发错"、"未收到"等中文关键词）
- **解决的问题**：避免 LLM API 超时或限流时整个流程阻塞，生产环境必须有降级路径

**`order_lookup.py`** — 订单数据查询
- 调用 `get_order_detail` Tool 查询 PostgreSQL
- 生成 `order_card` UI 事件，前端实时渲染订单卡片（商品列表、金额、状态、收货地址）

**`risk_check.py`** — 风险评分
- 规则引擎：金额 > ¥500 触发人工审核标志；金额 > ¥1000 额外加分；特定用户 ID 有欺诈记录
- 最终评分 0-100，低风险(<30) 自动审批，高风险(≥60) 强制人工
- 在 DB 创建 Ticket 记录（状态 PENDING），生成 `risk_alert` + `approval_panel` UI 事件

**`human_review.py`** — 人工审批节点
- 该节点在 `interrupt_before` 配置下**从不被直接执行**，仅在图恢复后执行
- 校验 `reviewer_role` 必须为 MANAGER（HTTP 403 拒绝普通用户）
- 更新 Ticket 状态为 APPROVED / REJECTED，记录 operator_id

**`refund.py`** — 退款执行
- 调用 `execute_refund` Tool，生成唯一退款单号（REFUND_XXXXX）
- 更新 Ticket 状态为 COMPLETED
- 生成 `refund_timeline` UI 事件，展示四阶段退款进度

**`notification.py`** — 财务通知
- 调用 `send_notification` Tool，通过 Gmail SMTP 发送邮件
- **幂等性保障**：同一 (order_id, refund_id) 组合不会重复发送邮件
- 开发环境无 Gmail 配置时自动降级为控制台输出

**`policy.py`** — 政策问答（RAG）
- 调用 `search_policy_raw` Tool：基于 TF-IDF + 余弦相似度检索最相关的 2 条政策文档
- 将政策原文作为上下文注入 Prompt，Gemini 生成有依据的回答，不凭空捏造

#### `backend/app/agent/tools/` — Function Calling 工具集

| 工具 | 功能 |
|------|------|
| `get_order_detail(order_id)` | 查询订单详情（商品、金额、状态、地址） |
| `check_risk_level(order_id, amount, user_id, reason)` | 计算风险分 0-100，返回风险级别和原因列表 |
| `execute_refund(order_id, amount, ticket_id)` | 执行退款（模拟支付网关），返回退款单号 |
| `send_notification(to_email, order_id, amount, refund_id, ticket_id)` | 发送财务通知邮件（幂等） |
| `get_ticket_status(order_id)` | 查询工单状态和时间线 |
| `search_policy_raw(query, top_k=2)` | TF-IDF 检索退款政策文档 |

#### `backend/app/agent/state_machine.py` — 业务状态机

定义退款工单的 9 个业务状态及合法流转路径：

```
CREATED → CLASSIFIED → ORDER_LOADED → RISK_EVALUATED
                                              │
                              ┌───────────────┼───────────────┐
                              ↓               ↓               ↓
                          APPROVED      PENDING_APPROVAL   FAILED
                              │               │
                              ↓         (approve/reject)
                          REFUNDED           │
                              │         REJECTED (terminal)
                              ↓
                          COMPLETED (terminal)
```

非法状态流转（如从 REJECTED 跳到 REFUNDED）会抛出 `InvalidStateTransitionError`，保证业务流程完整性。

---

### 数据库层

#### `backend/app/db/models.py` — 数据模型

**User** — 用户表
```
id | name | email | role(USER/AGENT/MANAGER) | created_at
```

**Order** — 订单表
```
id(string) | user_id(FK) | amount | status | items(JSON) | shipping_address | created_at | tracking_number
```
`items` 为 JSON 数组，每项包含商品 id、名称、图片 URL、数量、单价。

**Ticket** — 退款工单表
```
id | order_id(FK) | requester_id(FK→User) | operator_id(FK→User, nullable)
   | thread_id(indexed) | status(PENDING/APPROVED/REJECTED/COMPLETED)
   | reason | created_at
```
`thread_id` 与 LangGraph 检查点 ID 一一对应，是连接 Agent 状态与业务工单的关键外键。

**RefundLog** — 退款记录表
```
id | ticket_id(FK) | refund_id(unique) | amount | processed_at
```

**AuditLog** — 审计日志表
```
id | thread_id(indexed) | trace_id(indexed) | node_name | event_type
   | input_data(JSON, 已脱敏) | output_data(JSON, 已脱敏)
   | duration_ms | success | created_at
```
每个节点的执行都写入 AuditLog，支持按 thread_id 回放完整执行链。

---

### API 层

#### `backend/app/api/routes/chat.py`

**`POST /api/agent/chat`** — 主 Agent 流式端点

接收用户消息，启动 LangGraph 图执行，通过 Server-Sent Events 实时推送：

```
event: meta   → {trace_id, thread_id}         # 追踪 ID 元信息
event: ui     → {type, props}                  # Generative UI 组件指令
event: text   → {content: "..."}              # 流式文本
event: done   → {}                            # 流结束
```

关键特性：
- **Redis 缓存**：相同 (user_id, 消息内容) 的非退款查询命中缓存（TTL 300s），避免重复 LLM 调用
- **Langfuse 追踪**：每个节点执行都通过 Callback Handler 上报到 Langfuse
- **AuditLog 写入**：节点名称、耗时、输入输出（脱敏）、成功标志全量记录
- **优雅降级**：DB 宕机返回用户友好提示，不暴露堆栈信息

**`POST /api/agent/resume`** — 人工审批续传端点

```json
{
  "thread_id": "xxx",
  "action": "approve|reject",
  "reviewer_id": "manager_001",
  "reviewer_role": "MANAGER",
  "comment": "金额合规，批准"
}
```

- 校验 reviewer_role 必须为 MANAGER，否则 HTTP 403
- 从 Redis/PostgreSQL 检查点恢复 Graph 状态，注入 `human_decision`，继续执行
- **降级策略**：检查点丢失时直接操作数据库更新 Ticket 状态，并合成 UI 事件返回

**`GET /api/agent/audit/{thread_id}`** — 审计日志查询

**`GET /api/agent/replay/{thread_id}`** — 执行链回放数据（含节点耗时、状态快照）

**`GET /api/agent/debug/{thread_id}`** — Agent 状态调试（开发环境）

---

### 前端层

#### `frontend/app/page.tsx` — 主对话界面

- 左侧：聊天区域，使用 Vercel AI SDK `useChat` hook 管理流式消息
- 右侧：实时审计日志面板（每 2 秒轮询）
- 动态渲染 Generative UI 组件：根据后端推送的 `ui` 事件类型实例化对应 React 组件
- 每次提交生成新 `thread_id`，保证检查点隔离

#### `frontend/app/dashboard/page.tsx` — 运营仪表盘

- 退款统计：总量、通过率、平均处理时长
- **节点耗时图表**（新增）：可视化各 Agent 节点的 avg / P95 耗时，P95 > 3s 标红警告，直观定位性能瓶颈
- **ChainReplay** 组件：可视化任意 thread 的节点执行时间线，展示各节点耗时、成功/失败、状态快照
- 失败链路列表：快速定位 `success=false` 的异常执行

#### `frontend/components/generative/` — Generative UI 组件集

这是系统的核心创新点——后端节点通过 SSE 推送 `{type, props}` 指令，前端动态挂载对应 React 组件，实现**运行时 UI 组合**：

| 组件 | 触发节点 | 功能 |
|------|---------|------|
| `AgentThinkingStream` | 所有节点 | 实时展示节点执行进度（分类→查询→风险→执行→通知），带动画状态指示器 |
| `OrderCard` | `lookup_order` | 订单详情卡（商品列表、图片、金额、状态、收货地址） |
| `RiskAlert` | `check_risk` | 风险评分展示（0-100 色阶）+ 风险原因列表 |
| `ApprovalPanel` | `check_risk`（高风险） | Manager 审批面板，Approve/Reject 按钮 + 仿真回放动画（可调速 0.5x/1x/2x） |
| `RefundTimeline` | `execute_refund` | 四阶段退款进度（提交→审批→处理→到账） |
| `EmailPreview` | `send_notification` | 已发送财务邮件预览（收件人、主题、正文、时间戳） |

#### `frontend/app/api/chat/route.ts` — SSE 协议适配器

这是 Next.js Route Handler 充当 BFF（Backend for Frontend）的关键层：

- 接收 Vercel AI SDK `useChat` POST 请求
- 转发到 Python 后端 `/api/agent/chat`
- 将 Python SSE 格式（`event: ui\ndata: {...}`）转换为 AI SDK v3 协议（`2:[...]` message_annotations）
- 在服务端集成 Langfuse JS SDK，追踪前端请求

**为什么需要这一层？** 直接在浏览器调用后端会暴露 API 密钥、有跨域限制，且无法做服务端 Langfuse 追踪。BFF 层解决了这三个问题，同时可在此做缓存、限流等横切关注点。

---

## 核心设计决策

### 1. 为什么用 LangGraph 而非直接调用 LLM？

**问题**：企业退款流程不是单次对话，而是包含多步骤、条件分支、等待人工介入的**业务流程**。直接调用 LLM 无法在等待 Manager 审批时暂停——进程重启后状态丢失，也无法精确控制哪一步需要人工介入或回放历史执行轨迹。

**解决方案**：LangGraph 的显式状态机图让每一步都明确、可审计。`interrupt_before=["human_review"]` 使图在高风险节点前自动序列化到 Redis/PostgreSQL，进程重启后 `graph.ainvoke(None, config)` 即可从断点续传。这在合规要求严格的企业场景下不可或缺。

---

### 2. 为什么用 Generative UI（Server-Driven UI）？

**问题**：退款流程的每一步呈现不同信息（订单详情 vs 风险评分 vs 审批面板），传统做法需要前端预先写死所有可能的 UI 状态，与业务逻辑高度耦合。

**解决方案**：后端节点决定推送什么 UI 组件，前端动态实例化。这种**服务端驱动 UI** 模式解耦了业务逻辑和展示逻辑——增加新节点只需新增一个 React 组件，无需修改主界面代码。

技术实现：Python SSE 推送 `{type: "OrderCard", props: {...}}`，Next.js Route Handler 转换为 AI SDK `message_annotations`，前端 `useChat` 消费并动态渲染。

---

### 3. 为什么用 LLM + Regex 双重分类策略？

**问题**：生产环境中 LLM API 会出现超时、限流、服务中断。退款分类是入口，如果这里失败整个流程都阻塞。

**解决方案**：先用 Gemini 尝试结构化 JSON 提取，失败时切换到正则规则引擎（不依赖任何外部服务）。规则引擎覆盖最常见的退款场景（中英文关键词匹配），保证系统在 LLM 不可用时仍能运行，实现真正的高可用。

---

### 4. 为什么选 NumPy TF-IDF 而非向量数据库做 RAG？

**问题**：政策问答只有 10 条文档。引入 Pinecone/Weaviate 等向量数据库增加了运维复杂度、网络延迟和成本，但对这个数据规模没有性能收益。

**解决方案**：在服务启动时用 NumPy 计算 TF-IDF 矩阵，查询时做余弦相似度排序。无外部依赖、零额外延迟、百分百可控。当文档超过数百条时，可平滑迁移到 pgvector（已在 PostgreSQL 中预装）。

---

### 5. 为什么全面实现幂等性？

**问题**：网络重试、用户刷新、进程重启都可能导致相同操作执行多次。退款重复执行会导致财务损失，邮件重复发送会骚扰收件人。

**解决方案**：
- `upsert_ticket(thread_id)` — 同一 thread 只创建一条工单
- 退款以 `ticket_id` 为幂等键，防止双重退款
- 邮件通知用 `(order_id, refund_id)` 哈希标记已发送集合，进程内去重

---

### 6. 为什么用 Redis 做 LangGraph 检查点而非内存？

**问题**：`MemorySaver` 检查点存在进程内存中，一旦服务重启或容器调度，所有等待人工审批的 Graph 状态全部丢失，Manager 批准时无法续传。

**解决方案**：生产环境使用 `AsyncPostgresSaver`（PostgreSQL）或 Upstash Redis 存储检查点。状态序列化到外部存储后，任意进程实例都可以恢复任意 thread 的状态，实现真正的分布式有状态 Agent。

---

## 业务流程

### 低风险退款（全自动）

```
用户: "订单 789012 申请退款，商品破损"
  ↓ classify_intent: intent=refund, order_id=789012, reason=damaged
  ↓ lookup_order: 查到订单，金额 ¥320，状态已发货
  ↓ UI: OrderCard 渲染订单卡片
  ↓ check_risk: 风险分=15（低），自动审批通过
  ↓ UI: RiskAlert 显示风险评估结果
  ↓ execute_refund: 退款成功，单号 REFUND_ABC123
  ↓ UI: RefundTimeline 展示四阶段进度
  ↓ send_notification: 邮件已发送财务团队
  ↓ UI: EmailPreview 展示邮件内容
  ✅ 退款完成，预计 3 个工作日到账
```

### 高风险退款（需 Manager 审批）

```
用户: "订单 999999 申请退款"（金额 ¥600）
  ↓ classify_intent + lookup_order（同上）
  ↓ check_risk: 风险分=45（金额>¥500），需人工审核
  ↓ UI: ApprovalPanel 渲染审批面板
  ⏸ LangGraph INTERRUPT — 状态序列化到 Redis

  [Manager 登录，切换角色为 MANAGER]
  Manager 点击 "批准"
  ↓ POST /api/agent/resume {action: "approve", reviewer_role: "MANAGER"}
  ↓ 后端校验 MANAGER 权限，从 Redis 恢复 Graph 状态
  ↓ human_review: 更新工单状态 APPROVED，记录 operator_id
  ↓ execute_refund + send_notification（同低风险流程）
  ✅ 退款完成
```

### 政策问答（RAG）

```
用户: "七天无理由退款怎么算？"
  ↓ classify_intent: intent=query_policy
  ↓ answer_policy_node: TF-IDF 检索，找到政策 P001、P003
  ↓ Gemini: 基于政策原文生成回答，并返回 policy_citations
  ✅ "根据政策 P001，消费者在收货后 7 个自然日内可申请无理由退款..."
  ↳ References: P001 七天无理由退款, P003 发错商品退款
```

---

## 测试体系

### 测试分层

```
tests/
├── test_e2e_refund_flow.py      # 端到端集成测试（真实 LangGraph 图 + 真实 SQLite）
├── test_sse_integration.py      # SSE 协议 + 路由集成测试
├── test_api_routes.py           # FastAPI 路由 happy path
├── test_answer_node.py          # answer_node ReAct 行为单元测试
├── test_auth_and_ratelimit.py   # JWT 认证 + 限流中间件
├── test_classifier.py           # 意图分类（LLM + 规则引擎）
├── test_nodes.py                # 核心节点单元测试
├── test_risk_scoring.py         # 风险评分计算
└── test_edge_cases.py           # 边界情况与权限校验
```

### 端到端集成测试

`test_e2e_refund_flow.py` 覆盖三条核心业务路径，使用**真实 LangGraph StateGraph + 真实 SQLite 数据库**，只 mock LLM 调用、邮件发送、Redis 连接：

| 路径 | 测试内容 |
|------|---------|
| **Path A — 低风险自动退款** | SSE 序列完整（meta→ui→text→done）、meta 含 trace_id、无 interrupt 事件 |
| **Path B — 高风险 → 批准** | interrupt 事件触发、`/resume approve` 流程完成、非 MANAGER 角色 403 |
| **Path C — 高风险 → 拒绝** | 两步流程（提交+拒绝）均以 done 结束 |
| **边界情况** | 订单不存在优雅降级、DB 宕机不暴露异常、query_order 意图路由正确 |

运行测试：

```bash
cd backend
pytest tests/ -v --tb=short

# 仅跑端到端测试
pytest tests/test_e2e_refund_flow.py -v

# 运行 Evals（规则引擎，无需 API Key）
python -m evals.run_evals
```

### Evals 框架

`backend/evals/` 包含 20 个标注样本（`golden_dataset.json`），覆盖退款申请、工单查询、政策问答、订单号抽取与边界歧义场景。当前规则引擎模式（无需 API Key）评估结果：

| 指标 | 当前结果 |
|------|----------|
| Overall Pass Rate | 100.0% (20/20) |
| Intent Accuracy | 100.0% (20/20) |
| Order-ID Accuracy | 100.0% (6/6) |
| Reason Accuracy | 100.0% (20/20) |

报告产物示例见 `backend/evals/eval_report_sample.json`，真实运行会生成被 `.gitignore` 忽略的 `backend/evals/results_<run_id>.json`。报告包含：

| 字段 | 说明 |
|------|------|
| `metrics` | Overall / Intent / Order-ID / Reason Accuracy，以及平均延迟 |
| `tag_stats` | 按 `refund`、`query_policy`、`explicit_order_id` 等标签拆分准确率 |
| `cases` | 每条样本的输入、期望、预测、错误原因、延迟和分类方法 |
| `quality_gates` | CI 阈值与通过状态，便于接入发布门禁 |

CI 质量门阈值为 75% 意图准确率；同时支持 `--llm` 模式评估真实 LLM 分类效果。

---

## 快速开始

### 前置条件

- Docker & Docker Compose
- Google Gemini API Key（可选；无 Key 时会走规则/检索降级，部分 LLM 回答不可用）
- Gmail App Password（可选，无则 mock 到控制台）
- Langfuse 账号（可选，无则跳过追踪）

### Docker 一键启动

```bash
# 1. 克隆项目
git clone <repo-url>
cd enterprise-ticket-agent

# 2. 配置环境变量
cp .env.example .env
# 如需真实 LLM 分类/RAG 回答，填写 GOOGLE_API_KEY；不填也可体验规则降级与主体流程

# 3. 启动所有服务
docker compose up --build

# 4. 访问
# 前端: http://localhost:3000
# 后端 API: http://localhost:8000
# API 文档: http://localhost:8000/docs
```

### 本地开发模式

```bash
# 后端
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 启动数据库（需要 Docker）
docker compose up postgres redis -d

# 运行数据库迁移
alembic upgrade head

# 启动后端
# 注意：不要加 --reload，SSE 流在 Windows 上使用 --reload 时 TCP body 会丢失
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（新终端）
cd frontend
npm install
npm run dev                        # http://localhost:3000
```

### 快速测试场景

在对话框中尝试以下输入：

```
# 低风险退款（自动处理，金额 < ¥500）
订单 789012 申请退款，商品破损

# 高风险退款（需切换角色为 MANAGER 后审批）
订单 999999 申请退款

# 订单查询
我的订单 789012 处理到哪一步了？

# 政策问答
七天无理由退款怎么算？什么情况下不能退？
```

切换角色：点击界面右上角角色选择器，选择 **MANAGER** 后可在 ApprovalPanel 中点击批准/拒绝。

---

## 环境变量配置

```bash
# ── 数据库 ──────────────────────────────────────
POSTGRES_USER=ticketuser
POSTGRES_PASSWORD=ticketpass
DATABASE_URL=postgresql+asyncpg://ticketuser:ticketpass@postgres:5432/ticketdb

# ── Redis ────────────────────────────────────────
REDIS_URL=redis://redis:6379
# Serverless 部署时使用（二选一）
UPSTASH_REDIS_URL=rediss://xxx.upstash.io:6380
UPSTASH_REDIS_TOKEN=your-token

# ── LLM ─────────────────────────────────────────
GOOGLE_API_KEY=<your-google-api-key>
GEMINI_MODEL=gemini-2.0-flash      # 默认

# ── 风险规则 ─────────────────────────────────────
RISK_THRESHOLD_AMOUNT=500.0        # 超过此金额触发人工审核

# ── 邮件通知 ─────────────────────────────────────
GMAIL_USER=finance@yourcompany.com
GMAIL_APP_PASSWORD=<your-gmail-app-password>   # Gmail 应用专用密码（非账号密码）

# ── 可观测性 ─────────────────────────────────────
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=<your-langfuse-secret-key>
LANGFUSE_HOST=https://cloud.langfuse.com  # 或自建实例

# ── 前端 ─────────────────────────────────────────
NEXT_PUBLIC_API_URL=http://localhost:8000  # 本地开发
BACKEND_URL=http://backend:8000            # Docker 内网 DNS

# ── 开发调试 ─────────────────────────────────────
SIMULATE_DATABASE_DOWN=false   # true 时模拟 DB 宕机，测试降级路径
```

---

## 项目结构

```
enterprise-ticket-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI 入口，路由注册，CORS 配置
│   │   ├── agent/
│   │   │   ├── graph.py               # LangGraph 图编译，节点注册，条件路由
│   │   │   ├── state.py               # AgentState TypedDict 定义
│   │   │   ├── state_machine.py       # 业务状态机（9 状态，合法流转校验）
│   │   │   ├── utils.py               # Graph 工具函数
│   │   │   └── nodes/
│   │   │       ├── classifier.py      # 意图分类（LLM + 正则降级）
│   │   │       ├── order_lookup.py    # 订单查询节点
│   │   │       ├── risk_check.py      # 风险评分节点
│   │   │       ├── human_review.py    # 人工审批节点（Graph interrupt 续传）
│   │   │       ├── refund.py          # 退款执行节点
│   │   │       ├── notification.py    # 邮件通知节点（幂等）
│   │   │       ├── answer.py          # 通用查询回答节点
│   │   │       └── policy.py          # RAG 政策问答节点
│   │   ├── tools/
│   │   │   ├── order_tools.py         # get_order_detail Tool
│   │   │   ├── refund_tools.py        # check_risk_level + execute_refund Tools
│   │   │   ├── notification_tools.py  # send_notification Tool（SMTP + 幂等）
│   │   │   ├── ticket_tools.py        # get_ticket_status Tool
│   │   │   └── policy_tools.py        # search_policy_raw Tool（TF-IDF RAG）
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── chat.py            # /chat + /resume + /audit + /replay 端点
│   │   │       └── dashboard.py       # 运营统计端点（stats / node-latency / failed-traces）
│   │   ├── db/
│   │   │   ├── models.py              # SQLAlchemy ORM（User/Order/Ticket/RefundLog/AuditLog）
│   │   │   └── database.py            # 异步 DB 引擎 + Session 工厂
│   │   └── core/
│   │       ├── config.py              # Settings（从 .env 读取所有配置）
│   │       ├── permissions.py         # RBAC 权限校验（USER/AGENT/MANAGER）
│   │       ├── observability.py       # Langfuse Callback Handler
│   │       └── logging.py             # structlog 配置
│   ├── alembic/                       # 数据库迁移版本
│   ├── tests/
│   │   ├── test_e2e_refund_flow.py    # 端到端集成测试（真实图 + 真实 DB）（新增）
│   │   ├── test_sse_integration.py    # SSE 协议集成测试
│   │   ├── test_api_routes.py         # API 路由 happy path
│   │   ├── test_answer_node.py        # ReAct 节点单元测试
│   │   ├── test_auth_and_ratelimit.py # JWT + 限流
│   │   ├── test_classifier.py         # 意图分类
│   │   ├── test_nodes.py              # 核心节点
│   │   ├── test_risk_scoring.py       # 风险评分
│   │   └── test_edge_cases.py         # 边界情况
│   ├── evals/                         # 意图分类 Evals（20 golden cases）
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── app/
│   │   ├── page.tsx                   # 主对话界面
│   │   ├── dashboard/
│   │   │   └── page.tsx               # 运营仪表盘 + ChainReplay
│   │   └── api/
│   │       ├── chat/route.ts          # SSE 协议适配器（Python → AI SDK）
│   │       ├── dashboard/
│   │       │   ├── stats/route.ts
│   │       │   ├── failed-traces/route.ts
│   │       │   └── node-latency/route.ts  # 节点耗时代理端点（新增）
│   │       ├── agent/audit/[threadId]/route.ts
│   │       └── agent/replay/[threadId]/route.ts
│   ├── components/
│   │   ├── generative/
│   │   │   ├── AgentThinkingStream.tsx  # 节点执行进度流
│   │   │   ├── OrderCard.tsx            # 订单详情卡
│   │   │   ├── ApprovalPanel.tsx        # 审批面板（含仿真动画）
│   │   │   ├── RiskAlert.tsx            # 风险评分展示
│   │   │   ├── RefundTimeline.tsx       # 退款四阶段进度
│   │   │   └── EmailPreview.tsx         # 邮件内容预览
│   │   ├── AuditLogPanel.tsx            # 实时审计日志侧栏
│   │   ├── ChainReplay.tsx              # 执行链可视化回放
│   │   └── ErrorBoundary.tsx            # 组件错误隔离
│   ├── store/
│   │   └── authStore.ts               # Zustand 角色状态
│   ├── lib/
│   │   └── utils.ts                   # 格式化工具函数
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml                 # 全栈编排（postgres/redis/backend/frontend）
└── .env.example                       # 环境变量模板
```

---

## 可观测性与调试

### Langfuse 追踪

每次对话都会在 Langfuse 生成完整 trace，包含：
- 每个 LangGraph 节点的输入/输出
- Gemini Token 用量和费用
- 节点执行延迟（可识别性能瓶颈）
- 前端 HTTP 请求与后端节点的关联（通过 sessionId = thread_id）

访问 [cloud.langfuse.com](https://cloud.langfuse.com) 查看。

### 审计日志 API

```bash
# 查询某次对话的完整执行链
curl http://localhost:8000/api/agent/audit/{thread_id}

# 获取可视化回放数据（节点耗时、状态快照）
curl http://localhost:8000/api/agent/replay/{thread_id}

# 查看当前 Agent 状态（开发环境）
curl http://localhost:8000/api/agent/debug/{thread_id}
```

### Dashboard 节点耗时图表

访问 `http://localhost:3000/dashboard`，「节点耗时」图表展示各 Agent 节点的 avg / P95 耗时：
- 颜色编码：P95 > 3000ms 标红（严重瓶颈）、> 1000ms 标橙（需关注）、其余蓝色正常
- 数据来源：`/api/dashboard/node-latency`，实时从 AuditLog 聚合近 7 天成功执行记录

### Dashboard 执行链回放

访问 `http://localhost:3000/dashboard`，在 ChainReplay 组件中输入任意 thread_id，可视化查看整条执行链：每个节点的执行时间、成功/失败状态、退款状态机快照。

### 模拟 DB 宕机

```bash
SIMULATE_DATABASE_DOWN=true uvicorn app.main:app --port 8000
# 发送退款请求，验证降级路径是否返回友好错误信息
```

---

## 安全与权限

### Agent Safety Regression Tests

`backend/tests/` 覆盖三类面试中常被追问的 Agent 安全场景：

| 风险 | 覆盖方式 |
|------|----------|
| Prompt injection | 政策问答遇到“忽略规则/泄露 Key/直接批准”等注入文本时，仍只返回检索到的政策引用 |
| 越权审批 | `USER` / `AGENT` 即使构造 `approve` 决策，也会被 `human_review_node` 的 RBAC 拦截 |
| 敏感信息泄露 | 审计日志/SSE 输出前通过 `mask_dict()` 脱敏邮箱、手机号、API Key、Token 等模式 |

### RBAC 角色权限

| 角色 | 可执行操作 |
|------|-----------|
| **USER** | 发起退款申请、查询政策、查看自己的工单 |
| **AGENT** | 查看订单详情、查看统计数据 |
| **MANAGER** | 审批/拒绝高风险退款（金额 > ¥500） |

- `/api/agent/resume` 服务端强校验 `reviewer_role == "MANAGER"`，非 Manager 返回 HTTP 403
- 前端 ApprovalPanel 中，非 Manager 角色按钮不可点击（UI 层保护）

### 数据脱敏

AuditLog 写入前通过 `mask_dict()` 移除 PII：
- 电子邮件地址、手机号等敏感字段自动过滤

### 生产部署安全

- Docker 容器以非 root 用户（`appuser`）运行
- 所有密钥通过环境变量注入，不硬编码
- Gmail 使用应用专用密码（App Password），非账号密码

---

## 数据库迁移

```bash
# 生成新迁移
alembic revision --autogenerate -m "add new field"

# 应用迁移
alembic upgrade head

# 回滚一步
alembic downgrade -1

# 查看迁移历史
alembic history
```

---

## License

MIT
