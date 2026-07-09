# 代码审查与修复总账 —— enterprise-ticket-agent

> 审查 + 修复的完整记录，按 **Agent 方向 / 数据与平台方向 / 两层结合层** 分类。
> 状态图例：✅ 已完成 ｜ 🟡 做了一半（已完成/剩余部分均标明）｜ ⏳ 未动（需决策或单独排期）
>
> 验证现状：所有改动/新增文件全部通过字节码级编译 + 跨模块符号引用验证 + 核心逻辑
> asyncio 实测（状态机、幂等键、ReAct 循环、补槽、D3 租户作用域、F 系列结合层信号等）。
> **完整集成测试尚未运行**——云端环境无法安装依赖，请在本地 backend 目录执行
> `.\run_tests.ps1`（一键建venv/装依赖/跑pytest），或 push 后由 GitHub Actions CI 运行。

---

# 第一部分：Agent 方向

## 1.1 安全与信任边界

| 项 | 问题 | 状态 |
|---|---|---|
| #1 | 审批接口角色可被客户端伪造 → 越权批准 | ✅ resume/审批接口一律以 JWT 覆盖请求体身份（chat.py / approvals.py） |
| #2 | `/auth/token` 无口令签发任意角色 JWT | ✅ 生产环境不再注册该路由（main.py） |
| #3 | Dashboard 全无鉴权，泄露运营成本数据 | ✅ router 级 `Depends(get_current_user)` |
| #4 | `TESTING=1` 误置生产即全面绕过认证 | ✅ `testing_mode_active()`：生产环境即使设了也不生效（config/auth/rate_limit/admin_config 四处统一） |
| #15 | `mask_dict` 深度>3 返回**未脱敏**原文 | ✅ 超深返回 `_masked` 占位，不再泄漏 |
| A6 | 工具入参无 schema 强校验 | ✅ tool_gateway 按 input_schema 做必填/类型校验，拦截即产审计事件 |
| A2 | 无企业 SSO | 🟡 **已完成**：OIDC RS256/ES256 + JWKS 验证（`core/oidc.py`，发现文档/密钥轮换/alg 混淆防护/未知角色降权），与本地 HS256 并存。**剩余**：前端授权码流程接 IdP；IdP sub→DB users 映射 |

## 1.2 对话与交互

| 项 | 问题 | 状态 |
|---|---|---|
| A1 | 每条消息新建 thread，多轮对话断裂 | ✅ 会话内复用 thread_id，新增"新对话"按钮（page.tsx） |
| H1 🔴 | **长对话消息 O(N²) 重复**：前端每轮重发全量历史，add_messages 对无 ID 消息一律追加——第 20 轮 state 里积累 ~210 条重复消息，token 费用/时延随轮数二次方增长 | ✅ thread 已有 checkpoint 时只传最新一条用户消息（历史在 checkpointer 里），新 thread 才收全量。20 轮对话 state 消息数 210→20 |
| H2 | answer 节点把全部历史塞进每次 LLM 调用，长对话单次调用数千 token | ✅ 上下文窗口裁剪：只带最近 N 条（`CHAT_LLM_HISTORY_WINDOW`，默认 20）进 prompt，截断保证不以孤儿 ToolMessage 开头；完整历史仍在 checkpointer |
| H3 | 六个事件列表（ui_events/tool_gateway_events/policy_events/prompt_events/approval_history/specialist_handoffs）operator.add 无限累积，checkpointer 每超步全量序列化，长对话每个节点都在写越来越大的 blob | ✅ 换成 `capped_add`（保留最近 100 条）；权威记录本就在 audit_logs 表，state 列表只是运行时便利 |
| A3 | 路由=关键词匹配，低置信硬猜 | ✅ **完整形态**：关键词快路径 → LLM 结构化语义路由 → 置信度过低时 **interrupt 真澄清**（graph 暂停提问，用户下一条消息即回答，澄清后双路重路由，仅一轮）。剩余小尾巴：前端 `clarification_request` 事件的专用卡片渲染（现以文本气泡显示，功能可用） |
| A7 | slot 提取靠正则，缺槽用默认值硬走 | ✅ **完整形态**：slot 配置自动编译 JSON Schema 交 LLM 结构化提取（enum 白名单/负金额拒绝，缺失回落正则）+ 必填缺失时 **interrupt 补槽**（追问一轮，合并重提取）。剩余小尾巴：slot 置信度输出 |
| #13 | `next.config.mjs`/`ts` 双配置冲突 | 🟡 保留 ts 版的方案已定；**需你手动删除设备上的 next.config.mjs**（桥接工具无删除权限） |

## 1.3 可靠性（agent 越自动化越会放大的债）

| 项 | 问题 | 状态 |
|---|---|---|
| B1 | checkpointer 静默降级 MemorySaver，重启丢全部待审批 | ✅ 生产非持久 checkpointer 直接拒绝启动；启动时输出待审批任务数指标 |
| B2 | resume 无幂等，重复点击/重试造成副作用翻倍（二次 reject 可翻转已完成工单） | ✅ decision 已消费的重复请求只回显结果，绝不落 direct-DB fallback；checkpoint 丢失才允许逃生通道 |
| B3 | LLM 全挂时各节点降级行为不一且不可审计 | ✅ classifier/answer/policy/supervisor 降级统一标注 `llm_degraded`；**降级链路强制人工审批**，不许自动放行资金操作 |
| #9 | 状态机 happy path 记录非法转移 | ✅ 自动审批路径补合法 →APPROVED 转移 |
| B7 | 风控节点内工具与记忆加载串行 | ✅ `asyncio.gather` 并行（to_thread 保 contextvars） |
| #10 | order_tools 模块级事件循环线程桥接同步 @tool | ⏳ 需整体 async 化改走 execute_tool_async，小重构，建议下一个 PR |

## 1.4 智能化（workflow → agent 跃迁）

| 项 | 内容 | 状态 |
|---|---|---|
| A4 | 工具自主规划层 | ✅ **完整形态**：ReAct 步间重规划（观察-决策-执行循环，失败可换参补救、可主动 finish）+ plan_execute 保守模式。白名单/schema/步数三重校验逐步执行 + 连续失败上限；每步过 tool_gateway 全量治理，LLM 只能提议不能绕闸。**有意不做**：refund 主流程不接规划层（固定序列=财务合规要求） |
| B5 | 记忆层只是计数器、fraud_flag 只进不出 | ✅ 画像按时效加权（180 天未触发的欺诈标记降权、旧拒绝/旧高频降分），不再永久一刀切。剩余方向（C 系列）：long_term_memory 检索进风控上下文 |
| B6 | prompt 无版本追溯 | ✅ 复核确认为**误报**——prompt_registry 已有 stable/canary 分流 + 版本/哈希审计，三节点均接入 |
| G1 | 决策深度：风控每次"从零判断"，审批表里的历史人工判决无人利用 | ✅ **判例检索式风控**（`precedent.py`）：检索同租户/同类型/金额±30%/近90天的已决审批，批准率≤20%→+20分强制人工（附典型拒绝理由）；同用户有类似被拒→强制人工；批准率≥95%且样本≥10→仅-10分且**绝不翻转已有强制人工**；样本<5只展示不评分。判例摘要三路透出：approval_panel、interrupt payload、收件箱 business_payload——审批人打开任务即见"17件类似案件批准率12%，常见拒绝理由：xx"。零LLM零新表，每次新审批自动扩充判例库（唯一自我增强的飞轮） |
| G2 | `long_term_memory` 建了表、写了数据，风控只用三个计数器 | ✅ 争议/欺诈类长期记忆接入风控：高重要度（≥80）争议记录且计数器未捕捉（rejected_count<2）时 +10 分（双计防护）；记忆内容随附审批上下文。与 G1、用户画像、风控工具四路并行加载 |

## 1.5 评测与成本治理

| 项 | 内容 | 状态 |
|---|---|---|
| A5 | 评测闭环 | 🟡 **已完成**：`GET /api/dashboard/agent-slo`（路由分布/澄清率/HITL 时长/节点错误率/降级数/LLM 健康，对齐 ERP SLO）+ `scripts/export_failed_traces.py`（失败链路去重导出为回归用例）。**剩余**：SLO 目标值与告警；失败用例自动重放执行器；A3/A7 上线后用真实数据校准阈值 |
| B4 | RAG 无质量评测 | ✅ 引用标注复核为已有（policy_citations 完整）；补 `scripts/eval_rag_retrieval.py`（golden set → hit@1/hit@3/MRR，可接 CI） |
| B8 | LLM 成本只记录不控制 | ✅ 每租户日 token 预算硬闸门（超额拒绝 → 自然落入降级路径），默认关闭 |

**Agent 方向配置开关一览**（均默认关闭/安全值）：`OIDC_ENABLED`、`SUPERVISOR_LLM_ROUTING_ENABLED`、`SUPERVISOR_CLARIFICATION_ENABLED`、`AGENT_PLANNER_ENABLED`（`AGENT_PLANNER_MODE` 默认 react、连续失败上限默认 2）、`SLOT_LLM_EXTRACTION_ENABLED`、`SLOT_CLARIFICATION_ENABLED`、`LLM_TENANT_DAILY_TOKEN_BUDGET`。

**对话式 interrupt 公共机制**（A3/A7 共用）：interrupt 载荷经 SSE 完整透出（问题文本 + `clarification_request` UI 事件）；chat 入口检测 thread 停在澄清/补槽 interrupt 上时，把新消息转为 `Command(resume=...)` 恢复而非重开流程；审批类 interrupt 仍走 /resume，两类隔离。

---

# 第二部分：数据与平台方向

## 2.1 多租户与数据正确性

| 项 | 问题 | 状态 |
|---|---|---|
| D1 | `user_memory`（风控画像）无 tenant_id、user_id 全局唯一，跨租户串数据 | ✅ 加 tenant_id + `(tenant_id,user_id)` 复合唯一 + RLS 策略；三处读写节点全部按租户过滤；迁移 `1a2b3c4d5e6f`（**待你执行 `alembic upgrade head`**） |
| D2/#7 | `get_db` 把 RLS 上下文写死默认租户，且吞异常 `yield None` | ✅ 绑定 `current_tenant_id()`；连接失败 fail-fast 抛 5xx |
| D3 | SSE 流式落库租户 contextvar 在中间件退出后被 reset → 流内写入落默认租户 | ✅ **本轮完成**：问题经 asyncio 实测复现确认为真；修复=整条 SSE 流（chat + resume）重新包进 `tenant_scope`，流内全部 DB 写入（audit/ticket/user_memory/approval）绑定请求租户，流毕复位；附回归测试 `tests/test_tenant_stream_scope.py`（同时验证问题存在性与修复行为） |
| #6 | 审批操作人硬编码 `operator_id=2`，审计失真 | ✅ `resolve_operator_id` 按 name/email 解析真实审批人，解析不到留空而非记假 |
| D4 | `users` 表无 tenant_id、email 全局唯一 | ⏳ 牵动全局唯一约束+所有外键+seed，独立迁移工程，单独排期 |
| D5 | `init_db` create_all 不建 RLS，隔离测试假信心 | ✅ postgres 下发 RuntimeWarning 明示，文档写明用迁移 |
| D6 | 小项：全表 naive `datetime.utcnow`；个别全局 unique 与租户复合唯一风格不一 | ⏳ 低风险技术债，建议与 D4 一起做 |

## 2.2 一致性与幂等

| 项 | 问题 | 状态 |
|---|---|---|
| #12 | 幂等键含 amount 但 float/str/Decimal 形态不同键不同 → 重复退款风险 | ✅ `_canonical_amount` 统一 Decimal 量化到分（100 / "100.00" / 100.0 / Decimal 同键，实测验证） |
| #9 | （状态机，见 Agent 1.3——同一修复） | ✅ |
| #8 | Saga 每步独立提交，跨步无原子性，崩溃依赖补偿 | ⏳ 重构风险高：建议先做崩溃点注入重放测试验证补偿收敛性，再决定是否改 |

## 2.3 基础设施与性能

| 项 | 问题 | 状态 |
|---|---|---|
| #5 | 限流/缓存在事件循环里同步阻塞 I/O 且每请求新建连接 | ✅ 统一 `redis.asyncio` 池化单例（`db/redis_client.py`），chat_cache/rate_limit 共用，Redis 不可用自动放行 |
| #11 | `get_settings()` 每次重建（读盘解析 .env） | ✅ 生产 `lru_cache`，开发保持热加载 |
| #14 | CORS 生产写死 localhost | ✅ 生产只放行配置的 frontend_origin |
| E2 | ERP 连接器每次新建 httpx client 重握手 | ✅ 按 (base_url,tls,timeout) 池化长连接 + lifespan 优雅关闭 |

## 2.4 ERP 数据中心（SAP 风格）

| 项 | 问题 | 状态 |
|---|---|---|
| E1 | 熔断把 4xx 业务错误计入失败，客户端错误刷开熔断拖垮正常流量 | ✅ 仅 5xx/超时/连接错误触发熔断，4xx 抛业务错误不计失败 |
| E4 | PII record_id 不含 purpose 但 AAD 含 → 换 purpose 覆盖旧记录静默丢数据 | ✅ record_id 纳入 purpose，读取按 purpose 过滤 |
| E3 | 熔断/token 缓存进程内隔离，与 tool_gateway 两套语义不一致 | ⏳ 需 Redis 共享状态统一设计，单独排期 |
| E5 | 低风险自动退款 `force_write` 绕过 shadow_writes 直写真实 SAP | ⏳ **需产品决策**：自动审批的退款要不要真写 ERP？（前置认证已修复，策略本身待拍板） |

## 2.5 验证工具（本轮新增）

- `backend/run_tests.ps1`：一键本地测试（建 venv → 装依赖 → TESTING=1 + sqlite 跑 pytest）——云端沙箱无法访问 PyPI，完整集成测试请用它或 CI。
- `backend/tests/test_tenant_stream_scope.py`：D3 回归测试。
- `backend/scripts/eval_rag_retrieval.py`、`backend/scripts/export_failed_traces.py`：见 Agent 1.5。

---

# 第二部分半：Agent × 数据结合层（F 系列）

> 独立审查"agent 和数据层是否结合"后的结论与修复。此前的判断：**执行面结合很深
> （退款写路径五层贯穿治理边界），但认知面没结合——agent 会"操作"ERP，却不"理解"
> ERP 的状态**。本轮把六道缝焊上了四道（外加两道排期），让结合从"一条线"变成"一张网"。

## 结合前已有的深度（保留，作为基线）

- 退款写路径五层贯穿：`refund.py` → `gateway_context_from_state`（打包身份/租户/审批语义）→ `refund_saga` → **saga 每步回穿 tool_gateway**（不绕治理）→ `execute_connector_envelope`（分布式幂等+熔断+审计）→ SAP。
- 三条语义链完整：审批语义（`human_decision=approve` → `allow_live_write` → `force_write`）、租户（state→context→saga→runtime→幂等/审计全程带 tenant，D3 修完后 SSE 期间也正确）、幂等键族（`deterministic_refund_id`→`refund_request_id`→`saga_id`→step key→outbox event_id 可端到端溯源）。
- 读路径半结合：`order_lookup` 从真实 ERP open items / connectors 取 saga 执行参数。

## 本轮焊上的缝

| 缝 | 问题 | 状态 |
|---|---|---|
| F1（缝1，最痛） | ERP 数据搬到了 state 却无人消费——`order_tools` 查回的 reconciliationIssues/outboxEvents/openItems 没进任何决策 | ✅ `risk_check._erp_health_signals`：未决对账差异→强制人工+分数≥80；outbox 失败/死信→+20 分；要退的未清项已不存在→强制人工。ERP 的"数据质量信号"正式成为风控"决策输入" |
| F2（缝2） | 连接器审计用 `erp:{幂等键}` 独立编号，无法与 agent trace join | ✅ `AgentTraceRef` 把 agent 侧 thread_id/trace_id 透传进 `execute_connector_envelope` → 连接器审计；一个 trace_id 可从"用户说了什么"查到"对 SAP 发了什么"。后台直调（无 agent trace）优雅回退旧编号 |
| F3（缝3） | Saga 进 MANUAL_REVIEW 只落状态字段，运营靠翻 dashboard 偶然发现 | ✅ `_escalate_saga_to_hitl`：三处 MANUAL_REVIEW 出口调 `ensure_approval_task`，进 agent 侧现成的 HITL 收件箱+SLA 升级+审批中心。task_key 绑 saga_id 幂等，登记失败不阻断 saga 收尾 |
| F4（缝5） | 能规划的没 ERP（planner 只有 2 个模拟工具），有 ERP 的不能规划（erp_get_order/query_doctype 没进白名单） | ✅ ERP 只读工具挂进规划器：`_execute_planned_tool` 按类型分派（ERP 走 async 连接器治理路径），`effective_tools` 并入 ERP 只读集合。规划器可串"查订单→查未清项→查凭证"做多步只读诊断。默认关（`PLANNER_ERP_READONLY_ENABLED`） |
| F5（新增） | agent 与 ERP 各一个 dashboard，看不到完整链路健康 | ✅ `GET /api/dashboard/chain-health`：一个响应给 agent SLO + ERP 业务指标 + **linkage 缝合指标**（多少 ERP 调用能 join 回 agent trace，即 F2 的可观测证据） |

## 仍未焊（排期项）

| 缝 | 问题 | 性质 |
|---|---|---|
| 缝4 治理组件对 agent 是装饰品 | schema_registry / pii_vault 唯一消费方是 erp_governance API——agent 工具出入参不过契约校验，对话 PII 不进 vault（只靠 mask_dict 兜底） | 真工程：需在 tool_gateway 出入参接契约校验、在对话入口接 pii_vault，单独排期 |
| 缝6 双熔断双幂等（=E3） | runtime 与 tool_gateway 各一套熔断，进程内隔离、语义不一 | 架构：需 Redis 共享状态统一，单独排期 |

**结合层新增配置开关**（默认关闭/安全值）：`PLANNER_ERP_READONLY_ENABLED`、`ERP_MANUAL_REVIEW_SLA_MINUTES`（默认 120）。

## 判决更新

上一轮的判决是"执行面结合、认知面没结合"。F1–F5 之后：**认知面焊上了主干**——agent 现在能"读懂"ERP 的对账/outbox/清账状态并据此决策（F1），能被一个 trace 端到端追踪（F2），ERP 的人工介入回流到统一收件箱（F3），规划器能自主查 ERP 做诊断（F4），完整链路健康在一个视图可见（F5）。剩余缝 4/6 是治理组件复用与基础设施统一，属"锦上添花+架构统一"，不影响"结合"这个命题的成立。

---

# 第三部分：剩余工作与路线

## 未动项汇总（6 项）

| 项 | 方向 | 性质 | 建议 |
|---|---|---|---|
| #10 双事件循环桥接 | Agent | 小重构 | 下一个 PR：@tool 链路 async 化走 execute_tool_async |
| #8 Saga 跨步原子性 | 数据 | 高风险重构 | 先做崩溃点注入重放测试，验证补偿收敛再动 |
| D4 users 租户化（含 D6 小项） | 数据 | 独立迁移工程 | 单独排期 |
| E3/缝6 熔断跨进程统一 | 数据/结合层 | 架构设计 | 引 Redis 共享状态，与 tool_gateway 熔断合并语义 |
| E5 force_write 策略 | 数据 | 产品决策 | 拍板"自动退款是否真写 SAP" |
| 缝4 治理组件接 agent | 结合层 | 真工程 | tool_gateway 出入参接 schema_registry 契约校验；对话入口接 pii_vault |

## 需要你手动操作的事项

1. `alembic upgrade head`（应用 user_memory 租户化迁移）
2. 删除 `frontend/next.config.mjs` 与根目录 `_claude_write_test.txt`
3. **本地跑一次完整测试**：backend 目录执行 `.\run_tests.ps1`（或 push 触发 CI）——三轮共 40+ 处改动只做过编译级+逻辑级验证，集成测试是当前最大的未覆盖风险
4. 按需开启新能力开关（见 Agent 方向开关一览）

## 后续方向（C 系列，未开工）

**Agent**：~~长期记忆检索进风控~~（已完成，见 G2）、按任务复杂度路由模型（利用现有 llm_node_routes_json，成本直降）、SLO 告警+失败重放执行器（A5 收口）、注入攻击红队评测集、前端澄清卡片与 SSO 授权码流程、事件驱动反向通道（对账差异/outbox 死信主动唤醒 agent 开工单——连接层从"拉"到"推"的最后一块）。
**数据**：审计/用量表的保留期与分区归档（表只增不减）、alembic 迁移往返回归测试、dashboard/SLO 聚合下推（物化视图）、PII vault 密钥轮换批处理、embedding 版本化与知识库重建流程。

---

# 附：定位对比（Agent 层 vs ERP 层）

| 维度 | Agent 层 | ERP 数据中心 |
|---|---|---|
| 问题性质 | 杂而开放：LLM 决策/HITL/多场景编排 | 难而确定：SAP 协议/幂等/Saga/对账 |
| 成熟度 | 三轮修复后：治理完整、决策可审计、具备语义路由/自主规划/评测闭环 | 核心路径扎实，接近质量上限 |
| 技术稀缺性 | 编排框架同质化高，但"治理边界内的自主性"是差异点 | S/4HANA 级连接器+Saga+对账稀缺，可独立成资产 |

结论不变：两者互补，护城河在结合点——"让 AI 在治理边界内安全操作真实企业财务系统"。多轮修复后 Agent 层可信度已配得上 ERP 层的扎实度；F 系列结合层修复后，两层从"并排的优秀系统"变成"一张相互感知的网"——护城河从命题落成了实现。

## 值得肯定的地方（首轮审查原评，保留）

- Tool Gateway 抽象干净：策略鉴权、dry-run、幂等键、熔断、审计统一在一个边界。
- Saga 持久化步骤/补偿/事务性 outbox（SKIP LOCKED leasing 正确），DLQ/重放考虑周到。
- 字段级信封加密 + 可替换 KMS provider 设计规范。
- 多租户 RLS 经 contextvar + after_begin 事件贯穿，近 90 张表批量 FORCE RLS。
- A2A 回调 SSRF 防护（禁凭据、host 白名单、私网校验）是加分项。
