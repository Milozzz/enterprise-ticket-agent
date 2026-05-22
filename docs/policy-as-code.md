# Policy-as-Code

本项目将 Agent 的关键治理规则抽成 `agent_policy.json`，让权限、审批阈值和高风险动作控制从业务代码中分离出来。代码负责执行策略，策略文件负责表达规则。

## 解决的问题

- 避免权限规则散落在多个节点和工具函数里。
- 让“谁能调用什么工具”可以被单独审查和测试。
- 让“什么退款必须进入人工审批”具备明确、可复盘的规则来源。
- 为后续接入 OPA/Rego、Cedar 或企业内部权限中心留下演进空间。

## 当前策略范围

策略文件位置：

```text
backend/app/policies/agent_policy.json
```

当前包含两类规则：

| 策略类型 | 示例 |
| --- | --- |
| Action Permission | `USER` 不能直接执行 `execute_refund`，只有 `AGENT` / `MANAGER` 可以执行 |
| Refund Review | 金额超过 500、风险分数大于等于 40、高风险等级、欺诈标记用户，都必须进入 HITL |

## 执行路径

工具调用路径：

```text
LangGraph Node -> Tool Gateway -> Policy Engine -> Business Tool
```

退款审批路径：

```text
check_risk_node -> risk scoring -> Policy Engine -> auto approval / HITL
```

## 面试表述

我没有把高风险规则写死在节点里，而是抽成 Policy-as-Code。Tool Gateway 在调用工具前会先查询策略引擎，判断当前执行者是否有权限调用这个 action；风控节点在得到风险分数后，也会再经过策略规则判断是否必须进入人工审批。

这样做的价值是：规则可审查、可测试、可版本化。后续如果企业已有 OPA/Rego、Cedar 或内部权限中心，可以把当前 JSON 策略引擎替换成外部策略服务，而 Agent 工作流本身不需要大改。
