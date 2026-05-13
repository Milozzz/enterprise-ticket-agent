"""
Langfuse 可观测性模块

提供：
- get_langfuse_callback()  → CallbackHandler（用于 astream_events config）
- get_langfuse_client()    → Langfuse 原生客户端（手动打 span 用）
- flush_langfuse()         → 应用关闭前刷新缓冲区

Langfuse CallbackHandler 会自动追踪：
  - 每个 LangGraph 节点的执行（span）
  - LLM 调用的 token 用量（prompt / completion tokens）
  - Tool 调用的入参和出参
  - 整条链路的 trace（thread_id 作为 session_id）
"""

import os
from functools import lru_cache

from app.core.logging import get_logger

logger = get_logger(__name__)

_langfuse_client = None


def get_langfuse_client():
    """获取 Langfuse 原生客户端（懒初始化，key 未配置时返回 None）"""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key or public_key.startswith("pk-lf-..."):
        logger.info("langfuse_disabled", reason="keys_not_configured")
        return None

    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info("langfuse_client_initialized", host=host)
        return _langfuse_client
    except Exception as e:
        logger.warning("langfuse_client_init_failed", error=str(e))
        return None


def get_langfuse_callback(
    thread_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
):
    """
    获取 Langfuse CallbackHandler 实例（用于 LangGraph astream_events config）
    trace_id 写入 metadata，让 Langfuse trace 与 AuditLog / 前端三处对齐。
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key or public_key.startswith("pk-lf-..."):
        return None

    try:
        from langfuse.callback import CallbackHandler
        cb = CallbackHandler(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            session_id=session_id or thread_id,
            user_id=str(user_id) if user_id else None,
            trace_name=f"ticket_agent_{thread_id}" if thread_id else "ticket_agent",
            metadata={"trace_id": trace_id, "thread_id": thread_id} if trace_id else {"thread_id": thread_id},
        )
        logger.debug("langfuse_callback_created", thread_id=thread_id, trace_id=trace_id)
        return cb
    except Exception as e:
        logger.warning("langfuse_callback_init_failed", error=str(e))
        return None


def flush_langfuse():
    """应用关闭时刷新 Langfuse 缓冲区（确保所有事件都发送出去）"""
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
            logger.info("langfuse_flushed")
        except Exception as e:
            logger.warning("langfuse_flush_failed", error=str(e))
