# Agent 平台深度升级报告

生成日期：2026-07-14

## 结论

本轮已把项目从“配置化业务工作流”深化为一条可验证的受控自治主链：

`TaskSpec -> PlanGraph -> Specialist -> Evidence Graph -> Verifier -> Policy/HITL -> Executor -> Reconciliation -> Memory`

用户要求的八个 Agent 深化方向均已在代码、接口或自动化门禁中形成闭环。项目当前具备生产型架构演示与面试讲解深度，但真实 SAP Sandbox、真实网络容量和真实用户业务指标仍属于外部验收，不应仅凭本地代码宣称完成。

## 1. 统一任务模型

- 新增 TaskSpec 2.0，将自然语言转换为统一业务契约。
- 字段覆盖目标、场景、实体、约束、成功标准、风险、申请人、截止时间、预算和缺失信息。
- TaskSpec 带来源哈希，可被 Supervisor、Planner、Verifier、Eval 和审计共同引用。
- 退款任务明确要求退货、库存、金额、币种、政策、审批、贷项凭证、清账、通知与最终对账结果。

主要实现：`backend/app/agent/task_spec.py`。

## 2. 可执行 PlanGraph 与动态规划

- PlanStep 支持 specialist、tool、依赖、前后置条件、副作用、审批和补偿定义。
- Planner 只能从受信节点、白名单工具和有限 DAG 中生成计划。
- Validator 检查未知工具、越权 specialist、写操作补偿、依赖环、步数和预算。
- Executor 在每个节点执行前授权，执行后更新步骤状态和 execution journal。
- 支持可配置动态 planner、计划验证、受控调度、超时、重试和最多一次有限重规划。
- HITL 中断后保留 TaskSpec、PlanGraph、Evidence、预算和重规划次数，从 checkpoint 原位置恢复。

主要实现：`plan_graph.py`、`dynamic_planner.py`、`dynamic_plan_runtime.py`、`execution_governance.py`、`scenario_graph_runtime.py`。

## 3. 持久化 Evidence Graph

- 所有关键业务事实统一为带来源、对象、值、置信度、采集时间和 trace 的 Evidence。
- 决策必须引用 Evidence ID，不能只引用自然语言推理。
- Evidence、关系和决策记录写入数据库，采用 append-only 与哈希链校验。
- 支持 `evaluated_by`、`governed_by`、`authorized_by`、`verified_by` 等关系。
- 退款写入前要求订单、金额、币种、退货、质检、库存、风险和政策证据完整且不过期。

主要实现：`evidence_graph.py`、`evidence_store.py`、迁移 `2b3c4d5e6f70_add_agent_evidence_store.py`。

## 4. 独立 Verifier 与有限反思

- 确定性 Verifier 校验金额、币种、证据覆盖、证据时效、库存一致性、审批和计划后置条件。
- 可选 LLM Critic 只做补充审查，不能替代确定性规则或扩大执行权限。
- 统一输出 `EVIDENCE_MISSING`、`PLAN_PRECONDITION_FAILED`、`TOOL_RESULT_INCONSISTENT`、`POLICY_CONFLICT`、`SUCCESS_CRITERIA_NOT_MET` 等原因码。
- 可恢复缺失进入有限重规划；越权、证据冲突和审批冲突直接 fail closed；超过预算转人工。
- 工具返回内容在进入 Agent 上下文前做 Schema 校验与隐藏指令扫描。

主要实现：`verifier.py`、`tool_gateway.py`、`dynamic_plan_runtime.py`。

## 5. 按权限边界拆分 Specialist

- Operations Specialist：订单与流程上下文。
- Inventory Specialist：退货授权、收货质检和库存一致性，只读。
- Risk Specialist：风险、异常和历史争议，只读。
- Policy Specialist：权限感知的政策检索与引用，只读。
- Finance Specialist：财务证据和金额校验，不直接执行写入。
- Executor：仅在计划、Policy、HITL 和审批凭证有效时执行确定性写操作。
- Tool Gateway 强制校验 specialist 与工具的允许关系，子 Agent 无法绕过权限边界。

主要实现：`plan_graph.py`、`tool_gateway.py` 和各 specialist node。

## 6. 跨系统退款长事务

退款链路已覆盖：

1. 理解任务并生成计划。
2. 查询订单和客户历史。
3. 校验退货授权、仓库收货与质检。
4. 检查订单行、库存记录和账实一致性。
5. 收集风险、政策和审批证据。
6. 执行 Finance Saga，创建贷项凭证并清账。
7. 财务失败时执行冲销/补偿。
8. 恢复或隔离退货库存。
9. 发送通知并执行最终对账。
10. 只有成功标准全部满足才写入程序性记忆。

主要实现：`nodes/return_inventory.py`、`tools/inventory_tools.py`、`nodes/refund.py`、`nodes/reconciliation.py`、`nodes/final_reconciliation.py`。

## 7. 四类 Agent 记忆

- Semantic Memory：稳定偏好和企业事实。
- Episodic Memory：任务过程、失败步骤和人工修正。
- Procedural Memory：通过验证的成功计划模板。
- Risk Memory：纠纷、欺诈和异常风险事实。
- 记忆包含来源、有效期、置信度衰减、版本和租户/用户边界。
- 当前权威业务事实与旧记忆冲突时，当前事实获胜并保留冲突记录。
- 支持检索相似先例、重新验证后复用，以及用户级删除权。

主要实现：`long_term_memory.py`、`procedural_memory.py`、`precedent.py`、`api/routes/memories.py`。

## 8. Agent Eval、故障注入与 SLO

发布门禁已从分类准确率扩展为任务与执行质量：

- Task Success Rate
- Plan Validity
- Tool Selection Accuracy
- Evidence Coverage
- Replan Success Rate
- Human Override Rate
- Policy Violation Rate
- Compensation Success Rate
- Cost per Successful Task
- Time to First Result
- End-to-End Completion Time

故障注入覆盖 ERP 超时、脏数据、审批超时、金额不一致、执行中途失败、记忆冲突、工具输出 Prompt Injection、证据过期、非法计划、越权写入、预算耗尽、库存不一致和 specialist 越权。

Simulation Lab 提供路由、运行时、场景评估和故障注入面板；Dashboard 展示 Agent SLO、质量、成本和延迟指标。

主要实现：`production_quality_eval.py`、`agent_depth_eval.py`、`agent_resilience_eval.py`、`agent_slo.py`。

## 验证结果

- 后端完整回归：`307 passed, 1 skipped`。
- 生产型核心决策评估：`120/120`。
- 受控自治深度评估：`48/48`。
- Agent 故障注入评估：`14/14`。
- 新数据库 Alembic 升级到 head：通过，当前 head 为 `2b3c4d5e6f70`。
- Ruff correctness（`F`）门禁：通过。
- 前端 TypeScript type-check：通过。
- `git diff --check`：通过。

## 仍需真实环境验收

以下内容依赖外部系统或真实业务流量，不能由代码仓库单独证明：

- SAP Sandbox 的 OAuth、Principal Propagation、真实 OData 字段、CSRF、ETag、Batch 和 Credit Memo Request。
- 真实网络下的 Render 冷启动、P50/P95/P99、并发容量、限流和多副本一致性。
- 真实企业用户的任务完成率、人工接管率、错误成本、节省工时和审批 SLA。
- 生产 KMS、密钥轮换、灾难恢复、告警值班与审计合规验收。

项目已经提供 Connector acceptance、故障注入、审计与 SLO 接口；接入真实环境后应把脱敏失败样本持续回灌到 Eval，而不是把 Mock 通过等同于商业生产验证。
