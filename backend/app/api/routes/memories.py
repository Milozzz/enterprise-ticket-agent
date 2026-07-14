"""User-controlled long-term Agent memory APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent.long_term_memory import (
    forget_all_long_term_memories,
    forget_long_term_memory,
    list_active_memories,
)
from app.core.auth import get_current_user
from app.db.database import AsyncSessionLocal
from app.db.tenant_context import current_tenant_id

router = APIRouter()


@router.get("")
async def get_my_agent_memories(
    jwt_user: Annotated[dict, Depends(get_current_user)],
    query: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    user_id = str(jwt_user.get("user_id") or "")
    return {
        "memories": await list_active_memories(
            user_id,
            tenant_id=current_tenant_id(),
            query=query,
            limit=limit,
            session_factory=AsyncSessionLocal,
        )
    }


@router.delete("/{memory_id}")
async def delete_my_agent_memory(
    memory_id: int,
    jwt_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    deleted = await forget_long_term_memory(
        user_id=str(jwt_user.get("user_id") or ""),
        memory_id=memory_id,
        tenant_id=current_tenant_id(),
        session_factory=AsyncSessionLocal,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory was not found")
    return {"deleted": True, "memory_id": memory_id}


@router.delete("")
async def delete_all_my_agent_memories(
    jwt_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    count = await forget_all_long_term_memories(
        user_id=str(jwt_user.get("user_id") or ""),
        tenant_id=current_tenant_id(),
        session_factory=AsyncSessionLocal,
    )
    return {"deleted": count}
