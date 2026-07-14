# Configurable Supervisor-based Multi-scenario Agent Platform

本文件记录当前平台化能力的完成范围，方便面试时讲清楚“从单一退款 Agent 到可配置企业 Agent 平台”的演进。

## P0 核心闭环

已完成：

- `Scenario config v2/v3 schema`：v2 保留兼容；v3 同时表达抽槽/工具/策略/UI、受信节点、边、条件路由和 PlanGraph 步骤绑定。
- `Generic scenario runtime`：权限申请、报销复用同一组配置驱动 handler，并由各自 v3 拓扑编排。
- `slot extraction 配置化`：支持 `keyword_map`、`keyword_enum`、`amount`、`message_excerpt`。
- `tool input/output mapping 配置化`：支持 `$slots.xxx`、`$context.xxx` 入参映射，以及 `state_outputs` 输出映射。
- `policy binding 配置化`：通过 `runtime.policy.name` 和 `runtime.policy.args` 绑定 Policy-as-Code。
- `UI template 配置化`：支持 `thinking_stream`、`business_request_card`、`approval_panel` 和 `reply_template`。
- `Declarative LangGraph`：退款、权限申请、报销三个活跃场景全部迁移到 runtime v3，主图只编译场景配置，不再按场景 ID 特判。
- `Executable PlanGraph`：v3 节点通过 `plan_step`/`plan_steps` 绑定计划步骤，运行前校验依赖、证据、Policy/HITL 与预算，运行后更新状态和执行日志。
- `Post-execution verification`：通用场景核验单据、策略、审批和持久化结果；退款额外执行财务 Saga、贷项凭证、清账凭证和 Outbox 对账。
- `必填字段追问`：required slot 缺失会通过 LangGraph interrupt 追问，补充后从 checkpoint 恢复；仍缺失则拒绝创建业务单据。

## P1 企业级治理

已完成：

- `config validator`：校验 workflow、tool、policy、slot 引用、正则、HITL、多级审批链等。
- `draft/publish/version/rollback`：场景配置保存、发布、回滚会生成 file-backed version snapshot。
- `Simulation Lab 完整链路`：后端 dry-run 接口 + 前端 `/admin/simulation` 页面。
- `场景级 eval`：权限申请、报销已有 scenario-level eval cases，可作为发布门禁基础。
- `approval center`：后端 `/api/agent/approval-center` + 前端 `/admin/approvals` 页面。

## P2 高级能力

已完成可运行骨架：

- `多级审批`：场景 `hitl.approval_chain` 支持多阶段审批，审批接口支持 `stageId`。
- `Saga/补偿事务`：提供 `execute_saga` 通用执行器和退款 Saga 模板。
- `MCP-compatible tool adapter`：Tool Gateway 可导出 MCP-compatible tool descriptors。
- `embedding + LLM router fallback`：Supervisor 在关键词未命中时使用本地 embedding-style similarity fallback，并提供 LLM router prompt contract。
- `config marketplace / scenario template`：提供模板目录和 template -> draft scenario 实例化 API。

## 面试讲法

这个系统现在不只是一个退款 Agent，而是一个“可配置的企业 Agent 运行平台”：

1. Supervisor 先做场景路由。
2. 命中场景后创建 TaskSpec，先检索已验证先例，再生成并校验 PlanGraph。
3. 平台编译 v3 LangGraph，按配置抽槽、执行条件分支、Specialist 子图和 PlanGraph 步骤授权。
4. Runtime 通过 Tool Gateway 调用工具、绑定 Policy-as-Code；高风险动作进入多级 HITL 审批链。
5. 写操作完成后必须通过后置验证与对账，验证成功才允许完成计划并写入程序性记忆。
6. 配置修改通过 validator、Simulation Lab、scenario eval、version snapshot 再发布；MCP/A2A 入口也不能绕过相同治理边界。

这套讲法的重点是：业务场景扩展从“修改主图并硬编码场景分支”升级为“受信节点目录 + 声明式拓扑 + 受控工具 + 策略 + 校验 + 模拟 + 发布治理”。配置不能导入任意 Python，从而保留企业安全边界。

## Production storage

By default, bundled scenarios are loaded from `backend/app/scenarios`. For
deployed environments, set `SCENARIO_CONFIG_DIR` to a mounted persistent
directory. The platform creates the directory and seeds the bundled scenarios
on first boot, then saves Scenario Studio edits and version snapshots there
instead of relying on an ephemeral container filesystem.
