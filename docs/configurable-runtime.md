# 配置驱动运行时

本阶段把权限申请、报销两个场景从“每个场景一个完整 Python node”升级为“同一个 Generic Scenario Runtime 读取场景配置执行”。

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

## 当前支持的 slot extractor

- `keyword_map`：根据关键词映射到业务值，例如 `github -> GitHub`
- `keyword_enum`：根据关键词识别枚举，例如 `admin -> admin`
- `amount`：从文本中抽取金额
- `message_excerpt`：截取原始输入作为 reason/description

## 当前收益

权限申请和报销的 node 已经变成薄入口：

```python
async def permission_request_node(state):
    return await run_configured_scenario(state, "permission_request")
```

这说明两个场景的主要差异已经迁移到配置中：

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

## 仍需完善

下一步可以继续做：

- Runtime Simulation 前端化：在 Scenario Studio 中预览完整执行链路
- Draft/Publish/Version：配置发布和回滚治理
- 多节点 workflow 配置：不止支持单工具调用
- 缺失字段追问：slot 不完整时不直接执行
- LLM extractor fallback：规则抽取失败时使用结构化 LLM 抽取
