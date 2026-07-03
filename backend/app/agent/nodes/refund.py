"""Execute the governed ERP refund Saga from the LangGraph workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import uuid

from sqlalchemy import select

from app.agent.state import AgentState
from app.agent.tool_gateway import gateway_context_from_state
from app.agent.utils import get_state_val
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.agent.dependencies import resolve_session_factory
from app.db.models import UserMemory
from app.db.ticket_repository import (
    complete_ticket,
    deterministic_refund_id,
    record_refund_once,
)
from app.erp.refund_saga import RefundFinanceCommand, execute_refund_finance_saga

logger = get_logger(__name__)


async def _update_user_memory_refund(user_id: str) -> None:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return

    try:
        async with resolve_session_factory(AsyncSessionLocal)() as session:
            memory = await session.scalar(select(UserMemory).where(UserMemory.user_id == uid))
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if memory is None:
                memory = UserMemory(
                    user_id=uid,
                    refund_count=1,
                    rejected_count=0,
                    fraud_flag=False,
                    last_refund_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(memory)
            else:
                memory.refund_count = (memory.refund_count or 0) + 1
                memory.last_refund_at = now
                memory.updated_at = now
            await session.commit()
    except Exception as exc:
        logger.warning("update_user_memory_refund_failed", error=str(exc), user_id=user_id)


def _money(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Refund amount must be a valid decimal value") from exc


def _timeline(
    *,
    ticket_id: str,
    saga_result: dict,
    amount: Decimal,
    currency: str,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    success = bool(saga_result.get("success"))
    return {
        "type": "refund_timeline",
        "data": {
            "steps": [
                {
                    "label": "提交退款申请",
                    "description": f"工单 #{ticket_id[:12]}",
                    "status": "completed",
                    "timestamp": now,
                },
                {
                    "label": "财务预检与策略校验",
                    "description": f"{currency} {amount:.2f}",
                    "status": "completed" if success else "failed",
                    "timestamp": now,
                },
                {
                    "label": "创建贷项凭证",
                    "description": str(saga_result.get("credit_memo_id") or "未创建"),
                    "status": "completed" if saga_result.get("credit_memo_id") else "failed",
                    "timestamp": now,
                },
                {
                    "label": "客户未清项清账",
                    "description": str(saga_result.get("clearing_document_id") or "等待人工处理"),
                    "status": "completed" if saga_result.get("clearing_document_id") else "pending",
                    "timestamp": now,
                },
            ],
            "sagaId": saga_result.get("saga_id"),
            "sagaStatus": saga_result.get("status"),
            "replayed": bool(saga_result.get("replayed")),
        },
    }


async def execute_refund_node(state: AgentState) -> dict:
    """Run credit memo, clearing, compensation, outbox, and evidence as one Saga."""

    ticket_id = str(get_state_val(state, "ticket_id") or uuid.uuid4())
    order_id = str(get_state_val(state, "order_id", "") or "")
    amount = _money(get_state_val(state, "order_amount", "0"))
    currency = str(get_state_val(state, "currency", "CNY") or "CNY").upper()
    connector_id = str(
        get_state_val(state, "connector_id", "CONN-MOCK-ERP") or "CONN-MOCK-ERP"
    )
    open_item_id = str(
        get_state_val(state, "open_item_id", f"OI-AR-{order_id}") or f"OI-AR-{order_id}"
    )
    refund_request_id = str(
        get_state_val(state, "refund_request_id")
        or deterministic_refund_id(order_id, ticket_id, float(amount))
    )

    logger.info(
        "refund_finance_saga_start",
        order_id=order_id,
        refund_request_id=refund_request_id,
        connector_id=connector_id,
    )

    thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "executing_refund_saga",
                    "label": "执行 ERP 财务退款",
                    "status": "running",
                    "detail": "正在执行财务预检、贷项凭证和未清项清账",
                }
            ]
        },
    }

    try:
        saga_result = await execute_refund_finance_saga(
            RefundFinanceCommand(
                order_id=order_id,
                refund_request_id=refund_request_id,
                open_item_id=open_item_id,
                amount=amount,
                currency=currency,
                connector_id=connector_id,
                reason_code=str(get_state_val(state, "refund_reason", "CUSTOMER_RETURN")),
            ),
            context=gateway_context_from_state(
                state,
                actor_role="AGENT",
                scenario="refund_finance",
            ),
        )
    except Exception as exc:
        logger.exception("refund_finance_saga_error", error=str(exc), order_id=order_id)
        thinking["data"]["steps"][0].update(status="error", detail="ERP 财务退款执行失败")
        return {
            "ticket_id": ticket_id,
            "refund_request_id": refund_request_id,
            "refund_success": False,
            "refund_message": str(exc),
            "error_message": f"ERP 财务退款执行失败: {exc}",
            "current_step": "execute_refund_error",
            "ui_events": [thinking],
        }

    success = bool(saga_result.get("success"))
    saga_status = str(saga_result.get("status") or "FAILED")
    refund_id = str(saga_result.get("credit_memo_id") or refund_request_id)
    thinking["data"]["steps"][0].update(
        status="done" if success else "error",
        detail=(
            f"Saga {saga_result.get('saga_id')} 已完成"
            if success
            else f"Saga 进入 {saga_status}，未执行不安全的后续写入"
        ),
    )

    if success:
        try:
            db_ticket_id = await complete_ticket(ticket_id=ticket_id, order_id=order_id)
            if isinstance(db_ticket_id, int):
                await record_refund_once(
                    ticket_id=db_ticket_id,
                    refund_id=refund_id,
                    amount=float(amount),
                )
        except Exception as exc:
            logger.warning("legacy_ticket_projection_failed", error=str(exc), ticket_id=ticket_id)
        await _update_user_memory_refund(str(get_state_val(state, "user_id", "unknown")))

    result = {
        "ticket_id": ticket_id,
        "refund_request_id": refund_request_id,
        "refund_id": refund_id,
        "refund_success": success,
        "refund_message": (
            f"退款财务处理完成，贷项凭证 {saga_result.get('credit_memo_id')}，"
            f"清账凭证 {saga_result.get('clearing_document_id')}"
            if success
            else f"退款未完成，Saga 状态为 {saga_status}"
        ),
        "saga_id": str(saga_result.get("saga_id") or ""),
        "saga_status": saga_status,
        "saga_steps": list(saga_result.get("steps") or []),
        "credit_memo_id": str(saga_result.get("credit_memo_id") or ""),
        "clearing_document_id": str(saga_result.get("clearing_document_id") or ""),
        "compensation": dict(saga_result.get("compensation") or {}),
        "current_step": "execute_refund_done" if success else "execute_refund_manual_review",
        "ui_events": [
            thinking,
            _timeline(
                ticket_id=ticket_id,
                saga_result=saga_result,
                amount=amount,
                currency=currency,
            ),
        ],
    }
    if not success:
        result["error_message"] = result["refund_message"]
        result["is_completed"] = True
    return result
