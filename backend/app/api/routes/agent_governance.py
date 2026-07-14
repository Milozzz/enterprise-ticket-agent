"""Agent identity, feedback-loop and counterfactual governance APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.counterfactual_replay import (
    materialize_counterfactual_variants,
    persist_counterfactual_experiment,
    reconstruct_counterfactual_snapshot,
    run_counterfactual_replay,
    serialize_experiment,
)
from app.agent.delegation import create_persisted_delegation, serialize_delegation
from app.agent.online_eval import (
    online_eval_report,
    promote_online_eval_case,
    record_agent_feedback,
    serialize_eval_case,
    serialize_feedback,
)
from app.agent.tool_gateway import TOOL_SPECS
from app.agent.plan_graph import build_plan_graph
from app.api.routes.admin_config import require_admin_api_key
from app.core.auth import get_current_user
from app.db.database import get_db
from app.db.models import AgentDelegationGrant, CounterfactualExperiment
from app.db.tenant_context import current_tenant_id


router = APIRouter()


class DelegationIssuePayload(BaseModel):
    principal_id: str | None = Field(default=None, max_length=120)
    principal_role: str | None = Field(default=None, max_length=40)
    agent_id: str = Field(default="enterprise-ticket-agent", max_length=120)
    allowed_tools: list[str] = Field(min_length=1, max_length=30)
    resource_scopes: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    purpose: str = Field(min_length=3, max_length=500)
    approval_id: str | None = Field(default=None, max_length=140)
    expires_at: datetime | None = None
    max_uses: int = Field(default=1, ge=1, le=100)


class FeedbackPayload(BaseModel):
    thread_id: str = Field(min_length=1, max_length=120)
    trace_id: str | None = Field(default=None, max_length=120)
    task_id: str | None = Field(default=None, max_length=140)
    scenario_id: str = Field(default="refund", max_length=100)
    disposition: Literal["accept", "correct", "reject"]
    rating: int | None = Field(default=None, ge=1, le=5)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    correction: dict[str, Any] | None = None
    task_snapshot: dict[str, Any] = Field(default_factory=dict)
    version_context: dict[str, Any] = Field(default_factory=dict)


class EvalPromotionPayload(BaseModel):
    status: Literal["approved", "rejected"] = "approved"
    dataset_version: str = Field(default="production-feedback-v1", max_length=80)
    reviewed_by: str = Field(default="admin", max_length=120)


class CounterfactualPayload(BaseModel):
    thread_id: str | None = Field(default=None, max_length=120)
    task_id: str | None = Field(default=None, max_length=140)
    requested_by: str = Field(default="simulation-lab", max_length=120)
    baseline_snapshot: dict[str, Any] | None = None
    scenario_id: str = Field(default="refund", max_length=100)
    variants: list[dict[str, Any]] = Field(min_length=1, max_length=8)


@router.post("/delegations")
async def issue_delegation(
    payload: DelegationIssuePayload,
    current_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    issuer_id = str(current_user.get("user_id") or "")
    issuer_role = str(current_user.get("role") or "USER").upper()
    principal_id = str(payload.principal_id or issuer_id)
    principal_role = str(
        payload.principal_role
        or (issuer_role if principal_id == issuer_id else "USER")
    ).upper()
    if principal_id != issuer_id and issuer_role not in {"MANAGER", "SECURITY", "ADMIN"}:
        raise HTTPException(status_code=403, detail="Only a manager or security role may delegate to another principal.")
    unknown = sorted(set(payload.allowed_tools) - set(TOOL_SPECS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown Tool Gateway tools: {unknown}")
    tenant_id = str(current_user.get("tenant_id") or current_tenant_id())
    try:
        grant, token = await create_persisted_delegation(
            session,
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_role=principal_role,
            issued_by=issuer_id,
            agent_id=payload.agent_id,
            allowed_tools=payload.allowed_tools,
            resource_scopes=payload.resource_scopes,
            constraints=payload.constraints,
            purpose=payload.purpose,
            approval_id=payload.approval_id,
            expires_at=payload.expires_at,
            max_uses=payload.max_uses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"delegation": serialize_delegation(grant), "delegation_token": token, "token_type": "Agent-Delegation"}


@router.get("/delegations")
async def list_delegations(
    current_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(default=None, max_length=30),
):
    tenant_id = str(current_user.get("tenant_id") or current_tenant_id())
    role = str(current_user.get("role") or "USER").upper()
    statement = select(AgentDelegationGrant).where(AgentDelegationGrant.tenant_id == tenant_id)
    if role not in {"MANAGER", "SECURITY", "ADMIN"}:
        statement = statement.where(AgentDelegationGrant.principal_id == str(current_user.get("user_id")))
    if status:
        statement = statement.where(AgentDelegationGrant.status == status)
    grants = list((await session.execute(statement.order_by(AgentDelegationGrant.created_at.desc()))).scalars())
    return {"delegations": [serialize_delegation(item) for item in grants]}


@router.post("/delegations/{grant_id}/revoke")
async def revoke_delegation(
    grant_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = str(current_user.get("tenant_id") or current_tenant_id())
    grant = await session.scalar(
        select(AgentDelegationGrant).where(
            AgentDelegationGrant.grant_id == grant_id,
            AgentDelegationGrant.tenant_id == tenant_id,
        )
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="Delegation grant not found.")
    user_id = str(current_user.get("user_id") or "")
    role = str(current_user.get("role") or "USER").upper()
    if user_id not in {grant.principal_id, grant.issued_by} and role not in {"MANAGER", "SECURITY", "ADMIN"}:
        raise HTTPException(status_code=403, detail="This principal cannot revoke the delegation.")
    grant.status = "revoked"
    grant.revoked_at = datetime.utcnow()
    await session.flush()
    return {"delegation": serialize_delegation(grant)}


@router.post("/feedback")
async def submit_feedback(
    payload: FeedbackPayload,
    current_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    feedback, eval_case = await record_agent_feedback(
        session,
        tenant_id=str(current_user.get("tenant_id") or current_tenant_id()),
        thread_id=payload.thread_id,
        trace_id=payload.trace_id,
        task_id=payload.task_id,
        scenario_id=payload.scenario_id,
        submitted_by=str(current_user.get("user_id") or "unknown"),
        disposition=payload.disposition,
        rating=payload.rating,
        reason_codes=payload.reason_codes,
        correction=payload.correction,
        task_snapshot=payload.task_snapshot,
        version_context=payload.version_context,
    )
    return {
        "feedback": serialize_feedback(feedback),
        "eval_case": serialize_eval_case(eval_case) if eval_case else None,
    }


@router.get("/online-eval/report", dependencies=[Depends(require_admin_api_key)])
async def get_online_eval_report(
    session: Annotated[AsyncSession, Depends(get_db)],
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
):
    return await online_eval_report(session, tenant_id=current_tenant_id(), window_hours=window_hours)


@router.post("/online-eval/cases/{case_id}/review", dependencies=[Depends(require_admin_api_key)])
async def review_online_eval_case(
    case_id: str,
    payload: EvalPromotionPayload,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        case = await promote_online_eval_case(
            session,
            tenant_id=current_tenant_id(),
            case_id=case_id,
            reviewed_by=payload.reviewed_by,
            dataset_version=payload.dataset_version,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"eval_case": serialize_eval_case(case)}


@router.post("/counterfactual/replay", dependencies=[Depends(require_admin_api_key)])
async def counterfactual_replay(
    payload: CounterfactualPayload,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        if payload.baseline_snapshot is not None:
            snapshot = payload.baseline_snapshot
        elif payload.thread_id:
            snapshot = await reconstruct_counterfactual_snapshot(
                session,
                tenant_id=current_tenant_id(),
                thread_id=str(payload.thread_id),
            )
        else:
            task_spec = {
                "task_id": "simulation-lab-task",
                "scenario_id": payload.scenario_id,
                "budget": {"max_steps": 20, "max_replans": 1},
                "success_criteria": [],
            }
            snapshot = {
                "source": "simulation_lab_synthetic_baseline",
                "task_spec": task_spec,
                "plan_graph": build_plan_graph(task_spec),
                "evidence_graph": {"claims": [{"evidence_id": "simulated-evidence"}]},
                "verification_result": {"status": "pass"},
                "order_amount": 299,
                "currency": "CNY",
                "risk_score": 10,
                "risk_level": "low",
                "user_history": {},
                "permission_system": "SAP",
                "permission_level": "read",
                "reimbursement_amount": 299,
                "reimbursement_category": "travel",
            }
        materialized_variants = await materialize_counterfactual_variants(
            baseline_snapshot=snapshot,
            variants=payload.variants,
            tenant_id=current_tenant_id(),
            thread_id=payload.thread_id,
        )
        result = run_counterfactual_replay(
            baseline_snapshot=snapshot,
            variants=materialized_variants,
        )
        experiment = await persist_counterfactual_experiment(
            session,
            tenant_id=current_tenant_id(),
            thread_id=payload.thread_id,
            task_id=payload.task_id or str((snapshot.get("task_spec") or {}).get("task_id") or "") or None,
            requested_by=payload.requested_by,
            baseline_snapshot=snapshot,
            variants=materialized_variants,
            result=result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return serialize_experiment(experiment)


@router.get("/counterfactual/{experiment_id}", dependencies=[Depends(require_admin_api_key)])
async def get_counterfactual_experiment(
    experiment_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    experiment = await session.scalar(
        select(CounterfactualExperiment).where(
            CounterfactualExperiment.experiment_id == experiment_id,
            CounterfactualExperiment.tenant_id == current_tenant_id(),
        )
    )
    if experiment is None:
        raise HTTPException(status_code=404, detail="Counterfactual experiment not found.")
    return serialize_experiment(experiment)
