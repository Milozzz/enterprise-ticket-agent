"""Generic approval API for supervisor-based business scenarios."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agent.approval_service import authorize_generic_approval
from app.core.logging import get_logger
from app.core.masking import mask_dict
from app.db.database import AsyncSessionLocal
from app.db.models import ApprovalDecision, AuditLog

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
async def decide_generic_approval(payload: GenericApprovalRequest) -> dict:
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
    logger.info("generic_approval_recorded", request_id=payload.request_id, status=status)
    return output


@router.get("/approval-center")
async def list_approval_center(limit: int = 50) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(ApprovalDecision).order_by(ApprovalDecision.created_at.desc()).limit(min(max(limit, 1), 200))
            )
        ).scalars().all()

    items = []
    for decision in rows:
        policy_event = decision.policy_event or {}
        items.append(
            {
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
        )

    return {
        "items": items,
        "count": len(items),
        "status_counts": {
            "approved": sum(1 for item in items if item["status"] == "approved"),
            "rejected": sum(1 for item in items if item["status"] == "rejected"),
        },
    }
