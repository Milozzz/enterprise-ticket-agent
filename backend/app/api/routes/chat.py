"""Thin HTTP boundary for LangGraph chat, resume, audit, and replay APIs."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import ticket_graph
from app.core.auth import get_current_user, get_optional_user
from app.core.config import effective_simulate_database_down, get_settings
from app.core.logging import get_logger
from app.core.observability import get_langfuse_callback
from app.db.tenant_context import current_tenant_id, tenant_scope
from app.models.ticket import ChatRequest, ResumeRequest
from app.services.approval_resume import (
    direct_db_approve as _direct_db_approve,
    synthetic_timeline as _synthetic_timeline,
)
from app.services.audit_service import (
    add_audit_log as _add_audit_log,
    list_audit_logs,
    replay_audit_logs,
)
from app.services.chat_cache import (
    chat_cache_key as _chat_cache_key,
    redis_get as _redis_get,
    redis_setex as _redis_setex,
)
from app.services.chat_stream import (
    cached_sse_stream,
    pending_conversational_interrupt,
    stream_agent,
    stream_resume,
)

logger = get_logger(__name__)
router = APIRouter()

USER_FACING_SYSTEM_BUSY = "系统繁忙，转入人工处理，请稍后再试或联系人工客服。"


async def _tenant_scoped_stream(stream, tenant_id: str):
    """D3 修复：TenantContextMiddleware 的 tenant_scope 在 call_next 返回时
    即退出，而 StreamingResponse body 在其后才迭代——流式期间 contextvar
    已被 reset 回默认租户，导致 audit_logs/tickets/user_memory 等所有
    流式落库写到默认租户（或被 RLS WITH CHECK 拒绝）。

    修法：把整个 SSE 流重新包进 tenant_scope。生成器每次 __anext__ 都在
    响应任务的上下文中执行，进入时设置的 contextvar 对流内全部 DB 写入
    （含 LangGraph 节点内的写入）生效，流结束/异常时正常复位。"""
    with tenant_scope(tenant_id):
        async for chunk in stream:
            yield chunk


def _stream_response(stream, *, thread_id: str | None = None, trace_id: str | None = None, cache_hit=False):
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    if thread_id:
        headers["X-Thread-Id"] = thread_id
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    if cache_hit:
        headers["X-Cache"] = "HIT"
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)


@router.post("/chat")
async def chat_with_agent(
    request: ChatRequest,
    jwt_user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    if not request.messages:
        raise HTTPException(status_code=422, detail="messages must contain at least one item")

    thread_id = request.thread_id
    trace_id = request.trace_id or thread_id
    user_message = str(request.messages[-1].get("content", ""))
    effective_user_id = str((jwt_user or {}).get("user_id") or request.user_id or "anonymous")
    effective_role = str((jwt_user or {}).get("role") or request.user_role or "USER")
    tenant_id = str((jwt_user or {}).get("tenant_id") or current_tenant_id())
    settings = get_settings()
    cache_key = _chat_cache_key(effective_user_id, user_message)

    cached = await _redis_get(cache_key)
    if cached:
        try:
            chunks = json.loads(cached)
            if isinstance(chunks, list):
                return _stream_response(
                    cached_sse_stream(chunks),
                    thread_id=thread_id,
                    trace_id=trace_id,
                    cache_hit=True,
                )
        except (TypeError, ValueError) as exc:
            logger.warning("chat_cache_read_error", error=str(exc))

    callback = get_langfuse_callback(
        thread_id=thread_id,
        user_id=effective_user_id,
        trace_id=trace_id,
    )
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [callback] if callback else [],
    }
    # H1（长对话优化）：checkpointer 已经持久化了该 thread 的全部历史，
    # 前端每轮却会重发全量消息；add_messages 对无 ID 消息一律追加，
    # 若照单全收，第 N 轮 state 里会堆积 O(N²) 条重复消息（token 费用 /
    # 时延同步爆炸）。因此：thread 已有 checkpoint → 只传最新一条用户消息；
    # 全新 thread → 传全量（首轮或跨端迁移场景）。
    pending_state = None
    thread_has_history = False
    try:
        pending_state = ticket_graph.get_state(config)
        thread_has_history = bool(
            pending_state
            and pending_state.values
            and pending_state.values.get("messages")
        )
    except Exception as exc:
        logger.warning("thread_state_check_failed", error=str(exc))

    if thread_has_history:
        messages = [HumanMessage(content=user_message)]
    else:
        messages = []
        for item in request.messages:
            content = str(item.get("content", ""))
            if item.get("role", "user") == "user":
                messages.append(HumanMessage(content=content))
            elif item.get("role") == "assistant":
                messages.append(AIMessage(content=content))
        if not messages:
            messages = [HumanMessage(content=user_message)]

    initial_state = {
        "messages": messages,
        "user_role": effective_role,
        "user_id": effective_user_id,
        "thread_id": thread_id,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "ui_events": [],
    }

    # A3/A7：该 thread 若停在"等待用户回答"的 interrupt 上（意图澄清 / 补槽），
    # 这条新消息就是回答——作为 resume 输入恢复被中断的节点，而非重新开跑。
    try:
        if pending_state and pending_conversational_interrupt(pending_state):
            from langgraph.types import Command

            initial_state = Command(resume={"answer": user_message})
            logger.info(
                "conversational_interrupt_resume",
                thread_id=thread_id,
                kind=(pending_conversational_interrupt(pending_state) or {}).get("kind"),
            )
    except Exception as exc:
        logger.warning("pending_interrupt_check_failed", error=str(exc))

    return _stream_response(
        _tenant_scoped_stream(
            stream_agent(
                graph=ticket_graph,
                initial_state=initial_state,
                config=config,
                thread_id=thread_id,
                trace_id=trace_id,
                cache_key=cache_key,
                cache_ttl=settings.chat_cache_ttl,
                busy_message=USER_FACING_SYSTEM_BUSY,
                audit_writer=_add_audit_log,
                cache_writer=_redis_setex,
                database_down_check=effective_simulate_database_down,
            ),
            tenant_id,
        ),
        thread_id=thread_id,
        trace_id=trace_id,
    )


@router.post("/resume")
async def resume_agent(
    request: ResumeRequest,
    jwt_user: Annotated[dict, Depends(get_current_user)],
):
    # 审批人身份/角色一律以已验证的 JWT 为准，忽略请求体里客户端可伪造的角色字段。
    reviewer_role = str(jwt_user.get("role") or "").upper()
    reviewer_id = str(jwt_user.get("user_id") or "")
    if reviewer_role not in {"MANAGER", "SECURITY", "FINANCE"}:
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：角色 '{reviewer_role}' 无法执行审批操作",
        )
    # 用可信身份覆盖请求体，后续所有下游都用这份。
    request.reviewer_role = reviewer_role
    request.reviewer_id = reviewer_id

    callback = get_langfuse_callback(
        thread_id=request.thread_id,
        user_id=request.reviewer_id,
        session_id=request.thread_id,
    )
    config = {
        "configurable": {"thread_id": request.thread_id},
        "callbacks": [callback] if callback else [],
    }
    current_state = ticket_graph.get_state(config)
    graph_has_checkpoint = bool(current_state and current_state.values)
    graph_can_resume = graph_has_checkpoint and bool(current_state.next)
    # D3：resume 流同样在中间件作用域之外迭代，租户以 JWT 为准重新绑定
    resume_tenant = str(jwt_user.get("tenant_id") or current_tenant_id())
    return _stream_response(
        _tenant_scoped_stream(
            stream_resume(
                graph=ticket_graph,
                request=request,
                config=config,
                current_state=current_state,
                graph_has_checkpoint=graph_has_checkpoint,
                graph_can_resume=graph_can_resume,
                busy_message=USER_FACING_SYSTEM_BUSY,
                audit_writer=_add_audit_log,
                direct_approver=_direct_db_approve,
                timeline_builder=_synthetic_timeline,
                database_down_check=effective_simulate_database_down,
            ),
            resume_tenant,
        )
    )


@router.get("/debug/{thread_id}")
async def debug_state(thread_id: str):
    if get_settings().environment != "development":
        raise HTTPException(status_code=403, detail="该接口仅在开发环境可用")
    config = {"configurable": {"thread_id": thread_id}}
    state = ticket_graph.get_state(config)
    if not state or not state.values:
        return {"status": "no_state", "thread_id": thread_id}
    values = {key: value for key, value in state.values.items() if key != "messages"}
    return {"status": "ok", "thread_id": thread_id, "state": values, "next": state.next}


@router.get("/audit/{thread_id}")
async def get_audit_logs(thread_id: str):
    return await list_audit_logs(thread_id)


@router.get("/replay/{thread_id}")
async def get_replay(thread_id: str):
    return await replay_audit_logs(thread_id)
