from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langgraph.types import Command

from app.agent.state_machine import (
    InvalidStateTransitionError,
    RefundState,
    transition,
)
from app.agent.utils import get_state_val
from app.core.config import effective_simulate_database_down
from app.core.logging import get_logger
from app.services.chat_summary import build_summary
from app.services.sse import encode_sse, ui_event_payload

logger = get_logger(__name__)

AuditWriter = Callable[..., Awaitable[None]]
CacheWriter = Callable[[str, int, str], Awaitable[None]]
DirectApprover = Callable[..., Awaitable[dict]]

NODE_NAMES = {
    "supervisor_router",
    "classify_intent", "lookup_order", "check_risk", "risk_fanout",
    "fetch_user_history", "risk_decision", "human_review", "execute_refund",
    "send_notification", "permission_request", "reimbursement",
    "generic_human_review", "finalize_business_request", "answer_node",
    "answer_policy_node", "summarize_session",
}

NODE_STATE_MAP = {
    "classify_intent": RefundState.CLASSIFIED,
    "lookup_order": RefundState.ORDER_LOADED,
    "check_risk": RefundState.RISK_EVALUATED,
    "human_review": RefundState.PENDING_APPROVAL,
    "execute_refund": RefundState.REFUNDED,
    "send_notification": RefundState.COMPLETED,
}

RESUME_NODES = {
    "human_review",
    "execute_refund",
    "send_notification",
    "generic_human_review",
    "finalize_business_request",
}


async def cached_sse_stream(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk
    yield encode_sse("done", {})


async def stream_agent(
    *,
    graph: Any,
    initial_state: dict,
    config: dict,
    thread_id: str,
    trace_id: str,
    cache_key: str,
    cache_ttl: int,
    busy_message: str,
    audit_writer: AuditWriter,
    cache_writer: CacheWriter,
    database_down_check: Callable[[], bool] = effective_simulate_database_down,
) -> AsyncIterator[str]:
    collected: list[str] = []
    try:
        if database_down_check():
            yield encode_sse("text", {"content": busy_message})
            yield encode_sse("done", {})
            return

        yield encode_sse("meta", {"trace_id": trace_id, "thread_id": thread_id})
        yield encode_sse("text", {"content": "已收到请求，Supervisor 正在选择业务场景并准备执行...\n\n"})

        refund_state = RefundState.CREATED
        node_start_times: dict[str, float] = {}
        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            event_type = event.get("event", "")
            event_name = event.get("name", "")
            if event_type == "on_chain_start" and event_name in NODE_NAMES:
                node_start_times[event_name] = time.monotonic()

            if event_type == "on_chain_end" and event_name in NODE_NAMES:
                input_data = event.get("data", {}).get("input")
                output = event.get("data", {}).get("output")
                duration_ms = None
                if event_name in node_start_times:
                    duration_ms = int((time.monotonic() - node_start_times.pop(event_name)) * 1000)

                if event_name in NODE_STATE_MAP:
                    target_state = NODE_STATE_MAP[event_name]
                    try:
                        # 退款执行前必须先经过 APPROVED（含自动审批）。若当前尚未 APPROVED，
                        # 先补一次合法的 →APPROVED 转移，避免 RISK_EVALUATED/PENDING_APPROVAL
                        # 直接跳 REFUNDED 触发非法转移、审计里 _refund_state 卡住。
                        if target_state is RefundState.REFUNDED and refund_state in (
                            RefundState.RISK_EVALUATED,
                            RefundState.PENDING_APPROVAL,
                        ):
                            refund_state = transition(refund_state, RefundState.APPROVED)
                        refund_state = transition(refund_state, target_state)
                    except InvalidStateTransitionError as exc:
                        logger.warning("state_machine_invalid_transition", error=str(exc))
                    if isinstance(output, dict):
                        output = {**output, "_refund_state": refund_state.value}

                await audit_writer(
                    thread_id,
                    event_name,
                    event_type,
                    input_data,
                    output,
                    trace_id=trace_id,
                    duration_ms=duration_ms,
                )

                if isinstance(output, dict):
                    for ui_event in output.get("ui_events", []):
                        chunk = encode_sse("ui", ui_event_payload(ui_event))
                        collected.append(chunk)
                        yield chunk
                    if output.get("reply_text"):
                        chunk = encode_sse("text", {"content": output["reply_text"]})
                        collected.append(chunk)
                        yield chunk
                    elif output.get("error_message"):
                        chunk = encode_sse("text", {"content": f"⚠️ {output['error_message']}"})
                        collected.append(chunk)
                        yield chunk
            elif event_type == "on_chat_model_stream":
                model_chunk = event.get("data", {}).get("chunk")
                if model_chunk and getattr(model_chunk, "content", None):
                    chunk = encode_sse("text", {"content": model_chunk.content})
                    collected.append(chunk)
                    yield chunk

        final = graph.get_state(config)
        if final and final.values:
            if final.next:
                chunk = encode_sse(
                    "interrupt",
                    {"thread_id": thread_id, "trace_id": trace_id, "next": list(final.next)},
                )
                collected.append(chunk)
                yield chunk
            summary = build_summary(final.values)
            if summary:
                chunk = encode_sse("text", {"content": summary})
                collected.append(chunk)
                yield chunk

        yield encode_sse("done", {})
        if collected and final and final.values and get_state_val(final.values, "intent", "") == "query_policy":
            await cache_writer(cache_key, cache_ttl, json.dumps(collected))
    except Exception as exc:
        logger.error("agent_stream_error", error=str(exc), thread_id=thread_id)
        yield encode_sse("text", {"content": busy_message})
        yield encode_sse("done", {})


async def stream_resume(
    *,
    graph: Any,
    request: Any,
    config: dict,
    current_state: Any,
    graph_has_checkpoint: bool,
    graph_can_resume: bool,
    busy_message: str,
    audit_writer: AuditWriter,
    direct_approver: DirectApprover,
    timeline_builder: Callable[[str], dict],
    database_down_check: Callable[[], bool] = effective_simulate_database_down,
) -> AsyncIterator[str]:
    try:
        if database_down_check():
            yield encode_sse("text", {"content": busy_message})
            yield encode_sse("done", {})
            return

        action_emoji = "✅" if request.action == "approve" else "❌"
        action_label = "批准" if request.action == "approve" else "拒绝"
        yield encode_sse("text", {"content": f"{action_emoji} {action_label}操作已确认，正在处理..."})

        if graph_can_resume:
            try:
                async for event in graph.astream_events(
                    Command(
                        resume={
                            "action": request.action,
                            "reviewer_id": request.reviewer_id,
                            "reviewer_role": request.reviewer_role,
                            "comment": request.comment,
                        }
                    ),
                    config=config,
                    version="v2",
                ):
                    event_type = event.get("event", "")
                    event_name = event.get("name", "")
                    if event_type != "on_chain_end" or event_name not in RESUME_NODES:
                        continue
                    input_data = event.get("data", {}).get("input")
                    output = event.get("data", {}).get("output")
                    # 带上 trace_id，让审批阶段的审计记录能与主链路 trace 关联。
                    await audit_writer(
                        request.thread_id,
                        event_name,
                        event_type,
                        input_data,
                        output,
                        trace_id=getattr(request, "trace_id", None) or request.thread_id,
                    )
                    if isinstance(output, dict):
                        for ui_event in output.get("ui_events", []):
                            yield encode_sse("ui", ui_event_payload(ui_event))
                        if output.get("reply_text"):
                            yield encode_sse("text", {"content": output["reply_text"]})
                        elif output.get("error_message"):
                            yield encode_sse("text", {"content": f"⚠️ {output['error_message']}"})

                final = graph.get_state(config)
                if final and final.values:
                    summary = build_summary(final.values)
                    if summary:
                        yield encode_sse("text", {"content": summary})
            except Exception as exc:
                logger.warning("graph_resume_stream_failed_using_fallback", error=str(exc))
                async for chunk in _fallback_resume(
                    request=request,
                    current_state=current_state,
                    graph_has_checkpoint=graph_has_checkpoint,
                    busy_message=busy_message,
                    audit_writer=audit_writer,
                    direct_approver=direct_approver,
                    timeline_builder=timeline_builder,
                    graph_failed=True,
                ):
                    yield chunk
        else:
            async for chunk in _fallback_resume(
                request=request,
                current_state=current_state,
                graph_has_checkpoint=graph_has_checkpoint,
                busy_message=busy_message,
                audit_writer=audit_writer,
                direct_approver=direct_approver,
                timeline_builder=timeline_builder,
                graph_failed=False,
            ):
                yield chunk

        yield encode_sse("done", {})
    except Exception as exc:
        logger.error("resume_stream_error", error=str(exc))
        yield encode_sse("text", {"content": busy_message})
        yield encode_sse("done", {})


async def _fallback_resume(
    *,
    request: Any,
    current_state: Any,
    graph_has_checkpoint: bool,
    busy_message: str,
    audit_writer: AuditWriter,
    direct_approver: DirectApprover,
    timeline_builder: Callable[[str], dict],
    graph_failed: bool,
) -> AsyncIterator[str]:
    ticket_id = get_state_val(current_state.values, "ticket_id") if graph_has_checkpoint else None
    result = await direct_approver(
        ticket_id,
        request.action,
        request.thread_id,
        reviewer_id=request.reviewer_id,
    )
    await audit_writer(
        request.thread_id,
        "direct_db_approve",
        "fallback_db_write",
        {"ticket_id": ticket_id, "action": request.action, "reviewer_id": request.reviewer_id},
        result,
    )
    if result.get("reason") == "db_error":
        yield encode_sse("text", {"content": busy_message})
    elif not result.get("ok"):
        yield encode_sse("text", {"content": "⚠️ 未能完成工单更新，请稍后重试或联系人工客服。"})
    elif graph_failed:
        yield encode_sse("text", {"content": "⚠️ 退款流程执行遇到问题，审批结果已直接写入数据库。"})
        if request.action == "approve":
            yield encode_sse("ui", timeline_builder(request.reviewer_id))
    elif request.action == "approve":
        yield encode_sse(
            "ui",
            {
                "type": "AgentThinkingStream",
                "props": {
                    "steps": [{
                        "step": "approved",
                        "label": "审批完成",
                        "status": "done",
                        "detail": f"主管 {request.reviewer_id} 已批准，退款处理完成",
                    }]
                },
            },
        )
        yield encode_sse("ui", timeline_builder(request.reviewer_id))
        yield encode_sse("text", {"content": "✅ 退款处理完成！预计 3 个工作日内到账，财务已收到邮件通知。"})
    else:
        yield encode_sse("text", {"content": "❌ 退款申请已拒绝，工单已标记为已拒绝状态。"})
