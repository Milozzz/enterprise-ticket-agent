from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.agent.agent_jobs import cancel_agent_job, enqueue_agent_job, serialize_agent_job
from app.core.auth import get_optional_user
from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import AgentExecutionJob
from app.db.tenant_context import current_tenant_id, tenant_scope

router = APIRouter()


class AgentJobRequest(BaseModel):
    messages: list[dict]
    thread_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    user_role: str | None = None
    priority: int = Field(default=50, ge=0, le=100)


def _identity(user: dict | None, payload: AgentJobRequest) -> tuple[str, str, str]:
    if user:
        return str(user["user_id"]), str(user["role"]), str(user.get("tenant_id") or current_tenant_id())
    if get_settings().environment != "development" and os.environ.get("TESTING") != "1":
        raise HTTPException(status_code=401, detail="Authentication is required")
    return payload.user_id or "anonymous", payload.user_role or "USER", current_tenant_id()


def _read_identity(user: dict | None) -> tuple[str, str]:
    if user:
        return str(user.get("user_id") or ""), str(user.get("role") or "USER").upper()
    if get_settings().environment != "development" and os.environ.get("TESTING") != "1":
        raise HTTPException(status_code=401, detail="Authentication is required")
    return "anonymous", "USER"


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_agent_job(
    payload: AgentJobRequest,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    if not payload.messages:
        raise HTTPException(status_code=422, detail="messages must not be empty")
    user_id, role, tenant_id = _identity(user, payload)
    data = payload.model_dump(exclude={"priority"}, exclude_none=True)
    data["user_id"] = user_id
    data["user_role"] = role
    job, idempotent = await enqueue_agent_job(
        data,
        tenant_id=tenant_id,
        requester_id=user_id,
        requester_role=role,
        idempotency_key=idempotency_key,
        priority=payload.priority,
    )
    return {**serialize_agent_job(job), "idempotent": idempotent}


@router.get("/{job_id}")
async def get_agent_job(
    job_id: str,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
) -> dict:
    user_id, role = _read_identity(user)
    tenant_id = current_tenant_id()
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            job = await session.get(AgentExecutionJob, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Agent job not found")
    if role not in {"AGENT", "MANAGER"} and job.requester_id != user_id:
        raise HTTPException(status_code=403, detail="Agent job is owned by another requester")
    return serialize_agent_job(job)


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
) -> dict:
    user_id, role = _read_identity(user)
    tenant_id = current_tenant_id()
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            job = await session.get(AgentExecutionJob, job_id)
    if job is None or (role not in {"AGENT", "MANAGER"} and job.requester_id != user_id):
        raise HTTPException(status_code=403, detail="Agent job cannot be cancelled by this requester")
    cancelled = await cancel_agent_job(job_id, tenant_id=tenant_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")
    return {"job_id": job_id, "status": "cancelled"}
