from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import re
from typing import Any

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import UserLongTermMemory
from app.db.tenant_context import current_tenant_id, tenant_scope

logger = get_logger(__name__)
settings = get_settings()

MEMORY_CLASS_BY_TYPE = {
    "preference": "semantic",
    "enterprise_fact": "semantic",
    "task_episode": "episodic",
    "human_correction": "episodic",
    "successful_plan": "procedural",
    "risk_signal": "risk",
    "fraud_signal": "risk",
    "dispute_history": "risk",
}

SOURCE_PRIORITY = {
    "explicit_user_statement": 100,
    "human_approval": 95,
    "human_correction": 95,
    "canonical_system": 90,
    "verified_successful_execution": 85,
    "approval_decision": 85,
    "workflow": 70,
    "llm_inference": 40,
}

MEMORY_HALF_LIFE_DAYS = {
    "semantic": 365.0,
    "episodic": 90.0,
    "procedural": 180.0,
    "risk": 365.0,
}


def resolve_memory_fact_conflict(
    *,
    memory_value: Any,
    current_value: Any,
    memory_source: str,
    current_source: str = "canonical_system",
) -> dict[str, Any]:
    """Resolve a memory/current-fact conflict without letting memory override truth.

    Memory is advisory context. A fresher source with equal or higher authority
    wins, while the disagreement remains explicit for audit and human review.
    """

    memory_priority = SOURCE_PRIORITY.get(memory_source, 50)
    current_priority = SOURCE_PRIORITY.get(current_source, 50)
    conflict = memory_value != current_value
    current_wins = not conflict or current_priority >= memory_priority
    return {
        "conflict": conflict,
        "selected_value": current_value if current_wins else memory_value,
        "selected_source": current_source if current_wins else memory_source,
        "memory_value": memory_value,
        "current_value": current_value,
        "memory_source_priority": memory_priority,
        "current_source_priority": current_priority,
        "memory_used_as_context_only": bool(conflict and current_wins),
        "requires_review": bool(conflict),
    }


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
            memory_class = MEMORY_CLASS_BY_TYPE.get(memory_type, "episodic")
            if memory is None:
                stored_attributes = {
                    **(attributes or {}),
                    "_memory_class": memory_class,
                    "_revision": 1,
                    "_source_priority": SOURCE_PRIORITY.get(source_type, 50),
                }
                memory = UserLongTermMemory(
                    tenant_id=tenant,
                    user_id=str(user_id),
                    memory_type=memory_type,
                    memory_key=memory_key,
                    content=content[:1000],
                    attributes=stored_attributes,
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
                previous_attributes = dict(memory.attributes or {})
                revision = int(previous_attributes.get("_revision") or 1) + 1
                stored_attributes = {
                    **previous_attributes,
                    **(attributes or {}),
                    "_memory_class": memory_class,
                    "_revision": revision,
                    "_source_priority": SOURCE_PRIORITY.get(source_type, 50),
                }
                if memory.content != content[:1000]:
                    stored_attributes["_previous_content_hash"] = hashlib.sha256(
                        memory.content.encode("utf-8")
                    ).hexdigest()
                    history = list(previous_attributes.get("_history") or [])[-9:]
                    history.append(
                        {
                            "content_hash": hashlib.sha256(
                                memory.content.encode("utf-8")
                            ).hexdigest(),
                            "source_type": memory.source_type,
                            "updated_at": memory.updated_at.isoformat()
                            if memory.updated_at
                            else None,
                        }
                    )
                    stored_attributes["_history"] = history
                previous_priority = int(
                    previous_attributes.get("_source_priority")
                    or SOURCE_PRIORITY.get(memory.source_type, 50)
                )
                incoming_priority = SOURCE_PRIORITY.get(source_type, 50)
                if memory.content != content[:1000] and incoming_priority < previous_priority:
                    conflicts = list(previous_attributes.get("_conflicts") or [])[-9:]
                    conflicts.append(
                        {
                            "candidate_hash": hashlib.sha256(
                                content[:1000].encode("utf-8")
                            ).hexdigest(),
                            "source_type": source_type,
                            "recorded_at": now.isoformat(),
                        }
                    )
                    stored_attributes["_conflicts"] = conflicts
                    stored_attributes["_source_priority"] = previous_priority
                    confidence = min(float(memory.confidence), max(0.0, confidence * 0.9))
                else:
                    memory.content = content[:1000]
                    memory.source_type = source_type
                memory.attributes = stored_attributes
                memory.confidence = min(max(confidence, 0.0), 1.0)
                memory.importance = min(max(importance, 0), 100)
                memory.source_thread_id = source_thread_id
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
    query: str | None = None,
    limit: int = 20,
    session_factory=AsyncSessionLocal,
) -> list[dict[str, Any]]:
    tenant = tenant_id or current_tenant_id()
    now = datetime.utcnow()
    with tenant_scope(tenant):
        async with session_factory() as session:
            statement = select(UserLongTermMemory).where(
                UserLongTermMemory.tenant_id == tenant,
                UserLongTermMemory.user_id == str(user_id),
                UserLongTermMemory.status == "active",
                or_(UserLongTermMemory.expires_at.is_(None), UserLongTermMemory.expires_at > now),
            )
            if memory_types:
                statement = statement.where(UserLongTermMemory.memory_type.in_(memory_types))
            candidate_limit = min(max(limit * 5 if query else limit, 1), 100)
            rows = (
                await session.execute(
                    statement.order_by(
                        UserLongTermMemory.importance.desc(),
                        UserLongTermMemory.updated_at.desc(),
                    ).limit(candidate_limit)
                )
            ).scalars().all()
    ranked = []
    for row in rows:
        public = {
            "id": row.id,
            "type": row.memory_type,
            "key": row.memory_key,
            "content": row.content,
            "attributes": row.attributes or {},
            "confidence": row.confidence,
            "memory_class": (row.attributes or {}).get(
                "_memory_class", MEMORY_CLASS_BY_TYPE.get(row.memory_type, "episodic")
            ),
            "importance": row.importance,
            "source_thread_id": row.source_thread_id,
            "source_type": row.source_type,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        public["rank_score"] = _memory_rank_score(public, query=query, now=now)
        public["effective_confidence"] = _effective_memory_confidence(public, now=now)
        ranked.append(public)
    ranked.sort(key=lambda item: (item["rank_score"], item["importance"]), reverse=True)
    return ranked[: min(max(limit, 1), 100)]


async def forget_long_term_memory(
    *,
    user_id: str,
    memory_id: int,
    tenant_id: str | None = None,
    session_factory=AsyncSessionLocal,
) -> bool:
    """Soft-delete one memory inside the tenant/user boundary."""
    tenant = tenant_id or current_tenant_id()
    with tenant_scope(tenant):
        async with session_factory() as session:
            memory = await session.scalar(
                select(UserLongTermMemory).where(
                    UserLongTermMemory.id == memory_id,
                    UserLongTermMemory.tenant_id == tenant,
                    UserLongTermMemory.user_id == str(user_id),
                    UserLongTermMemory.status == "active",
                )
            )
            if memory is None:
                return False
            memory.status = "forgotten"
            memory.updated_at = datetime.utcnow()
            await session.commit()
            return True


async def forget_all_long_term_memories(
    *,
    user_id: str,
    tenant_id: str | None = None,
    session_factory=AsyncSessionLocal,
) -> int:
    """Exercise the user's deletion right without destroying audit row identity."""
    tenant = tenant_id or current_tenant_id()
    with tenant_scope(tenant):
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(UserLongTermMemory).where(
                        UserLongTermMemory.tenant_id == tenant,
                        UserLongTermMemory.user_id == str(user_id),
                        UserLongTermMemory.status == "active",
                    )
                )
            ).scalars().all()
            now = datetime.utcnow()
            for row in rows:
                row.status = "forgotten"
                row.content = "[deleted by user]"
                row.attributes = {
                    "_memory_class": (row.attributes or {}).get("_memory_class"),
                    "_deleted_at": now.isoformat(),
                    "_revision": int((row.attributes or {}).get("_revision") or 1) + 1,
                }
                row.updated_at = now
            if rows:
                await session.commit()
            return len(rows)


def _memory_rank_score(
    memory: dict[str, Any],
    *,
    query: str | None,
    now: datetime,
) -> float:
    query_tokens = _memory_tokens(query or "")
    memory_text = " ".join(
        [
            str(memory.get("key") or ""),
            str(memory.get("content") or ""),
            str(memory.get("attributes") or ""),
        ]
    )
    memory_tokens = _memory_tokens(memory_text)
    relevance = (
        len(query_tokens & memory_tokens) / max(1, len(query_tokens))
        if query_tokens
        else 0.5
    )
    importance = float(memory.get("importance") or 0) / 100
    confidence = _effective_memory_confidence(memory, now=now)
    updated_at_raw = memory.get("updated_at")
    try:
        updated_at = datetime.fromisoformat(str(updated_at_raw))
        age_days = max(0.0, (now - updated_at).total_seconds() / 86400)
    except (TypeError, ValueError):
        age_days = 365.0
    recency = 1.0 / (1.0 + age_days / 30.0)
    return round(0.5 * relevance + 0.2 * importance + 0.2 * confidence + 0.1 * recency, 6)


def _effective_memory_confidence(
    memory: dict[str, Any],
    *,
    now: datetime,
) -> float:
    base = min(max(float(memory.get("confidence") or 0), 0.0), 1.0)
    memory_class = str(memory.get("memory_class") or "episodic")
    half_life = MEMORY_HALF_LIFE_DAYS.get(memory_class, 90.0)
    try:
        updated_at = datetime.fromisoformat(str(memory.get("updated_at")))
        age_days = max(0.0, (now - updated_at).total_seconds() / 86400)
    except (TypeError, ValueError):
        age_days = half_life
    return round(base * (0.5 ** (age_days / max(1.0, half_life))), 6)


def _memory_tokens(text: str) -> set[str]:
    normalized = str(text or "").lower()
    words = {item for item in re.findall(r"[a-z0-9_]{2,}", normalized)}
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return words


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


async def capture_task_episode(
    state: dict[str, Any],
    *,
    outcome: str,
    human_override: bool = False,
    session_factory=AsyncSessionLocal,
) -> str | None:
    task = dict(state.get("task_spec") or {})
    if not task:
        return None
    task_id = str(task.get("task_id") or "")
    if not task_id:
        return None
    scenario_id = str(task.get("scenario_id") or "unknown")
    result = dict(state.get("verification_result") or {})
    await upsert_long_term_memory(
        user_id=str(task.get("requester_id") or "anonymous"),
        memory_type="human_correction" if human_override else "task_episode",
        memory_key=task_id,
        content=f"{scenario_id} task finished with outcome {outcome}",
        attributes={
            "task_id": task_id,
            "scenario_id": scenario_id,
            "outcome": outcome,
            "human_override": human_override,
            "reason_codes": [
                item.get("code") for item in result.get("issues") or []
            ],
            "plan_revision": (state.get("plan_graph") or {}).get("revision"),
            "cited_evidence_ids": result.get("cited_evidence_ids") or [],
        },
        confidence=1.0 if human_override else 0.95,
        importance=90 if human_override else 65,
        source_thread_id=str(task.get("thread_id") or state.get("thread_id") or ""),
        source_type="human_correction" if human_override else "workflow",
        tenant_id=str(task.get("tenant_id") or state.get("tenant_id") or "default"),
        retention_days=365 if human_override else 180,
        session_factory=session_factory,
    )
    return task_id
