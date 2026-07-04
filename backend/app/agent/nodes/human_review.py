"""
节点4：人工审批（Human-in-the-Loop）

这是整个项目最关键的技术点：
1. LangGraph 在进入此节点前 INTERRUPT（暂停）
2. 状态序列化到 Checkpointer（PostgreSQL/Memory）
3. 前端渲染 ApprovalPanel 组件，等待用户操作
4. 用户点击"批准/拒绝" → 前端 POST /api/agent/resume
5. 后端调用 graph.invoke() 携带 human_decision，从 Checkpoint 恢复执行
"""

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.logging import get_logger
from app.core.permissions import require_permission, PermissionDeniedError
from app.db.database import AsyncSessionLocal
from app.db.models import Ticket, TicketStatus, UserMemory
from sqlalchemy import select, update
from datetime import datetime
from collections.abc import Mapping
from langgraph.types import interrupt
from app.agent.approval_tasks import complete_approval_task, ensure_approval_task
from app.agent.dependencies import resolve_session_factory
from app.services.approval_resume import resolve_operator_id
from app.agent.scenario_registry import get_default_registry
from app.agent.long_term_memory import upsert_long_term_memory

logger = get_logger(__name__)


async def _update_user_memory_rejected(user_id: str, tenant_id: str = "default") -> None:
    """审批拒绝后，更新用户跨会话记忆（rejected_count，按租户隔离）"""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return
    try:
        async with resolve_session_factory(AsyncSessionLocal)() as session:
            result = await session.execute(
                select(UserMemory).where(
                    UserMemory.user_id == uid,
                    UserMemory.tenant_id == tenant_id,
                )
            )
            mem = result.scalar_one_or_none()
            now = datetime.utcnow()
            if mem is None:
                mem = UserMemory(
                    tenant_id=tenant_id,
                    user_id=uid,
                    refund_count=0,
                    rejected_count=1,
                    fraud_flag=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(mem)
            else:
                mem.rejected_count = (mem.rejected_count or 0) + 1
                # 自动触发欺诈标记：被拒 >= 3 次
                if mem.rejected_count >= 3:
                    mem.fraud_flag = True
                mem.updated_at = now
            await session.commit()
    except Exception as e:
        logger.warning("update_user_memory_rejected_failed", error=str(e), user_id=user_id)


async def human_review_node(state: AgentState) -> dict:
    """
    人工审批节点

    注意：此节点在 graph.py 中被配置为 interrupt_before=["human_review"]
    因此实际执行时，LangGraph 会在进入此节点前暂停。
    只有当 /resume 接口被调用后，携带 human_decision，此节点才会执行。
    """
    decision = get_state_val(state, "human_decision")
    reviewer_id = get_state_val(state, "reviewer_id")
    ticket_id = get_state_val(state, "ticket_id")
    reviewer_role = get_state_val(state, "user_role") or "USER"
    thread_id = str(get_state_val(state, "thread_id", "") or "")
    task_key = f"refund:{thread_id}:human_review"

    if not decision:
        scenario = get_default_registry().get("refund")
        await ensure_approval_task(
            task_key=task_key,
            request_id=str(ticket_id or get_state_val(state, "order_id", "")),
            scenario_id="refund",
            approval_type="refund",
            stage_id="human_review",
            stage_name="退款人工审批",
            requester_id=str(get_state_val(state, "user_id", "unknown")),
            requester_role=str(get_state_val(state, "user_role", "USER")),
            assigned_roles=["MANAGER"],
            thread_id=thread_id,
            business_payload={
                "ticketId": ticket_id,
                "orderId": get_state_val(state, "order_id"),
                "amount": get_state_val(state, "order_amount"),
                "currency": get_state_val(state, "currency", "CNY"),
                "riskScore": get_state_val(state, "risk_score", 0),
                "riskReasons": get_state_val(state, "risk_reasons", []),
            },
            sla_minutes=scenario.sla_minutes,
            priority="high" if float(get_state_val(state, "risk_score", 0) or 0) >= 80 else "normal",
            tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
            session_factory=resolve_session_factory(AsyncSessionLocal),
        )
        resume_value = interrupt(
            {
                "kind": "approval",
                "scenario_id": "refund",
                "approval_type": "refund",
                "ticket_id": ticket_id,
                "thread_id": get_state_val(state, "thread_id"),
                "order_id": get_state_val(state, "order_id"),
                "amount": get_state_val(state, "order_amount"),
                "currency": get_state_val(state, "currency", "CNY"),
                "risk_score": get_state_val(state, "risk_score", 0),
                "risk_reasons": get_state_val(state, "risk_reasons", []),
                "allowed_roles": ["MANAGER"],
            }
        )
        if not isinstance(resume_value, Mapping):
            resume_value = {"action": str(resume_value)}
        decision = str(resume_value.get("action") or "").lower()
        reviewer_id = str(resume_value.get("reviewer_id") or "unknown")
        reviewer_role = str(resume_value.get("reviewer_role") or "USER").upper()
        review_comment = str(resume_value.get("comment") or "")
    else:
        review_comment = str(get_state_val(state, "review_comment", "") or "")

    # 权限校验：审批操作仅限 MANAGER
    action = "approve_refund" if decision == "approve" else "reject_refund"
    try:
        require_permission(reviewer_role, action)
    except PermissionDeniedError as e:
        logger.error("human_review_permission_denied", role=reviewer_role, decision=decision)
        return {
            "error_message": str(e),
            "current_step": "permission_denied",
            "is_completed": True,
            "ui_events": [{
                "type": "thinking_stream",
                "data": {"steps": [{"step": "permission", "label": "权限校验",
                                    "status": "error", "detail": str(e)}]},
            }],
        }

    logger.info(
        "node_start",
        node="human_review",
        human_decision=decision,
        reviewer_id=reviewer_id,
        ticket_id=ticket_id,
    )
    try:
        await complete_approval_task(
            task_key,
            action=decision,
            reviewer_id=str(reviewer_id or "unknown"),
            comment=review_comment,
            tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
            session_factory=resolve_session_factory(AsyncSessionLocal),
        )
    except Exception as exc:
        # Inbox bookkeeping must not block a decision already authorized by the
        # workflow. The approval audit and ticket transition remain canonical.
        logger.warning("approval_task_completion_failed", task_key=task_key, error=str(exc))

    # 更新数据库：审批人 + 工单状态
    if ticket_id:
        try:
            ticket_int_id = int(ticket_id)
            new_status = TicketStatus.APPROVED if decision == "approve" else TicketStatus.REJECTED

            async with resolve_session_factory(AsyncSessionLocal)() as session:
                # 解析真实审批人（按 name / email），解析不到则留空而非记成假的固定用户，
                # 保证工单 operator_id 可用于事后审计追溯“谁批的”。
                op_id = await resolve_operator_id(str(reviewer_id) if reviewer_id else None, session)
                stmt = update(Ticket).where(Ticket.id == ticket_int_id).values(
                    status=new_status, operator_id=op_id
                )
                await session.execute(stmt)
                await session.commit()
                logger.info("db_ticket_updated", ticket_id=ticket_int_id, status=new_status, operator_id=op_id)
        except Exception as e:
            logger.error("db_update_ticket_error", error=str(e))

    if decision == "approve":
        logger.info("human_approved", reviewer_id=get_state_val(state, "reviewer_id"))
        return {
            "human_decision": decision,
            "reviewer_id": reviewer_id,
            "review_comment": review_comment,
            "current_step": "human_review_approved",
            "ui_events": [
                {
                    "type": "thinking_stream",
                    "data": {
                        "steps": [
                            {
                                "step": "awaiting_human",
                                "label": "人工审批",
                                "status": "done",
                                "detail": f"审批人 {get_state_val(state, 'reviewer_id')} 已批准，继续执行退款",
                            }
                        ]
                    },
                }
            ],
        }
    elif decision == "reject":
        logger.info("human_rejected", reviewer_id=get_state_val(state, "reviewer_id"))
        # 更新用户跨会话记忆（拒绝计数，自动触发欺诈标记）
        user_id = get_state_val(state, "user_id", "unknown")
        await _update_user_memory_rejected(
            user_id,
            str(get_state_val(state, "tenant_id", "default") or "default"),
        )
        try:
            await upsert_long_term_memory(
                user_id=str(user_id),
                memory_type="dispute_history",
                memory_key=f"refund_rejected:{get_state_val(state, 'order_id', 'unknown')}",
                content="退款申请经人工审批被拒绝",
                attributes={
                    "order_id": get_state_val(state, "order_id"),
                    "reviewer_id": reviewer_id,
                    "reason": get_state_val(state, "refund_reason", "other"),
                },
                confidence=1.0,
                importance=85,
                source_thread_id=thread_id,
                source_type="approval_decision",
                tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
                session_factory=resolve_session_factory(AsyncSessionLocal),
            )
        except Exception as exc:
            logger.warning("long_term_memory_write_failed", error=str(exc), user_id=user_id)
        return {
            "human_decision": decision,
            "reviewer_id": reviewer_id,
            "review_comment": review_comment,
            "current_step": "human_review_rejected",
            "is_completed": True,
            "ui_events": [
                {
                    "type": "thinking_stream",
                    "data": {
                        "steps": [
                            {
                                "step": "awaiting_human",
                                "label": "人工审批",
                                "status": "done",
                                "detail": f"审批人 {get_state_val(state, 'reviewer_id')} 已拒绝退款申请",
                            }
                        ]
                    },
                }
            ],
        }
    else:
        # 不应该出现此情况（graph 被 interrupt 在此节点前）
        logger.warning("human_review_no_decision")
        return {"current_step": "awaiting_human_decision"}


def should_continue_after_review(state: dict) -> str:
    """
    人工审批后的路由：
    - 批准 → execute_refund
    - 拒绝 → end
    """
    if get_state_val(state, "human_decision") == "approve":
        return "execute_refund"
    return "end"
