# P2 Agent 差异化能力完成报告

生成日期：2026-07-03

## 结论

`agent-提升目录.md` 中的 5 项 P2 已全部完成。实现统一接入现有 Supervisor、
Policy-as-Code、Tool Gateway、HITL、AuditLog 与 PostgreSQL 持久化边界，没有形成
绕过治理层的第二套演示链路。

## 验收矩阵

| P2 项目 | 状态 | 实现与证据 |
| --- | --- | --- |
| MCP 集成 | 完成 | `/mcp` 提供 Streamable HTTP JSON-RPC，支持初始化、协议协商、会话、`tools/list`、`tools/call`；工具来自受治理的 Tool Gateway，生产环境要求认证 |
| 多 Agent 协作 | 完成 | 风控和政策问答拆成独立 LangGraph Specialist Subgraph；Supervisor 只路由，写操作、风控阈值与审批保持确定性 |
| Prompt / Policy 版本化 | 完成 | SHA-256 稳定分桶、stable/canary 灰度；Prompt 版本/变体/模板哈希与 Policy 版本进入 AuditLog，LLMUsage 保存成本和版本维度 |
| 记忆分层 | 完成 | 短期消息/checkpoint、会话摘要、跨会话长期偏好与纠纷/风险事实三层；长期记忆支持租户隔离、保留期、更新和风控消费 |
| 异步任务化 | 完成 | 持久化 Agent Job API、Idempotency-Key、重试退避、取消、陈旧锁回收、`SKIP LOCKED` 多 Worker 抢占和 HITL waiting 状态 |

## 1. MCP 与 Tool Gateway

关键文件：

- `backend/app/api/routes/mcp_server.py`
- `backend/app/agent/mcp_adapter.py`
- `backend/app/agent/tool_gateway.py`
- `backend/tests/test_protocol_gateways.py`

MCP 是协议边界，不是新的执行旁路。`tools/call` 最终仍进入 Tool Gateway，因此
ERP 写操作继续执行 RBAC、Policy、审批凭证、幂等、超时/重试/熔断和审计。

## 2. 受控 Multi-Agent

关键文件：

- `backend/app/agent/subgraphs/specialists.py`
- `backend/app/agent/graph.py`
- `backend/app/agent/state.py`

退款流程中的风险 Specialist 采用 `check_risk + fetch_user_history` 并行 fan-out，
在 join 节点合并长期风险事实；政策 Specialist 负责 Permission-aware RAG、引用与摘要。
两个 Specialist 都不能直接执行退款或 ERP 写工具。

## 3. Prompt / Policy 灰度与审计

关键文件：

- `backend/app/llm/prompt_registry.py`
- `backend/app/core/policy.py`
- `backend/app/llm/gateway.py`
- `backend/app/api/routes/prompt_governance.py`
- `backend/app/api/routes/dashboard.py`

灰度分桶为 `sha256(node:routing_key) % 100`，同一 thread 始终落入同一版本。
Prompt 管理接口只返回发布元数据和模板哈希，且受 `X-Admin-API-Key` 保护。成本报表
增加 Prompt 版本维度，可对比 stable/canary 的调用量、失败率、延迟、token 与成本。

## 4. 长期记忆

关键文件：

- `backend/app/agent/long_term_memory.py`
- `backend/app/agent/nodes/user_history.py`
- `backend/app/agent/nodes/summarize.py`
- `backend/app/agent/nodes/human_review.py`

系统只把显式通知/语言偏好和审批拒绝形成的纠纷事实写入长期记忆，不保存原始整段
对话。高重要度 dispute/risk 记忆会提升风险等级并强制进入 HITL；所有查询均按
`tenant_id + user_id` 隔离，并过滤过期或停用记录。

## 5. 持久化 Agent Job

关键文件：

- `backend/app/agent/agent_jobs.py`
- `backend/app/api/routes/agent_jobs.py`
- `backend/app/erp/worker_main.py`
- `backend/alembic/versions/07a8b9c0d1e2_add_p2_prompt_memory_and_agent_jobs.py`

API：

```text
POST /api/agent/jobs
GET  /api/agent/jobs/{job_id}
POST /api/agent/jobs/{job_id}/cancel
```

任务按租户和请求人鉴权。生产 PostgreSQL 使用 `FOR UPDATE SKIP LOCKED`，Web 内嵌
Worker 和独立 Compose Worker 可以共同消费；失败任务指数退避，运行锁超过 5 分钟
可被其他实例回收。LangGraph 进入 interrupt 时任务状态为 `waiting_approval`，状态由
PostgreSQL checkpoint 保存，而不是让 Worker 占用线程等待人工审批。

## 验证结果

| 门禁 | 结果 |
| --- | --- |
| 后端全量测试 | `259 passed, 1 skipped` |
| P2/节点/流程定向回归 | PASS |
| Ruff `E/F/W` | PASS |
| 前端 `tsc --noEmit` | PASS |
| Repository hygiene | PASS |
| Alembic fresh upgrade / rollback / re-upgrade | head `07a8b9c0d1e2`，PASS |
| Trajectory eval | `6/6` |
| RAG eval | Recall@4 `1.0`，Citation Faithfulness `1.0` |
| Safety red-team | `24/24` |
| Answer Judge | `2/2` |

唯一跳过项是需要外部 PostgreSQL/SAP 环境的集成验收；真实 SAP Sandbox 和多副本
PostgreSQL 压测仍属于部署环境验收，不应在没有外部账号和基础设施时宣称已验证。

## 面试表达

这个 P2 的重点不是“Agent 数量”，而是职责与权限边界：模型和 Specialist 可以提出
判断、检索证据和生成建议，但资金、权限与 ERP 写操作必须经过版本化 Policy、Tool
Gateway、HITL 和持久化审计。异步队列解决吞吐，checkpoint 解决流程恢复，两者承担
不同职责。
