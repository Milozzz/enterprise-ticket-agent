# Configurable Supervisor-based Multi-scenario Agent Platform

本文件记录当前平台化能力的完成范围，方便面试时讲清楚“从单一退款 Agent 到可配置企业 Agent 平台”的演进。

## P0 核心闭环

已完成：

- `Scenario config v2 schema`：提供 `RUNTIME_V2_SCHEMA`，并通过 Admin API 返回给前端。
- `Generic scenario runtime`：权限申请、报销场景通过同一个 `run_configured_scenario` 执行。
- `slot extraction 配置化`：支持 `keyword_map`、`keyword_enum`、`amount`、`message_excerpt`。
- `tool input/output mapping 配置化`：支持 `$slots.xxx`、`$context.xxx` 入参映射，以及 `state_outputs` 输出映射。
- `policy binding 配置化`：通过 `runtime.policy.name` 和 `runtime.policy.args` 绑定 Policy-as-Code。
- `UI template 配置化`：支持 `thinking_stream`、`business_request_card`、`approval_panel` 和 `reply_template`。

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
2. 命中场景后，Generic Runtime 读取 runtime v2 配置。
3. Runtime 按配置抽槽、调用 Tool Gateway、绑定 Policy-as-Code、生成 UI 卡片。
4. 高风险动作进入多级 HITL 审批链。
5. 配置修改通过 validator、Simulation Lab、scenario eval、version snapshot 再发布。
6. 工具层可以继续演进为 MCP-compatible adapter，副作用链路可以用 Saga 做补偿。

这套讲法的重点是：业务场景扩展从“新增硬编码 LangGraph node”升级为“配置 + 受控工具 + 策略 + 校验 + 模拟 + 发布治理”。

## Production storage

By default, bundled scenarios are loaded from `backend/app/scenarios`. For
deployed environments, set `SCENARIO_CONFIG_DIR` to a mounted persistent
directory. The platform creates the directory and seeds the bundled scenarios
on first boot, then saves Scenario Studio edits and version snapshots there
instead of relying on an ephemeral container filesystem.
