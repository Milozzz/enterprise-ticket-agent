"""
节点3：风控评估
调用 check_risk_level() 工具，决定是否需要人工审批
"""

import asyncio
from datetime import datetime

from app.agent.precedent import apply_precedents_to_risk, load_refund_precedents
from app.agent.approval_service import persist_approval_evidence
from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tool_gateway import execute_tool, gateway_context_from_state
from app.agent.tools.refund_tools import check_risk_level
from app.core.idempotency import acquire_idempotency_key, stable_idempotency_key
from app.core.logging import get_logger
from app.core.policy import evaluate_refund_review_policy
from app.db.database import AsyncSessionLocal
from app.agent.dependencies import resolve_session_factory
from app.db.models import Ticket, TicketStatus, User, UserRole, UserMemory
from sqlalchemy import select

logger = get_logger(__name__)

# B5：用户画像时效窗口。超过窗口的历史信号降权而非等权，
# 避免"半年前被拒 3 次"与"昨天被拒 3 次"对风控产生同样影响。
FRAUD_FLAG_STALE_DAYS = 180
REFUND_FREQ_WINDOW_DAYS = 180
REJECTED_WINDOW_DAYS = 90


def _days_since(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    try:
        return max(0, (datetime.utcnow() - datetime.fromisoformat(iso_ts)).days)
    except (ValueError, TypeError):
        return None


def _erp_health_signals(order_detail: dict) -> dict:
    """F1（agent×数据结合层）：从订单的 ERP 上下文提取数据面风险信号。

    order_lookup 已经把 reconciliationIssues / outboxEvents / openItems 查回
    state.order_detail.erpContext，此前无人消费——这里把它们变成风控输入：
    - 未决对账差异：该订单财务状态在两个系统间对不上，禁止自动放款
    - outbox FAILED/DEAD_LETTER：上一次相关事件投递已经出问题，说明链路不健康
    - open item 全部已清账：要退的未清项不存在，数据不一致，必须人工看
    """
    erp = (order_detail or {}).get("erpContext") or {}

    open_issues = [
        issue
        for issue in (erp.get("reconciliationIssues") or [])
        if str(issue.get("status") or "").lower() not in {"resolved", "closed"}
    ]
    bad_outbox = [
        event
        for event in (erp.get("outboxEvents") or [])
        if "FAILED" in str(event.get("status") or "").upper()
        or "DEAD" in str(event.get("status") or "").upper()
    ]
    open_items = list(erp.get("openItems") or [])
    uncleared = [
        item
        for item in open_items
        if not item.get("clearing_document_id") and not item.get("clearingDocument")
    ]
    return {
        "open_reconciliation_issues": len(open_issues),
        "reconciliation_mismatch_types": sorted(
            {str(issue.get("mismatch_type") or "unknown") for issue in open_issues}
        ),
        "failed_outbox_events": len(bad_outbox),
        "open_item_missing": bool(open_items) and not uncleared,
    }


async def _upsert_ticket(order_id: str, user_role: str, thread_id: str, reason: str) -> int:
    """
    幂等创建工单记录：同一 thread_id 不重复插入。
    返回 Ticket DB 主键（integer）。
    """
    idem_key = stable_idempotency_key("ticket", order_id, thread_id, reason)
    acquired = await acquire_idempotency_key(idem_key, ttl_seconds=86400)
    if not acquired:
        for _ in range(5):
            async with resolve_session_factory(AsyncSessionLocal)() as session:
                result = await session.execute(select(Ticket).where(Ticket.thread_id == thread_id))
                existing = result.scalar_one_or_none()
                if existing:
                    logger.info("ticket_duplicate_redis_hit", ticket_id=existing.id, idempotency_key=idem_key)
                    return existing.id
            await asyncio.sleep(0.05)
        logger.warning("ticket_duplicate_in_flight", thread_id=thread_id, idempotency_key=idem_key)
        return 0

    async with resolve_session_factory(AsyncSessionLocal)() as session:
        # 如果同一 thread 已有工单，直接复用
        stmt = select(Ticket).where(Ticket.thread_id == thread_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.info("ticket_duplicate_db_hit", ticket_id=existing.id, idempotency_key=idem_key)
            return existing.id

        # 根据前端角色找对应的 DB 用户
        _role_map = {
            "MANAGER": UserRole.MANAGER,
            "AGENT":   UserRole.AGENT,
            "USER":    UserRole.USER,
        }
        db_role = _role_map.get(user_role.upper() if user_role else "AGENT", UserRole.AGENT)
        stmt2 = select(User).where(User.role == db_role)
        r2 = await session.execute(stmt2)
        db_user = r2.scalars().first()

        # 如果找不到对应角色的用户，退而求其次取第一个用户
        if not db_user:
            stmt3 = select(User)
            r3 = await session.execute(stmt3)
            db_user = r3.scalars().first()

        if not db_user:
            logger.warning("no_user_found_for_ticket_creation")
            return 0

        ticket = Ticket(
            order_id=order_id,
            requester_id=db_user.id,
            thread_id=thread_id,
            status=TicketStatus.PENDING,
            reason=reason,
        )
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        logger.info("ticket_created", ticket_id=ticket.id, order_id=order_id, idempotency_key=idem_key)
        return ticket.id


async def _load_user_memory(user_id: str, tenant_id: str) -> dict:
    """
    读取 user_memory 表中的持久化用户画像（按租户隔离）。
    找不到记录时返回空默认值（不阻断主流程）。
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {}

    try:
        async with resolve_session_factory(AsyncSessionLocal)() as session:
            result = await session.execute(
                select(UserMemory).where(
                    UserMemory.user_id == uid,
                    UserMemory.tenant_id == tenant_id,
                )
            )
            mem = result.scalar_one_or_none()
            if mem is None:
                return {}
            return {
                "refund_count": mem.refund_count,
                "rejected_count": mem.rejected_count,
                "fraud_flag": mem.fraud_flag,
                "last_refund_at": mem.last_refund_at.isoformat() if mem.last_refund_at else None,
                # B5：暴露记忆更新时间，供风控做时效加权（近似最后一次拒绝/标记时间）
                "updated_at": mem.updated_at.isoformat() if mem.updated_at else None,
                "notes": mem.notes,
            }
    except Exception as e:
        logger.warning("load_user_memory_failed", error=str(e), user_id=user_id)
        return {}


async def _load_dispute_memories(user_id: str, tenant_id: str) -> list[dict]:
    """G2：读取长期记忆中的争议/欺诈类记录（定性信号，补计数器盲区）。"""
    try:
        from app.agent.long_term_memory import list_active_memories

        return await list_active_memories(
            user_id,
            tenant_id=tenant_id,
            memory_types=["dispute_history", "fraud_signal"],
            limit=5,
        )
    except Exception as e:
        logger.warning("load_dispute_memories_failed", error=str(e), user_id=user_id)
        return []


async def check_risk_node(state: AgentState) -> dict:
    """
    风控评估节点
    根据金额、用户历史、退款原因计算风险分
    决定走自动审批还是人工审批流程
    """
    logger.info(
        "node_start",
        node="check_risk",
        order_id=get_state_val(state, "order_id"),
        amount=get_state_val(state, "order_amount", 0),
    )

    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "checking_risk",
                    "label": "风控评估",
                    "status": "running",
                    "detail": f"正在评估退款风险（金额 ¥{get_state_val(state, 'order_amount', 0)}）...",
                }
            ]
        },
    }

    try:
        risk_args = {
            "order_id": get_state_val(state, "order_id", ""),
            "amount": get_state_val(state, "order_amount", 0),
            "user_id": get_state_val(state, "user_id", "unknown"),
            "reason": get_state_val(state, "refund_reason", "other"),
        }
        # B7/G1/G2：风控工具（同步）、用户画像、判例检索、长期记忆四路互不依赖，
        # 并行执行。to_thread 会复制 contextvars，租户上下文安全。
        user_id = get_state_val(state, "user_id", "unknown")
        tenant_id = str(get_state_val(state, "tenant_id", "default") or "default")
        gateway_result, user_mem, precedents, dispute_memories = await asyncio.gather(
            asyncio.to_thread(
                execute_tool,
                "check_risk_level",
                risk_args,
                context=gateway_context_from_state(
                    state,
                    actor_role="AGENT",
                    specialist_id="risk_specialist",
                ),
                handler=check_risk_level,
            ),
            _load_user_memory(user_id, tenant_id),
            load_refund_precedents(
                tenant_id=tenant_id,
                amount=get_state_val(state, "order_amount", 0),
                requester_id=user_id,
            ),
            _load_dispute_memories(user_id, tenant_id),
        )
        if not gateway_result.success:
            raise RuntimeError(gateway_result.error or "check_risk_level failed")
        risk_data = gateway_result.data

        # 读取跨会话用户记忆，补充风控评分（按租户隔离，B5：按时效加权）
        if user_mem:
            mem_age = _days_since(user_mem.get("updated_at"))
            refund_age = _days_since(user_mem.get("last_refund_at"))
            if user_mem.get("fraud_flag"):
                if mem_age is not None and mem_age > FRAUD_FLAG_STALE_DAYS:
                    # 过期欺诈标记：不再一票拉满，显著加分并注明降权，避免永久一刀切
                    risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 25)
                    risk_data.setdefault("reasons", []).append(
                        f"用户画像：历史欺诈标记（{mem_age} 天未再触发，降权处理）"
                    )
                else:
                    # 近期欺诈标记：强制人工，风险分拉满
                    risk_data["riskScore"] = max(risk_data.get("riskScore", 0), 90)
                    risk_data["riskLevel"] = "high"
                    risk_data.setdefault("reasons", []).append("用户画像：历史欺诈标记")
            elif user_mem.get("refund_count", 0) >= 5:
                if refund_age is not None and refund_age > REFUND_FREQ_WINDOW_DAYS:
                    risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 5)
                    risk_data.setdefault("reasons", []).append(
                        f"用户画像：历史退款 {user_mem['refund_count']} 次"
                        f"（最近一次已隔 {refund_age} 天，降权）"
                    )
                else:
                    # 高频退款用户：+15 分
                    risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 15)
                    risk_data.setdefault("reasons", []).append(
                        f"用户画像：历史退款 {user_mem['refund_count']} 次（高频）"
                    )
            elif user_mem.get("rejected_count", 0) >= 2:
                if mem_age is not None and mem_age > REJECTED_WINDOW_DAYS:
                    risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 5)
                    risk_data.setdefault("reasons", []).append(
                        f"用户画像：历史被拒 {user_mem['rejected_count']} 次"
                        f"（{mem_age} 天前，降权）"
                    )
                else:
                    # 多次被拒：+10 分
                    risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 10)
                    risk_data.setdefault("reasons", []).append(
                        f"用户画像：历史被拒 {user_mem['rejected_count']} 次"
                    )
            logger.info("user_memory_applied", user_id=user_id, mem=user_mem)

        # G2：长期记忆（争议/欺诈类）——补计数器盲区的定性信号。
        # rejected_count 已 ≥2 时计数器信号已生效，不重复加分（避免双计）；
        # 计数器没捕捉到、但记忆里有高重要度争议时才 +10。全部记忆随附给审批人。
        if dispute_memories:
            risk_data["longTermMemories"] = [
                {
                    "key": mem.get("key"),
                    "content": str(mem.get("content") or "")[:120],
                    "importance": mem.get("importance"),
                }
                for mem in dispute_memories[:3]
            ]
            high_importance = [
                mem for mem in dispute_memories if int(mem.get("importance") or 0) >= 80
            ]
            if high_importance and int(user_mem.get("rejected_count") or 0) < 2:
                risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 10)
                risk_data.setdefault("reasons", []).append(
                    f"长期记忆：{len(high_importance)} 条高重要度争议记录"
                    f"（{str(high_importance[0].get('content') or '')[:40]}）"
                )
            logger.info(
                "dispute_memories_applied", user_id=user_id, count=len(dispute_memories)
            )

        # F1（agent×数据结合层）：把 ERP 数据面信号纳入风控。order_lookup 已把
        # 对账差异 / outbox 健康 / open item 状态查回 order_detail.erpContext，
        # 此前无人消费——这些是"系统当前状态下该不该写"的关键判据。
        erp_signals = _erp_health_signals(get_state_val(state, "order_detail", {}) or {})
        risk_data["erpSignals"] = erp_signals
        if erp_signals["open_reconciliation_issues"] > 0:
            # 财务状态在两系统间对不上——绝不自动放款
            risk_data["riskScore"] = max(risk_data.get("riskScore", 0), 80)
            risk_data["autoApprove"] = False
            risk_data.setdefault("reasons", []).append(
                f"ERP 数据面：{erp_signals['open_reconciliation_issues']} 项未决对账差异"
                f"（{', '.join(erp_signals['reconciliation_mismatch_types'])}），禁止自动放款"
            )
        if erp_signals["failed_outbox_events"] > 0:
            # 相关事件投递已失败/进死信，链路不健康，加分并倾向人工
            risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 20)
            risk_data.setdefault("reasons", []).append(
                f"ERP 数据面：{erp_signals['failed_outbox_events']} 个 outbox 事件失败/死信"
            )
        if erp_signals["open_item_missing"]:
            # 要退的未清项不存在（可能已被清账）——数据不一致，必须人工
            risk_data["autoApprove"] = False
            risk_data.setdefault("reasons", []).append(
                "ERP 数据面：未找到未清账的应收项，可能已清账，需人工核对"
            )

        # G1：判例检索——历史人工决策反哺本次评分（详见 precedent.py 的保守规则：
        # 低批准率收紧、同用户被拒强制人工、高批准率仅小幅减分且不翻转强制人工）。
        apply_precedents_to_risk(risk_data, precedents)
        if precedents:
            logger.info(
                "precedents_applied",
                sample=precedents.get("sample_size"),
                approval_rate=precedents.get("approval_rate"),
            )

        # 重新计算 autoApprove（分数 >= 50 需人工）
        risk_score = risk_data.get("riskScore", 0)
        if risk_score >= 50:
            risk_data["autoApprove"] = False

        review_policy = evaluate_refund_review_policy(
            amount=get_state_val(state, "order_amount", 0),
            risk_score=risk_data.get("riskScore", 0),
            risk_level=risk_data.get("riskLevel", "low"),
            user_history=user_mem,
            routing_key=str(get_state_val(state, "thread_id", "global") or "global"),
        )
        risk_data["policyDecision"] = review_policy.to_audit_event()
        if review_policy.requires_human_review:
            risk_data["autoApprove"] = False
            risk_data.setdefault("reasons", []).append(
                f"Policy-as-Code: {', '.join(review_policy.matched_rules)}"
            )

        # B3：本轮若存在 LLM 降级决策（意图分类走了规则 fallback），
        # 降级链路的产出不允许自动放行资金操作，强制人工审批。
        if get_state_val(state, "llm_degraded", False):
            risk_data["autoApprove"] = False
            risk_data.setdefault("reasons", []).append(
                "LLM 降级决策链路：禁止自动审批，强制人工复核"
            )

        requires_human = not risk_data.get("autoApprove", True)

        logger.info(
            "risk_evaluated",
            risk_score=risk_data.get("riskScore"),
            risk_level=risk_data.get("riskLevel"),
            requires_human=requires_human,
        )

        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = (
            f"风险评分：{risk_data.get('riskScore')} 分（{risk_data.get('riskLevel')}），"
            f"{'需要人工审批' if requires_human else '自动审批'}"
        )

        # 生成 RiskAlert UI 组件
        ui_risk_alert = {
            "type": "risk_alert",
            "data": risk_data,
        }

        events = [ui_thinking, ui_risk_alert]

        # 在 DB 中幂等创建工单（同一 thread 不重复）
        order_id   = get_state_val(state, "order_id", "")
        user_role  = get_state_val(state, "user_role", "AGENT")
        thread_id  = get_state_val(state, "thread_id", "")
        reason     = get_state_val(state, "refund_reason", "other")
        try:
            db_ticket_id = await _upsert_ticket(order_id, user_role, thread_id, reason)
            logger.info("upsert_ticket_result", db_ticket_id=db_ticket_id, order_id=order_id)
        except Exception as ticket_err:
            logger.error("upsert_ticket_failed", error=str(ticket_err), order_id=order_id)
            raise RuntimeError("退款工单未能持久化，已阻止后续财务操作") from ticket_err
        if not db_ticket_id:
            raise RuntimeError("退款工单未能形成有效记录，已阻止后续财务操作")

        approval_id = None
        if not requires_human:
            # Auto approval is still explicit evidence: a deterministic policy
            # decision is persisted before any ERP write can cross the gateway.
            approval_id = await persist_approval_evidence(
                tenant_id=tenant_id,
                scenario_id="refund",
                approval_type="refund_policy_auto_approval",
                thread_id=thread_id,
                stage_id="policy_auto_approval",
                action="auto_approve",
                reviewer_id="policy-engine",
                reviewer_role="SYSTEM",
                review_roles=["SYSTEM"],
                comment="Low-risk refund approved by Policy-as-Code.",
                policy_event=review_policy.to_audit_event(),
                session_factory=resolve_session_factory(AsyncSessionLocal),
            )

        # 如果需要人工审批，额外发送 ApprovalPanel 组件
        if requires_human:
            events.append({
                "type": "approval_panel",
                "data": {
                    "ticketId": db_ticket_id or order_id,
                    "threadId": thread_id,
                    "orderAmount": get_state_val(state, "order_amount", 0),
                    "riskScore": risk_data.get("riskScore"),
                    # G1/G2：判例摘要与长期记忆随面板给审批人做参考
                    "precedents": risk_data.get("precedents"),
                    "longTermMemories": risk_data.get("longTermMemories"),
                },
            })

        result = {
            "ticket_id": str(db_ticket_id) if db_ticket_id else order_id,
            "risk_score": risk_data.get("riskScore", 0),
            "risk_level": risk_data.get("riskLevel", "low"),
            "risk_reasons": risk_data.get("reasons", []),
            "risk_precedents": risk_data.get("precedents") or {},
            "requires_human_approval": requires_human,
            "current_step": "check_risk_done",
            "tool_gateway_events": [gateway_result.audit_event],
            "policy_events": [review_policy.to_audit_event()],
            "ui_events": events,
        }
        if approval_id:
            result["approval_id"] = approval_id
        return result

    except Exception as e:
        logger.error("check_risk_error", error=str(e))
        return {
            "error_message": f"风控评估失败: {e}",
            "current_step": "check_risk_error",
            "ui_events": [ui_thinking],
        }
