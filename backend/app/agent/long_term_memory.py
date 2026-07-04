from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import UserLongTermMemory
from app.db.tenant_context import current_tenant_id, tenant_scope

logger = get_logger(__name__)
settings = get_settings()


async def upsert_long_term_memory(
    *,
    user_id: str,
    memory_type: str,
    memory_key: str,
    content: str,
    attributes: dict[str, Any] | None = None,
    confidence: float = 1.0,
    importance: int = 50,
    source_thread_id: str | None = None,
    source_type: str = "workflow",
    tenant_id: str | None = None,
    retention_days: int | None = None,
    session_factory=AsyncSessionLocal,
) -> UserLongTermMemory:
    tenant = tenant_id or current_tenant_id()
    now = datetime.utcnow()
    days = retention_days if retention_days is not None else settings.long_term_memory_retention_days
    expires_at = now + timedelta(days=days) if days > 0 else None
    with tenant_scope(tenant):
        async with session_factory() as session:
            memory = await session.scalar(
                select(UserLongTermMemory).where(
                    UserLongTermMemory.tenant_id == tenant,
                    UserLongTermMemory.user_id == str(user_id),
                    UserLongTermMemory.memory_type == memory_type,
                    UserLongTermMemory.memory_key == memory_key,
                )
            )
            if memory is None:
                memory = UserLongTermMemory(
                    tenant_id=tenant,
                    user_id=str(user_id),
                    memory_type=memory_type,
                    memory_key=memory_key,
                    content=content[:1000],
                    attributes=attributes or {},
                    confidence=min(max(confidence, 0.0), 1.0),
                    importance=min(max(importance, 0), 100),
                    source_thread_id=source_thread_id,
                    source_type=source_type,
                    status="active",
                    valid_from=now,
                    expires_at=expires_at,
                    created_at=now,
                    updated_at=now,
                )
                session.add(memory)
            else:
                memory.content = content[:1000]
                memory.attributes = attributes or {}
                memory.confidence = min(max(confidence, 0.0), 1.0)
                memory.importance = min(max(importance, 0), 100)
                memory.source_thread_id = source_thread_id
                memory.source_type = source_type
                memory.status = "active"
                memory.expires_at = expires_at
                memory.updated_at = now
            await session.commit()
            await session.refresh(memory)
            return memory


async def list_active_memories(
    user_id: str,
    *,
    tenant_id: str | None = None,
    memory_types: list[str] | None = None,
    limit: int = 20,
    session_factory=AsyncSessionLocal,
) -> list[dict[str, Any]]:
    tenant = tenant_id or current_tenant_id()
    now = datetime.utcnow()
    with tenant_scope(tenant):
        async with session_factory() as session:
            query = select(UserLongTermMemory).where(
                UserLongTermMemory.tenant_id == tenant,
                UserLongTermMemory.user_id == str(user_id),
                UserLongTermMemory.status == "active",
                or_(UserLongTermMemory.expires_at.is_(None), UserLongTermMemory.expires_at > now),
            )
            if memory_types:
                query = query.where(UserLongTermMemory.memory_type.in_(memory_types))
            rows = (
                await session.execute(
                    query.order_by(
                        UserLongTermMemory.importance.desc(),
                        UserLongTermMemory.updated_at.desc(),
                    ).limit(min(max(limit, 1), 100))
                )
            ).scalars().all()
    return [
        {
            "id": row.id,
            "type": row.memory_type,
            "key": row.memory_key,
            "content": row.content,
            "attributes": row.attributes or {},
            "confidence": row.confidence,
            "importance": row.importance,
            "source_thread_id": row.source_thread_id,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        }
        for row in rows
    ]


async def capture_explicit_preferences(
    *,
    user_id: str,
    conversation_text: str,
    thread_id: str,
    tenant_id: str,
    session_factory=AsyncSessionLocal,
) -> list[str]:
    """Capture only explicit preferences; never persist the raw conversation."""

    text = conversation_text.lower()
    captured: list[str] = []
    preferences = []
    if "邮件" in text or "email" in text:
        preferences.append(("notification_channel", "email", "用户明确偏好邮件通知"))
    elif "短信" in text or "sms" in text:
        preferences.append(("notification_channel", "sms", "用户明确偏好短信通知"))
    if "英文回复" in text or "reply in english" in text:
        preferences.append(("language", "en", "用户明确偏好英文回复"))
    elif "中文回复" in text:
        preferences.append(("language", "zh-CN", "用户明确偏好中文回复"))

    for key, value, content in preferences:
        await upsert_long_term_memory(
            user_id=user_id,
            memory_type="preference",
            memory_key=key,
            content=content,
            attributes={"value": value},
            confidence=1.0,
            importance=60,
            source_thread_id=thread_id,
            source_type="explicit_user_statement",
            tenant_id=tenant_id,
            session_factory=session_factory,
        )
        captured.append(key)
    return captured
