"""Thin HTTP boundary for LangGraph chat, resume, audit, and replay APIs."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import ticket_graph
from app.core.auth import get_optional_user
from app.core.config import effective_simulate_database_down, get_settings
from app.core.logging import get_logger
from app.core.observability import get_langfuse_callback
from app.db.tenant_context import current_tenant_id
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
from app.services.chat_stream import cached_sse_stream, stream_agent, stream_resume

logger = get_logger(__name__)
router = APIRouter()

USER_FACING_SYSTEM_BUSY = "系统繁忙，转入人工处理，请稍后再试或联系人工客服。"


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
    return _stream_response(
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
        thread_id=thread_id,
        trace_id=trace_id,
    )


@router.post("/resume")
async def resume_agent(request: ResumeRequest):
    if request.reviewer_role.upper() not in {"MANAGER", "SECURITY", "FINANCE"}:
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：角色 '{request.reviewer_role}' 无法执行审批操作",
        )

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
    return _stream_response(
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
