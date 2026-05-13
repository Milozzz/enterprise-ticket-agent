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
from app.db.models import Ticket, TicketStatus, User, UserRole, UserMemory
from sqlalchemy import select, update
from datetime import datetime

logger = get_logger(__name__)


async def _update_user_memory_rejected(user_id: str) -> None:
    """审批拒绝后，更新用户跨会话记忆（rejected_count）"""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            mem = result.scalar_one_or_none()
            now = datetime.utcnow()
            if mem is None:
                mem = UserMemory(
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
    user_role = get_state_val(state, "user_role") or "USER"

    # 权限校验：审批操作仅限 MANAGER
    action = "approve_refund" if decision == "approve" else "reject_refund"
    try:
        require_permission(user_role, action)
    except PermissionDeniedError as e:
        logger.error("human_review_permission_denied", role=user_role, decision=decision)
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

    # 更新数据库：审批人 + 工单状态
    if ticket_id:
        try:
            ticket_int_id = int(ticket_id)
            new_status = TicketStatus.APPROVED if decision == "approve" else TicketStatus.REJECTED
            op_id = 2  # 默认用 MANAGER 用户（id=2）作为审批人

            async with AsyncSessionLocal() as session:
                stmt = update(Ticket).where(Ticket.id == ticket_int_id).values(
                    status=new_status, operator_id=op_id
                )
                result = await session.execute(stmt)
                await session.commit()
                logger.info("db_ticket_updated", ticket_id=ticket_int_id, status=new_status, operator_id=op_id)
        except Exception as e:
            logger.error("db_update_ticket_error", error=str(e))

    if decision == "approve":
        logger.info("human_approved", reviewer_id=get_state_val(state, "reviewer_id"))
        return {
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
        await _update_user_memory_rejected(user_id)
        return {
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
