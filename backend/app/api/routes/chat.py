"""
Agent 聊天路由
"""

import hashlib
import json
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from app.agent.graph import ticket_graph
from app.agent.utils import get_state_val
from app.models.ticket import ChatRequest, ResumeRequest
from app.core.logging import get_logger
from app.core.config import effective_simulate_database_down, get_settings
from app.core.auth import get_optional_user
from app.core.observability import get_langfuse_callback
from app.db.database import AsyncSessionLocal
from app.db.models import Ticket, TicketStatus, AuditLog
from app.core.masking import mask_dict
from sqlalchemy import update, select

logger = get_logger(__name__)
router = APIRouter()

# 第三方（数据库、外部网关等）故障时的对外文案，避免把异常栈暴露给前端
USER_FACING_SYSTEM_BUSY = "系统繁忙，转入人工处理，请稍后再试或联系人工客服。"


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 消息"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---- 缓存工具 ----

def _chat_cache_key(user_id: str, message: str) -> str:
    """生成缓存 key：sha256(user_id:message)"""
    raw = f"{user_id}:{message}"
    return "chat_cache:" + hashlib.sha256(raw.encode()).hexdigest()


async def _redis_get(key: str) -> str | None:
    """异步 Redis GET，连接不可用时返回 None。"""
    try:
        import redis.asyncio as aioredis
        settings = get_settings()
        url = settings.upstash_redis_url or settings.redis_url
        client = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        val = await client.get(key)
        await client.aclose()
        return val
    except Exception:
        return None


async def _redis_setex(key: str, ttl: int, value: str) -> None:
    """异步 Redis SETEX，失败时静默。"""
    try:
        import redis.asyncio as aioredis
        settings = get_settings()
        url = settings.upstash_redis_url or settings.redis_url
        client = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        await client.setex(key, ttl, value)
        await client.aclose()
    except Exception:
        pass


async def _add_audit_log(
    thread_id: str,
    node_name: str,
    event_type: str,
    input_data: dict | None,
    output_data: dict | None,
    trace_id: str | None = None,
    duration_ms: int | None = None,
    success: bool | None = None,
):
    """写入审计日志（携带 trace_id、耗时、成功标志）"""
    try:
        async with AsyncSessionLocal() as session:
            clean_input = {k: v for k, v in (input_data or {}).items() if k != "messages"}
            has_error = isinstance(output_data, dict) and bool(output_data.get("error_message"))
            log = AuditLog(
                thread_id=thread_id,
                trace_id=trace_id,
                node_name=node_name,
                event_type=event_type,
                input_data=mask_dict(clean_input),
                output_data=mask_dict(output_data) if isinstance(output_data, dict) else output_data,
                duration_ms=duration_ms,
                success=success if success is not None else (not has_error),
            )
            session.add(log)
            await session.commit()
            logger.debug("audit_log_added", thread_id=thread_id, node=node_name, trace_id=trace_id)
    except Exception as e:
        logger.error("audit_log_error", error=str(e))


@router.post("/chat")
async def chat_with_agent(
    request: ChatRequest,
    jwt_user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    import time
    thread_id = request.thread_id
    trace_id = request.trace_id or thread_id   # 前端未传时降级用 thread_id
    user_message_text = request.messages[-1]["content"]

    # ── 用户身份：JWT 优先，向后兼容 body 里的 user_id/user_role ──────────────
    if jwt_user:
        effective_user_id = jwt_user["user_id"]
        effective_role = jwt_user["role"]
    else:
        effective_user_id = request.user_id or "anonymous"
        effective_role = request.user_role or "USER"

    # ── 缓存层：相同 (user_id, message) 直接返回缓存结果 ──────────────────
    settings = get_settings()
    cache_key = _chat_cache_key(effective_user_id, user_message_text)

    cached = await _redis_get(cache_key)
    if cached:
        try:
            cached_chunks: list[str] = json.loads(cached)
            logger.info("chat_cache_hit", user_id=effective_user_id, key=cache_key[:20])

            async def cached_stream():
                for chunk in cached_chunks:
                    yield chunk
                yield _sse("done", {})

            return StreamingResponse(
                cached_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Thread-Id": thread_id,
                    "X-Trace-Id": trace_id,
                    "X-Cache": "HIT",
                },
            )
        except Exception as e:
            logger.warning("chat_cache_read_error", error=str(e))

    # ── 正常 Agent 流程 ───────────────────────────────────────────────────
    langfuse_cb = get_langfuse_callback(
        thread_id=thread_id,
        user_id=effective_user_id,
        trace_id=trace_id,
    )
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [langfuse_cb] if langfuse_cb else [],
    }

    # ── 多轮对话：将 request.messages 全部转换为 LangChain messages ──────────
    # 只把用户/助手消息传入；system 由各节点自己注入，避免重复
    lc_messages = []
    for msg in request.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        # 忽略 system 消息（由节点自己管理）

    # 如果前端只发了最新一条，lc_messages 就是单条；多轮时会有历史
    if not lc_messages:
        lc_messages = [HumanMessage(content=user_message_text)]

    initial_state = {
        "messages": lc_messages,
        "user_role": effective_role,
        "user_id": effective_user_id,
        "thread_id": thread_id,
        "trace_id": trace_id,
        "ui_events": [],
    }

    async def event_stream():
        collected: list[str] = []  # 收集本次所有 SSE 块，用于写缓存
        try:
            if effective_simulate_database_down():
                logger.warning("simulate_database_down_active", thread_id=thread_id)
                yield _sse("text", {"content": USER_FACING_SYSTEM_BUSY})
                yield _sse("done", {})
                return

            # 首包 SSE：把 trace_id 发给前端，让它能在 UI 展示
            yield _sse("meta", {"trace_id": trace_id, "thread_id": thread_id})
            yield _sse("text", {"content": "已收到请求，正在查询订单并评估风险...\n\n"})

            from app.agent.state_machine import RefundState
            _refund_state = RefundState.CREATED
            _node_start_times: dict[str, float] = {}

            async for event in ticket_graph.astream_events(
                initial_state,
                config=config,
                version="v2",
            ):
                event_type = event.get("event", "")
                event_name = event.get("name", "")

                logger.debug("langgraph_event", event_type=event_type, event_name=event_name)

                _NODE_NAMES = {
                             "classify_intent", "lookup_order", "check_risk",
                             "fetch_user_history",
                             "human_review", "execute_refund", "send_notification",
                             "answer_node", "answer_policy_node", "summarize_session",
                         }

                if event_type == "on_chain_start" and event_name in _NODE_NAMES:
                    _node_start_times[event_name] = time.monotonic()

                if event_type == "on_chain_end" and event_name in _NODE_NAMES:
                    input_data = event.get("data", {}).get("input")
                    output = event.get("data", {}).get("output")
                    duration_ms = None
                    if event_name in _node_start_times:
                        duration_ms = int((time.monotonic() - _node_start_times.pop(event_name)) * 1000)

                    # 推进业务状态机，把当前 refund_state 写入 AuditLog output_data
                    from app.agent.state_machine import RefundState, transition, InvalidStateTransitionError
                    _NODE_STATE_MAP = {
                        "classify_intent":    RefundState.CLASSIFIED,
                        "lookup_order":       RefundState.ORDER_LOADED,
                        "check_risk":         RefundState.RISK_EVALUATED,
                        "human_review":       RefundState.PENDING_APPROVAL,
                        "execute_refund":     RefundState.REFUNDED,
                        "send_notification":  RefundState.COMPLETED,
                    }
                    if event_name in _NODE_STATE_MAP:
                        next_rs = _NODE_STATE_MAP[event_name]
                        try:
                            _refund_state = transition(_refund_state, next_rs)
                        except InvalidStateTransitionError as ste:
                            logger.warning("state_machine_invalid_transition", error=str(ste))
                        if isinstance(output, dict):
                            output = {**output, "_refund_state": _refund_state.value}

                    await _add_audit_log(
                        thread_id, event_name, event_type, input_data, output,
                        trace_id=trace_id, duration_ms=duration_ms,
                    )

                    if isinstance(output, dict) and output.get("ui_events"):
                        for ui_event in output["ui_events"]:
                            logger.info("sending_ui_event", ui_type=ui_event.get("type"))
                            ui_type = ui_event.get("type", "")
                            if ui_type == "thinking_stream":
                                mapped_type = "AgentThinkingStream"
                            else:
                                mapped_type = "".join(word.capitalize() for word in ui_type.split("_"))

                            chunk = _sse("ui", {
                                "type": mapped_type,
                                "props": ui_event.get("data", {})
                            })
                            collected.append(chunk)
                            yield chunk

                    if isinstance(output, dict) and output.get("reply_text"):
                        reply = output["reply_text"]
                        logger.info("sending_reply_text", node=event_name, length=len(reply))
                        chunk = _sse("text", {"content": reply})
                        collected.append(chunk)
                        yield chunk

                    elif isinstance(output, dict) and output.get("error_message"):
                        chunk = _sse("text", {"content": f"⚠️ {output['error_message']}"})
                        collected.append(chunk)
                        yield chunk

                elif event_type == "on_chat_model_stream":
                    c = event.get("data", {}).get("chunk")
                    if c and hasattr(c, "content") and c.content:
                        chunk = _sse("text", {"content": c.content})
                        collected.append(chunk)
                        yield chunk

            final = ticket_graph.get_state(config)
            if final and final.values:
                if final.next:
                    chunk = _sse("interrupt", {
                        "thread_id": thread_id,
                        "trace_id": trace_id,
                        "next": list(final.next),
                    })
                    collected.append(chunk)
                    yield chunk

                summary = _build_summary(final.values)
                if summary:
                    chunk = _sse("text", {"content": summary})
                    collected.append(chunk)
                    yield chunk

            yield _sse("done", {})

            # ── 写缓存（仅 query_policy 只读意图，且收集到了内容）──────────────
            # 排除：refund（状态变更）、query_order（工单状态可能变化）
            if collected:
                intent_in_state = ""
                if final and final.values:
                    intent_in_state = get_state_val(final.values, "intent", "")
                if intent_in_state == "query_policy":
                    await _redis_setex(cache_key, settings.chat_cache_ttl, json.dumps(collected))
                    logger.info("chat_cache_written", key=cache_key[:20], ttl=settings.chat_cache_ttl)

        except Exception as e:
            import traceback
            logger.error("agent_stream_error", error=str(e), thread_id=thread_id)
            traceback.print_exc()
            yield _sse("text", {"content": USER_FACING_SYSTEM_BUSY})
            yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Thread-Id": thread_id,
            "X-Trace-Id": trace_id,
        },
    )


@router.post("/resume")
async def resume_agent(request: ResumeRequest):
    # 服务端二次角色校验 — 防止前端绕过
    if request.reviewer_role.upper() != "MANAGER":
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：角色 '{request.reviewer_role}' 无法执行审批操作，仅 MANAGER 可审批",
        )

    langfuse_cb = get_langfuse_callback(
        thread_id=request.thread_id,
        user_id=request.reviewer_id,
        session_id=request.thread_id,
    )
    config = {"configurable": {"thread_id": request.thread_id}}
    current_state = ticket_graph.get_state(config)
    graph_has_checkpoint = bool(current_state and current_state.values)
    graph_can_resume = graph_has_checkpoint and bool(current_state.next)

    logger.info(
        "resuming_agent_sse",
        thread_id=request.thread_id,
        action=request.action,
        reviewer_role=request.reviewer_role,
        can_resume=graph_can_resume,
    )

    _RESUME_NODES = {"human_review", "execute_refund", "send_notification"}

    async def resume_stream():
        try:
            if effective_simulate_database_down():
                logger.warning("simulate_database_down_resume", thread_id=request.thread_id)
                yield _sse("text", {"content": USER_FACING_SYSTEM_BUSY})
                yield _sse("done", {})
                return

            action_emoji = "✅" if request.action == "approve" else "❌"
            action_label = "批准" if request.action == "approve" else "拒绝"
            yield _sse("text", {"content": f"{action_emoji} {action_label}操作已确认，正在处理..."})

            if graph_can_resume:
                # ── 图可恢复：用 astream_events 续传，让节点自行更新 DB ──────────
                try:
                    async for event in ticket_graph.astream_events(
                        {
                            "human_decision": request.action,
                            "reviewer_id": request.reviewer_id,
                            "review_comment": request.comment,
                        },
                        config=config,
                        version="v2",
                    ):
                        event_type = event.get("event", "")
                        event_name = event.get("name", "")

                        if event_type == "on_chain_end" and event_name in _RESUME_NODES:
                            input_data = event.get("data", {}).get("input")
                            output = event.get("data", {}).get("output")
                            
                            # 写入审计日志
                            await _add_audit_log(request.thread_id, event_name, event_type, input_data, output)

                            if isinstance(output, dict):
                                for ui_event in output.get("ui_events", []):
                                    ui_type = ui_event.get("type", "")
                                    mapped_type = (
                                        "AgentThinkingStream"
                                        if ui_type == "thinking_stream"
                                        else "".join(w.capitalize() for w in ui_type.split("_"))
                                    )
                                    yield _sse("ui", {
                                        "type": mapped_type,
                                        "props": ui_event.get("data", {}),
                                    })
                                if output.get("reply_text"):
                                    yield _sse("text", {"content": output["reply_text"]})
                                elif output.get("error_message"):
                                    yield _sse("text", {"content": f"⚠️ {output['error_message']}"})

                    # 最终总结
                    final = ticket_graph.get_state(config)
                    if final and final.values:
                        summary = _build_summary(final.values)
                        if summary:
                            yield _sse("text", {"content": summary})

                except Exception as graph_err:
                    logger.warning("graph_resume_stream_failed_using_fallback", error=str(graph_err))
                    # 图执行失败 → 降级为 DB 直写 + 合成事件
                    ticket_id_in_state = get_state_val(current_state.values, "ticket_id") if graph_has_checkpoint else None
                    direct_res = await _direct_db_approve(
                        ticket_id_in_state,
                        request.action,
                        request.thread_id,
                        reviewer_id=request.reviewer_id,
                    )
                    await _add_audit_log(
                        request.thread_id,
                        "direct_db_approve",
                        "fallback_db_write",
                        {"ticket_id": ticket_id_in_state, "action": request.action, "reviewer_id": request.reviewer_id},
                        direct_res,
                    )
                    if direct_res.get("reason") == "db_error":
                        yield _sse("text", {"content": USER_FACING_SYSTEM_BUSY})
                    elif direct_res.get("ok"):
                        yield _sse("text", {"content": "⚠️ 退款流程执行遇到问题，审批结果已直接写入数据库。"})
                        if request.action == "approve":
                            yield _sse("ui", _synthetic_timeline(request.reviewer_id))
                    else:
                        yield _sse("text", {"content": "⚠️ 审批结果未能写入数据库，请稍后重试或联系人工客服。"})

            else:
                # ── 图不可恢复（checkpoint 丢失 / 已结束）→ 直接写 DB + 合成事件 ──
                ticket_id_in_state = get_state_val(current_state.values, "ticket_id") if graph_has_checkpoint else None
                direct_res = await _direct_db_approve(
                    ticket_id_in_state,
                    request.action,
                    request.thread_id,
                    reviewer_id=request.reviewer_id,
                )
                await _add_audit_log(
                    request.thread_id,
                    "direct_db_approve",
                    "fallback_db_write",
                    {"ticket_id": ticket_id_in_state, "action": request.action, "reviewer_id": request.reviewer_id},
                    direct_res,
                )

                if direct_res.get("reason") == "db_error":
                    yield _sse("text", {"content": USER_FACING_SYSTEM_BUSY})
                elif not direct_res.get("ok"):
                    yield _sse("text", {"content": "⚠️ 未能完成工单更新，请稍后重试或联系人工客服。"})
                elif request.action == "approve":
                    yield _sse("ui", {
                        "type": "AgentThinkingStream",
                        "props": {
                            "steps": [{
                                "step": "approved",
                                "label": "审批完成",
                                "status": "done",
                                "detail": f"主管 {request.reviewer_id} 已批准，退款处理完成",
                            }]
                        },
                    })
                    yield _sse("ui", _synthetic_timeline(request.reviewer_id))
                    yield _sse("text", {"content": "✅ 退款处理完成！预计 3 个工作日内到账，财务已收到邮件通知。"})
                else:
                    yield _sse("text", {"content": "❌ 退款申请已拒绝，工单已标记为已拒绝状态。"})

            yield _sse("done", {})

        except Exception as e:
            logger.error("resume_stream_error", error=str(e))
            import traceback
            traceback.print_exc()
            yield _sse("text", {"content": USER_FACING_SYSTEM_BUSY})
            yield _sse("done", {})

    return StreamingResponse(
        resume_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


def _synthetic_timeline(reviewer_id: str) -> dict:
    """构造合成的退款完成时间线（图不可恢复时用）"""
    return {
        "type": "RefundTimeline",
        "props": {
            "steps": [
                {"label": "提交退款申请", "status": "completed", "description": ""},
                {"label": "审批通过", "status": "completed", "description": f"审批人：{reviewer_id}"},
                {"label": "退款完成", "status": "completed", "description": "已退至原支付账户"},
            ]
        },
    }


async def _resolve_operator_id(reviewer_id: str | None, session) -> int | None:
    """将前端传入的 reviewer_id 字符串解析为数据库 User.id。
    先尝试按 name 匹配，再尝试按 email 匹配，找不到时返回 None（operator_id 字段可为空）。
    """
    if not reviewer_id:
        return None
    from app.db.models import User
    row = (await session.execute(
        select(User).where(User.name == reviewer_id)
    )).scalars().first()
    if row:
        return row.id
    # 兼容 email 格式的 reviewer_id
    row = (await session.execute(
        select(User).where(User.email == reviewer_id)
    )).scalars().first()
    return row.id if row else None


async def _direct_db_approve(ticket_id: str | None, action: str, thread_id: str, reviewer_id: str | None = None) -> dict:
    """Graph 已结束或 checkpoint 丢失时直接操作 DB 完成审批流程（返回写库结果供审计）"""
    try:
        new_status = TicketStatus.APPROVED if action == "approve" else TicketStatus.REJECTED
        ticket_int_id: int | None = None

        if ticket_id:
            try:
                ticket_int_id = int(ticket_id)
            except (ValueError, TypeError):
                pass

        if ticket_int_id is None:
            # 按 thread_id 查找工单
            async with AsyncSessionLocal() as session:
                row = (await session.execute(
                    select(Ticket).where(Ticket.thread_id == thread_id).order_by(Ticket.id.desc())
                )).scalars().first()
                if not row:
                    logger.warning("direct_db_approve_no_ticket", thread_id=thread_id)
                    return {"ok": False, "reason": "no_ticket", "thread_id": thread_id}
                ticket_int_id = row.id

        # 直接绕过图执行：approve → COMPLETED（跳过 execute_refund_node）
        # reject → REJECTED
        final_status = TicketStatus.COMPLETED if action == "approve" else new_status

        async with AsyncSessionLocal() as session:
            operator_id_value = await _resolve_operator_id(reviewer_id, session)
            result = await session.execute(
                update(Ticket)
                .where(Ticket.id == ticket_int_id)
                .values(status=final_status, operator_id=operator_id_value)
            )
            await session.commit()
            logger.info("direct_db_approved", ticket_id=ticket_int_id, status=final_status, rowcount=result.rowcount)
            return {
                "ok": True,
                "ticket_id": ticket_int_id,
                "status": final_status.value if hasattr(final_status, "value") else str(final_status),
                "operator_id": operator_id_value,
                "rowcount": result.rowcount,
                "thread_id": thread_id,
                "action": action,
                "reviewer_id": reviewer_id,
            }
    except Exception as e:
        logger.error("direct_db_approve_failed", error=str(e), thread_id=thread_id)
        return {"ok": False, "reason": "db_error", "thread_id": thread_id}


@router.get("/debug/{thread_id}")
async def debug_state(thread_id: str):
    """调试接口：查看指定会话的 Agent 当前状态（仅限开发环境）"""
    settings = get_settings()
    if settings.environment != "development":
        raise HTTPException(status_code=403, detail="该接口仅在开发环境可用")
    config = {"configurable": {"thread_id": thread_id}}
    state = ticket_graph.get_state(config)
    if not state or not state.values:
        return {"status": "no_state", "thread_id": thread_id}
    # 过滤掉 messages 避免输出太长
    values = {k: v for k, v in state.values.items() if k != "messages"}
    return {"status": "ok", "thread_id": thread_id, "state": values, "next": state.next}


@router.get("/audit/{thread_id}")
async def get_audit_logs(thread_id: str):
    """查询指定会话的审计日志"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AuditLog)
                .where(AuditLog.thread_id == thread_id)
                .order_by(AuditLog.created_at.asc())
            )
            logs = result.scalars().all()
            return [{
                "node": log.node_name,
                "event": log.event_type,
                "input": log.input_data,
                "output": log.output_data,
                "trace_id": log.trace_id,
                "duration_ms": log.duration_ms,
                "success": log.success,
                "time": log.created_at.isoformat()
            } for log in logs]
    except Exception as e:
        logger.error("audit_logs_db_error", error=str(e), thread_id=thread_id)
        return []


@router.get("/replay/{thread_id}")
async def get_replay(thread_id: str):
    """
    链路回放接口：返回单个 thread 的完整执行链路，供 Dashboard 可视化。
    包含每个节点的耗时、成功状态、业务状态机快照、trace_id。
    """
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AuditLog)
                .where(AuditLog.thread_id == thread_id)
                .order_by(AuditLog.created_at.asc())
            )
            logs = result.scalars().all()

        if not logs:
            return {"thread_id": thread_id, "trace_id": None, "nodes": [], "summary": {}}

        trace_id = next((l.trace_id for l in logs if l.trace_id), None)

        nodes = []
        for log in logs:
            output = log.output_data or {}
            nodes.append({
                "node": log.node_name,
                "refund_state": output.get("_refund_state"),
                "success": log.success,
                "duration_ms": log.duration_ms,
                "error": output.get("error_message"),
                "time": log.created_at.isoformat(),
                # 输出摘要：只取关键字段，避免过大
                "summary": {k: v for k, v in output.items()
                            if k in ("intent", "order_id", "order_amount", "risk_score",
                                     "risk_level", "requires_human_approval", "human_decision",
                                     "refund_id", "refund_success", "notification_to",
                                     "_refund_state")},
            })

        total_ms = sum(n["duration_ms"] or 0 for n in nodes)
        failed_nodes = [n["node"] for n in nodes if n["success"] is False]

        return {
            "thread_id": thread_id,
            "trace_id": trace_id,
            "nodes": nodes,
            "summary": {
                "total_duration_ms": total_ms,
                "node_count": len(nodes),
                "failed_nodes": failed_nodes,
                "success": len(failed_nodes) == 0,
            },
        }
    except Exception as e:
        logger.error("replay_error", error=str(e), thread_id=thread_id)
        return {"thread_id": thread_id, "trace_id": None, "nodes": [], "summary": {}}





def _build_summary(state) -> str:
    intent = get_state_val(state, "intent", "refund")

    # query_order / query_policy / other 意图：reply_text 已在 on_chain_end 实时发出，此处无需重复
    if intent in ("query_order", "query_policy", "other"):
        return ""

    # refund 意图
    if get_state_val(state, "refund_success"):
        return (
            f"✅ 退款处理完成！\n\n"
            f"退款单号：{get_state_val(state, 'refund_id', 'N/A')}\n"
            f"退款金额：¥{get_state_val(state, 'order_amount', 0)}\n"
            f"预计 3 个工作日内到账，财务已收到邮件通知。"
        )
    if get_state_val(state, "human_decision") == "reject":
        return "❌ 退款申请已被拒绝。如有疑问请联系客服。"
    if get_state_val(state, "requires_human_approval") and not get_state_val(state, "human_decision"):
        return "⏳ 退款金额超过风控阈值，请等待主管在上方面板审批。"
    if get_state_val(state, "error_message"):
        return f"⚠️ 处理遇到问题：{get_state_val(state, 'error_message')}\n请稍后重试。"
    return ""
