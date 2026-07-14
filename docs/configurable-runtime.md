# 配置驱动运行时

平台已把退款、权限申请、报销三个活跃场景统一到 runtime v3：场景配置声明 LangGraph 拓扑，权限与报销继续复用 Generic Scenario Runtime 的抽槽、Policy、Tool Gateway 和 UI handler。

## 目标

新增企业场景时，尽量只配置以下内容：

- 需要抽取哪些业务字段
- 调用哪个 Tool Gateway 工具
- 工具入参如何从 state/context 映射
- 绑定哪个 Policy-as-Code 策略
- 返回哪些业务 UI 卡片
- 进入 HITL 时使用哪些审批角色和动作
- 最终回复模板如何生成

这样平台的扩展方式会从“复制一个 node 再改代码”变成“注册工具 + 配置 runtime + 加 eval cases”。

## Runtime 配置结构

场景配置位于 `backend/app/scenarios/*.json`，核心字段是 `runtime`：

```json
{
  "runtime": {
    "schema_version": "2",
    "approval_type": "permission_request",
    "slot_extraction": {},
    "tool": {},
    "policy": {},
    "approval_messages": {},
    "reply_template": "",
    "state_outputs": {},
    "ui": {}
  }
}
```

活跃场景使用 runtime v3 声明 LangGraph 拓扑，并可携带 v2 的通用抽槽、工具、策略与 UI 合同：

```json
{
  "runtime": {
    "schema_version": "3",
    "engine": "langgraph",
    "entry_node": "classify_intent",
    "nodes": [{"id": "classify_intent", "handler": "classify_intent", "plan_step": "understand"}],
    "edges": [],
    "conditional_edges": []
  }
}
```

`handler` 和 `router` 必须来自后端受信目录，配置无法加载任意代码。`plan_step`/`plan_steps` 把节点绑定到可执行计划。发布前 validator 会检查重复节点、未知 handler/router、非法入口、悬空边、隐式终点和通用 runtime 合同。

## 执行链路

通用运行时执行顺序：

1. 从用户消息抽取 `slots`
2. 根据 `tool.args` 映射 Tool Gateway 入参
3. 执行受控工具调用
4. 根据 `policy.args` 映射策略入参
5. 执行 Policy-as-Code
6. 根据策略结果设置 `pending_review` 或 `auto_approved`
7. 根据 `ui` 配置生成业务卡片和审批面板
8. 根据 `reply_template` 生成最终回复
9. 将 `state_outputs` 写回 LangGraph state

在 v3 图中，上述操作由受信 handler 拆分执行。每个绑定计划步骤的节点先经过依赖、预算和 Evidence 授权；有副作用的步骤还必须具备 Policy/HITL 证据。完成后执行后置验证，通过后才把 PlanGraph 标记为 completed。

## 当前支持的 slot extractor

- `keyword_map`：根据关键词映射到业务值，例如 `github -> GitHub`
- `keyword_enum`：根据关键词识别枚举，例如 `admin -> admin`
- `amount`：从文本中抽取金额
- `message_excerpt`：截取原始输入作为 reason/description

## 当前收益

权限申请和报销的业务差异已经迁移到配置，代码只保留受信的通用 handler：

```python
async def permission_request_node(state):
    return await run_configured_scenario(state, "permission_request")
```

这说明场景的主要差异已经迁移到配置中：

- 字段抽取不同
- 工具不同
- Policy 不同
- UI 字段不同
- 回复模板不同
- 审批角色不同

## 配置校验门禁

平台现在增加了 `Scenario Runtime Validator`，用于在保存场景配置前发现不可运行的配置：

- `workflow` 是否已注册到 Supervisor 工作流入口
- `tools` 是否存在于 Tool Gateway
- `policies` 是否存在于 Policy-as-Code
- HITL 开启时是否配置审批角色
- config-driven workflow 是否包含 `runtime`
- `runtime.tool.name` 是否有通用运行时 handler
- `runtime.policy.name` 是否有通用策略 evaluator
- `tool.args`、`policy.args`、`reply_template`、`state_outputs`、`ui` 中的 `$slots.xxx` 是否指向已定义字段
- `amount`、`fallback_regex` 等正则是否可编译

Admin API 会在 `GET /api/admin/scenarios` 返回每个场景的 `validation` 报告和全局 `validation_summary`；创建或更新场景时，如果存在 error，会返回 `422` 并带上具体 issue。

## Runtime Simulation

平台也提供了 dry-run 级别的运行时模拟：

```http
POST /api/admin/scenarios/{scenario_id}/simulate-runtime
```

输入用户消息后，后端会执行完整配置链路：

1. 根据 `slot_extraction` 抽取字段
2. 根据 `policy.args` 执行策略判定
3. 通过 Tool Gateway 以 `dry_run=true` 方式模拟工具调用
4. 生成 `business_request_card`、`approval_panel` 等 UI events
5. 渲染 `reply_template`

这个接口不会产生真实业务副作用，适合后续接到 Scenario Studio，用于预览一个新场景是否真的能跑通。

## 已补齐的生产能力

- Runtime Simulation 已接入 Scenario Studio。
- Draft/Publish/Version/Rollback 已形成配置治理链路。
- runtime v3 支持多节点、普通边、条件边和 Specialist 子图。
- 退款、权限申请、报销全部使用 runtime v3，v2 仅作为兼容 schema。
- 节点级 `plan_step` 绑定让 PlanGraph 直接治理运行时，而不是只用于展示。
- 通用后置 Verifier 与退款财务 reconciliation 阻止“工具返回成功但业务未真正落库”。
- required slot 缺失时通过 interrupt 追问，仍缺失则 fail closed。
- 结构化 LLM slot extractor 可按环境开启，规则抽取作为低延迟降级路径。
- Planner 增加步数、deadline、连续失败、副作用白名单和重复调用保护；写操作默认不交给自由规划。
