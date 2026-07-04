"""Generic approval API for supervisor-based business scenarios."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agent.approval_service import authorize_generic_approval
from app.core.auth import get_current_user
from app.agent.approval_tasks import (
    complete_approval_task,
    escalate_overdue_tasks,
    serialize_approval_task,
)
from app.core.logging import get_logger
from app.core.masking import mask_dict
from app.db.database import AsyncSessionLocal
from app.db.models import ApprovalDecision, ApprovalTask, AuditLog
from app.db.tenant_context import current_tenant_id

logger = get_logger(__name__)
router = APIRouter()


class GenericApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scenario_id: str = Field(alias="scenarioId")
    approval_type: str = Field(alias="approvalType")
    request_id: str = Field(alias="requestId")
    action: Literal["approve", "reject"]
    reviewer_id: str = Field(alias="reviewerId")
    reviewer_role: str = Field(alias="reviewerRole")
    stage_id: str | None = Field(default=None, alias="stageId")
    thread_id: str | None = Field(default=None, alias="threadId")
    comment: str = ""


def _status_for_action(action: str) -> str:
    return "approved" if action == "approve" else "rejected"


def _decision_key(payload: GenericApprovalRequest) -> str:
    if payload.stage_id:
        return f"{payload.request_id}:{payload.stage_id}"
    return payload.request_id


async def _write_approval_audit(
    *,
    payload: GenericApprovalRequest,
    output: dict,
    success: bool,
) -> None:
    if not payload.thread_id:
        return
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                AuditLog(
                    thread_id=payload.thread_id,
                    trace_id=None,
                    node_name="generic_approval",
                    event_type="approval_decision",
                    input_data=mask_dict(payload.model_dump(by_alias=True)),
                    output_data=mask_dict(output),
                    success=success,
                )
            )
            await session.commit()
    except Exception as exc:
        logger.warning("generic_approval_audit_failed", error=str(exc), request_id=payload.request_id)


@router.post("/approval")
async def decide_generic_approval(
    payload: GenericApprovalRequest,
    jwt_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    # 审批人身份/角色以已验证 JWT 为准，忽略请求体里客户端可伪造的字段。
    payload.reviewer_role = str(jwt_user.get("role") or "").upper()
    payload.reviewer_id = str(jwt_user.get("user_id") or "")
    auth = authorize_generic_approval(
        scenario_id=payload.scenario_id,
        approval_type=payload.approval_type,
        action=payload.action,
        reviewer_role=payload.reviewer_role,
        stage_id=payload.stage_id,
    )
    if not auth.allowed:
        output = {
            "ok": False,
            "requestId": payload.request_id,
            "reason": auth.reason,
            "reviewRoles": list(auth.review_roles),
            "stageId": auth.stage_id,
            "stageName": auth.stage_name,
            "policyAction": auth.policy_action,
            "policy": auth.policy_event,
        }
        await _write_approval_audit(payload=payload, output=output, success=False)
        raise HTTPException(status_code=auth.status_code, detail=auth.reason)

    status = _status_for_action(payload.action)
    decision_key = _decision_key(payload)

    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(ApprovalDecision).where(ApprovalDecision.request_id == decision_key)
            )
        ).scalars().first()

        if existing:
            if existing.action != payload.action:
                output = {
                    "ok": False,
                    "requestId": payload.request_id,
                    "status": existing.status,
                    "existingAction": existing.action,
                    "requestedAction": payload.action,
                    "reason": "Approval request already has a different decision.",
                }
                await _write_approval_audit(payload=payload, output=output, success=False)
                raise HTTPException(status_code=409, detail=output["reason"])

            output = {
                "ok": True,
                "idempotent": True,
                "decisionId": existing.id,
                "requestId": payload.request_id,
                "decisionKey": existing.request_id,
                "scenarioId": existing.scenario_id,
                "approvalType": existing.approval_type,
                "stageId": existing.policy_event.get("stage_id") if existing.policy_event else payload.stage_id,
                "stageName": existing.policy_event.get("stage_name") if existing.policy_event else auth.stage_name,
                "action": existing.action,
                "status": existing.status,
                "reviewerId": existing.reviewer_id,
                "reviewerRole": existing.reviewer_role,
                "reviewRoles": list(auth.review_roles),
                "policyAction": auth.policy_action,
                "message": "审批决策已存在，本次请求按幂等结果返回。",
            }
            await _write_approval_audit(payload=payload, output=output, success=True)
            return output

        decision = ApprovalDecision(
            request_id=decision_key,
            scenario_id=payload.scenario_id,
            approval_type=payload.approval_type,
            action=payload.action,
            status=status,
            reviewer_id=payload.reviewer_id,
            reviewer_role=payload.reviewer_role.upper(),
            review_roles={"roles": list(auth.review_roles)},
            comment=payload.comment,
            thread_id=payload.thread_id,
            policy_event={
                **(auth.policy_event or {}),
                "original_request_id": payload.request_id,
                "stage_id": auth.stage_id or payload.stage_id,
                "stage_name": auth.stage_name,
            },
        )
        session.add(decision)
        await session.commit()
        await session.refresh(decision)

    output = {
        "ok": True,
        "idempotent": False,
        "decisionId": decision.id,
        "requestId": payload.request_id,
        "decisionKey": decision.request_id,
        "scenarioId": decision.scenario_id,
        "approvalType": decision.approval_type,
        "stageId": payload.stage_id,
        "stageName": auth.stage_name,
        "action": decision.action,
        "status": decision.status,
        "reviewerId": decision.reviewer_id,
        "reviewerRole": decision.reviewer_role,
        "reviewRoles": list(auth.review_roles),
        "policyAction": auth.policy_action,
        "policy": auth.policy_event,
        "message": "审批决策已记录。",
    }
    await _write_approval_audit(payload=payload, output=output, success=True)
    await complete_approval_task(
        decision_key,
        action=payload.action,
        reviewer_id=payload.reviewer_id,
        comment=payload.comment,
    )
    logger.info("generic_approval_recorded", request_id=payload.request_id, status=status)
    return output


def _serialize_decision(decision: ApprovalDecision) -> dict[str, Any]:
    policy_event = decision.policy_event or {}
    return {
        "decisionId": decision.id,
        "decisionKey": decision.request_id,
        "requestId": policy_event.get("original_request_id") or decision.request_id.split(":", 1)[0],
        "scenarioId": decision.scenario_id,
        "approvalType": decision.approval_type,
        "stageId": policy_event.get("stage_id"),
        "stageName": policy_event.get("stage_name"),
        "action": decision.action,
        "status": decision.status,
        "reviewerId": decision.reviewer_id,
        "reviewerRole": decision.reviewer_role,
        "reviewRoles": (decision.review_roles or {}).get("roles", []),
        "comment": decision.comment,
        "threadId": decision.thread_id,
        "createdAt": decision.created_at.isoformat() if decision.created_at else None,
    }


@router.get("/approval-center")
async def list_approval_center(
    jwt_user: Annotated[dict, Depends(get_current_user)],
    limit: int = 50,
    view: str = Query(default="approver", pattern="^(approver|requester|history)$"),
    status: str = "",
) -> dict[str, Any]:
    # 身份以 JWT 为准，避免通过 query 参数任意冒充角色/用户列举审批任务。
    user_id = str(jwt_user.get("user_id") or "")
    user_role = str(jwt_user.get("role") or "AGENT")
    tenant_id = current_tenant_id()
    async with AsyncSessionLocal() as session:
        task_rows = (
            await session.execute(
                select(ApprovalTask)
                .where(ApprovalTask.tenant_id == tenant_id)
                .order_by(ApprovalTask.due_at.asc())
                .limit(min(max(limit * 4, 1), 800))
            )
        ).scalars().all()
        decision_rows = (
            await session.execute(
                select(ApprovalDecision)
                .where(ApprovalDecision.tenant_id == tenant_id)
                .order_by(ApprovalDecision.created_at.desc())
                .limit(min(max(limit, 1), 200))
            )
        ).scalars().all()

    normalized_role = user_role.upper()
    tasks = task_rows
    if view == "approver":
        tasks = [task for task in tasks if normalized_role in (task.assigned_roles or [])]
        if not status:
            tasks = [task for task in tasks if task.status in {"pending", "escalated"}]
    elif view == "requester":
        tasks = [task for task in tasks if task.requester_id == user_id]
    if status:
        tasks = [task for task in tasks if task.status == status]
    tasks = tasks[: min(max(limit, 1), 200)]

    task_items = [serialize_approval_task(task) for task in tasks]
    history = [_serialize_decision(decision) for decision in decision_rows]
    items = history if view == "history" else task_items
    status_source = task_items if view != "history" else history

    return {
        "items": items,
        "tasks": task_items,
        "history": history,
        "count": len(items),
        "view": view,
        "status_counts": {
            key: sum(1 for item in status_source if item["status"] == key)
            for key in ("pending", "escalated", "approved", "rejected")
        },
    }


@router.post("/approval-center/escalate")
async def sweep_approval_sla(
    jwt_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    if str(jwt_user.get("role") or "").upper() not in {"MANAGER", "SECURITY", "FINANCE"}:
        raise HTTPException(status_code=403, detail="仅审批角色可触发 SLA 升级扫描")
    count = await escalate_overdue_tasks(tenant_id=current_tenant_id())
    return {"ok": True, "escalated": count}
