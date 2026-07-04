# P0 完成报告

生成日期：2026-07-02
范围：`agent-提升目录.md` 中 P0-1 至 P0-4

## 结论

P0 功能与工程门禁已经完成，确定性 P0 Eval 状态为 **PASS**：

| 指标 | 结果 |
|---|---:|
| Golden trajectory | 6/6 |
| RAG Recall@4 | 100% |
| 引用忠实度 | 100% |
| Safety red-team | 24/24 |
| Answer Judge | 2/2 |
| 政策语料 | 60 条 |
| 全量后端测试 | 248 passed / 1 skipped |
| Ruff | passed |
| Frontend type-check | passed |
| Repository hygiene gate | passed |

唯一不能由代码仓库代替完成的是历史凭据轮换：Git 历史中发现 3 个旧提交曾包含
`.env`。当前版本已忽略且不跟踪 `.env`，但 Google、Langfuse、PostgreSQL、Redis
等曾出现过的凭据必须在各平台轮换，再重写远端历史。报告末尾给出处理步骤。

## P0-1 可配置多场景平台

### 已完成

- 退款、权限申请、报销三个真实场景由 Supervisor 统一路由。
- 权限申请和报销共用 Generic Scenario Runtime 和通用 HITL Subgraph。
- 声明式配置覆盖 intents、slots、tools、policies、审批链、回复与 UI 模板。
- 新增场景不修改 `graph.py`；只注册 Tool/Policy adapter 并增加场景 JSON。
- README 已加入“三步新增采购审批场景”的 diff 示例。
- Golden trajectory 同时断言 Supervisor 路由、工具序列、审批分支和最终状态。

### 关键实现

- `backend/app/agent/generic_runtime.py`
- `backend/app/agent/graph.py`
- `backend/app/agent/scenario_schema.py`
- `backend/app/scenarios/reimbursement.json`
- `backend/evals/trajectory_dataset.json`
- `backend/tests/test_p0_evaluation.py`

## P0-2 生产级 RAG

### 已完成

- 新增 `knowledge_documents` 与 `knowledge_chunks` 持久化模型。
- PostgreSQL 主路径使用 pgvector 768 维向量和 HNSW cosine 索引。
- 使用 Gemini `text-embedding-004` 进行文档建库和查询 embedding。
- 文档先按政策段落切分，再执行固定长度与 overlap 控制。
- Top-K 检索支持可配置候选放大和词法 rerank。
- PostgreSQL、向量索引或模型不可用时自动降级到 TF-IDF bigram。
- 语料由 10 条退款政策扩展到 60 条企业政策。
- 引用契约包含 `document_id`、`paragraph_id`、`source` 和
  `retrieval_method`。
- 前端新增 PolicyCards，用户可直接看到来源与段落。
- 管理接口支持查看索引状态、检索测试和强制重建索引。

### API

```text
GET  /api/admin/knowledge/policies
POST /api/admin/knowledge/policies/search
POST /api/admin/knowledge/policies/reindex
```

### 关键实现

- `backend/app/agent/rag_service.py`
- `backend/app/agent/policy_corpus.py`
- `backend/app/agent/tools/policy_tools.py`
- `backend/app/db/models.py`
- `backend/alembic/versions/e5f6a7b8c9d0_add_pgvector_policy_knowledge_base.py`
- `frontend/components/generative/PolicyCards.tsx`

## P0-3 轨迹、RAG、安全与 Judge Eval

### 已完成

- 6 条 Golden trajectory 覆盖权限和报销的自动审批与 HITL 分支。
- 12 条 RAG ground-truth case 计算 Recall@4 与 citation faithfulness。
- 24 条安全红队 case：8 条 prompt injection、8 条越权、8 条 PII 泄露。
- LLM-as-Judge 支持 Gemini；CI 默认使用确定性 Judge，避免网络与费用波动。
- Judge 同时验证正常回答和故意构造的政策幻觉回答。
- 新增 `/admin/evals` 独立看板和 P0 报告 API。
- CI 把 P0 Eval 作为发布门禁，任何指标低于阈值都会返回非零退出码。

### 执行命令

```bash
cd backend
python -m evals.run_p0_evals
python -m evals.run_p0_evals --llm-judge
```

### 关键实现

- `backend/app/agent/p0_evaluation.py`
- `backend/evals/run_p0_evals.py`
- `backend/evals/rag_dataset.json`
- `backend/evals/safety_dataset.json`
- `frontend/app/admin/evals/page.tsx`
- `backend/evals/eval_report_sample.json`

## P0-4 仓库卫生

### 已完成

- 当前 Git 索引不包含 `.env`、`*.db`、`*.sqlite`、PDF、简历或 `.worktrees`。
- 工作区中的本地数据库和旧 `results_*.json` 已清除。
- 只保留一份 `backend/evals/eval_report_sample.json` 作为公开样例。
- `.gitignore` 覆盖密钥、本地数据库、缓存和历史 Eval 输出。
- 新增 `repo_hygiene_check.py` 并接入 CI，防止这些文件再次入库。
- 本机 `.worktrees/ux-fix` 是 Git 管理的活动 worktree，不在远端索引中，因此未破坏性删除。

### 历史凭据处置

审计发现 `.env` 曾存在于以下旧提交：

```text
33890267 basic function finished at day 9
f7f8c542 langfuse
d30143e5 introduce Upstash Redis Checkpointer
```

必须按以下顺序人工执行：

1. 在 Google AI Studio、Langfuse、PostgreSQL/Render、Upstash/Redis 中轮换旧凭据。
2. 暂停团队推送并备份远端仓库。
3. 在全新 mirror clone 中使用 `git filter-repo --path .env --invert-paths`。
4. 检查所有 branch/tag 后使用 `git push --force --mirror` 更新远端。
5. 所有开发者重新 clone，Render/Vercel 更新新凭据并重新部署。

不应在当前包含多个活动 worktree 和未提交改动的目录中直接重写历史。

## 验证记录

```text
pytest tests/ -q -> 248 passed, 1 skipped
python -m evals.run_p0_evals -> PASS
ruff check app/ -> All checks passed
npm run type-check -> passed
python scripts/repo_hygiene_check.py -> passed
git diff --check -> passed
```

PostgreSQL CI 已改用 `pgvector/pgvector:pg16`，会验证 Alembic fresh upgrade、
rollback/re-upgrade 与 schema drift。真实 Gemini 建索引需要在部署环境配置有效
`GOOGLE_API_KEY` 后调用一次 reindex API。
