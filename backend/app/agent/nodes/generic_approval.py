"""Dynamic HITL and canonical persistence for configured business scenarios."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal

from langgraph.types import interrupt
from sqlalchemy import select

from app.agent.approval_service import authorize_generic_approval
from app.agent.approval_tasks import complete_approval_task, ensure_approval_task
from app.agent.dependencies import resolve_session_factory
from app.agent.scenario_registry import ApprovalStageConfig, ScenarioConfig, get_default_registry
from app.agent.state import AgentState
from app.agent.plan_graph import mark_plan_steps
from app.agent.procedural_memory import store_successful_plan
from app.agent.evidence_graph import add_approval_evidence
from app.agent.verifier import verify_generic_completion
from app.agent.utils import get_state_val
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import (
    AccessRequestRecord,
    ApprovalDecision,
    CostCenter,
    ErpBusinessRequestStatus,
    ReimbursementClaim,
)
from app.db.tenant_context import tenant_scope

logger = get_logger(__name__)


def _required_stages(scenario: ScenarioConfig) -> list[ApprovalStageConfig]:
    stages = [stage for stage in scenario.hitl.approval_chain if stage.required]
    if not stages and scenario.hitl.enabled:
        stages.append(
            ApprovalStageConfig(
                id="human_review",
                name="Human review",
                roles=scenario.hitl.review_roles,
                required=True,
            )
        )
    return stages


def route_after_generic_prepare(state: AgentState) -> str:
    return "review" if get_state_val(state, "approval_required", False) else "finalize"


def route_after_generic_review(state: AgentState) -> str:
    if get_state_val(state, "current_step") == "generic_approval_permission_denied":
        return "end"
    if get_state_val(state, "human_decision") == "reject":
        return "finalize"

    scenario = get_default_registry().get(str(get_state_val(state, "scenario_id", "refund")))
    required_stages = _required_stages(scenario)
    stage_index = int(get_state_val(state, "approval_stage_index", 0) or 0)
    return "review" if stage_index < len(required_stages) else "finalize"


async def generic_human_review_node(state: AgentState) -> dict:
    scenario_id = str(get_state_val(state, "scenario_id", ""))
    scenario = get_default_registry().get(scenario_id)
    request = dict(get_state_val(state, "business_request", {}) or {})
    request_id = str(request.get("requestId") or "")
    required_stages = _required_stages(scenario)
    stage_index = int(get_state_val(state, "approval_stage_index", 0) or 0)

    if stage_index >= len(required_stages):
        return {"human_decision": "approve", "current_step": "generic_approval_complete"}

    stage = required_stages[stage_index]
    tenant_id = str(get_state_val(state, "tenant_id", "default") or "default")
    task_key = f"{request_id}:{stage.id}"
    await ensure_approval_task(
        task_key=task_key,
        request_id=request_id,
        scenario_id=scenario.id,
        approval_type=str(get_state_val(state, "approval_type", scenario.id)),
        stage_id=stage.id,
        stage_name=stage.name,
        requester_id=str(request.get("requesterId") or get_state_val(state, "user_id", "unknown")),
        requester_role=str(get_state_val(state, "user_role", "USER")),
        assigned_roles=list(stage.roles),
        thread_id=str(get_state_val(state, "thread_id", "") or ""),
        business_payload=request,
        sla_minutes=scenario.sla_minutes,
        priority="high" if scenario.id == "permission_request" else "normal",
        tenant_id=tenant_id,
        session_factory=resolve_session_factory(AsyncSessionLocal),
    )
    resume_value = interrupt(
        {
            "kind": "approval",
            "scenario_id": scenario.id,
            "approval_type": str(get_state_val(state, "approval_type", scenario.id)),
            "request_id": request_id,
            "thread_id": get_state_val(state, "thread_id"),
            "stage_id": stage.id,
            "stage_name": stage.name,
            "stage_index": stage_index,
            "allowed_roles": list(stage.roles),
            "business_request": request,
        }
    )
    if not isinstance(resume_value, Mapping):
        resume_value = {"action": str(resume_value)}

    action = str(resume_value.get("action") or "").lower()
    reviewer_id = str(resume_value.get("reviewer_id") or "unknown")
    reviewer_role = str(resume_value.get("reviewer_role") or "USER").upper()
    comment = str(resume_value.get("comment") or "")
    approval_type = str(get_state_val(state, "approval_type", scenario.id))
    authorization = authorize_generic_approval(
        scenario_id=scenario.id,
        approval_type=approval_type,
        action=action,
        reviewer_role=reviewer_role,
        stage_id=stage.id,
    )
    if not authorization.allowed:
        return {
            "error_message": authorization.reason,
            "current_step": "generic_approval_permission_denied",
            "is_completed": True,
        }

    decision_key = f"{request_id}:{stage.id}"
    with tenant_scope(tenant_id):
        async with resolve_session_factory(AsyncSessionLocal)() as session:
            existing = await session.scalar(
                select(ApprovalDecision).where(ApprovalDecision.request_id == decision_key)
            )
            if existing is None:
                session.add(
                    ApprovalDecision(
                        tenant_id=tenant_id,
                        request_id=decision_key,
                        scenario_id=scenario.id,
                        approval_type=approval_type,
                        action=action,
                        status="approved" if action == "approve" else "rejected",
                        reviewer_id=reviewer_id,
                        reviewer_role=reviewer_role,
                        review_roles={"roles": list(stage.roles)},
                        comment=comment,
                        thread_id=str(get_state_val(state, "thread_id", "") or ""),
                        policy_event={
                            **(authorization.policy_event or {}),
                            "stage_id": stage.id,
                            "stage_name": stage.name,
                        },
                    )
                )
                await session.commit()
            elif existing.action != action:
                return {
                    "error_message": "Approval stage already has a different decision.",
                    "current_step": "generic_approval_permission_denied",
                    "is_completed": True,
                }

    await complete_approval_task(
        task_key,
        action=action,
        reviewer_id=reviewer_id,
        comment=comment,
        tenant_id=tenant_id,
        session_factory=resolve_session_factory(AsyncSessionLocal),
    )

    history_item = {
        "stage_id": stage.id,
        "stage_name": stage.name,
        "action": action,
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role,
        "comment": comment,
    }
    next_index = stage_index + 1 if action == "approve" else stage_index
    return {
        "human_decision": action,
        "reviewer_id": reviewer_id,
        "review_comment": comment,
        "approval_id": decision_key,
        "approval_stage_index": next_index,
        "approval_history": [history_item],
        "current_step": (
            "generic_approval_rejected"
            if action == "reject"
            else "generic_approval_stage_completed"
        ),
        "ui_events": [
            {
                "type": "thinking_stream",
                "data": {
                    "steps": [
                        {
                            "step": stage.id,
                            "label": stage.name,
                            "status": "done",
                            "detail": f"{reviewer_role} {action} by {reviewer_id}",
                        }
                    ]
                },
            }
        ],
    }


async def finalize_business_request_node(state: AgentState) -> dict:
    scenario_id = str(get_state_val(state, "scenario_id", ""))
    request = dict(get_state_val(state, "business_request", {}) or {})
    request_id = str(request.get("requestId") or "")
    tenant_id = str(get_state_val(state, "tenant_id", "default") or "default")
    rejected = get_state_val(state, "human_decision") == "reject"
    status = ErpBusinessRequestStatus.REJECTED if rejected else ErpBusinessRequestStatus.APPROVED
    policy_events = list(get_state_val(state, "policy_events", []) or [])
    policy_snapshot = policy_events[-1] if policy_events else None
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    persisted = False
    with tenant_scope(tenant_id):
        async with resolve_session_factory(AsyncSessionLocal)() as session:
            if scenario_id == "permission_request":
                record = await session.get(AccessRequestRecord, request_id)
                if record is None:
                    record = AccessRequestRecord(
                        tenant_id=tenant_id,
                        access_request_id=request_id,
                        requester_employee_id=str(request.get("requesterId") or "unknown"),
                        target_system=str(request.get("system") or "unknown"),
                        permission_level=str(request.get("permissionLevel") or "read"),
                        business_reason=str(request.get("reason") or ""),
                        risk_level="high" if get_state_val(state, "approval_required", False) else "low",
                        status=status,
                        granted_at=now if not rejected else None,
                        policy_snapshot=policy_snapshot,
                    )
                    session.add(record)
                else:
                    record.status = status
                    record.granted_at = now if not rejected else None
                    record.policy_snapshot = policy_snapshot
            elif scenario_id == "reimbursement":
                record = await session.get(ReimbursementClaim, request_id)
                if record is None:
                    cost_center_id = str(request.get("costCenterId") or "CC-SUPPORT")
                    cost_center = await session.get(CostCenter, cost_center_id)
                    if cost_center is None:
                        cost_center = await session.scalar(select(CostCenter).limit(1))
                    if cost_center is None:
                        raise ValueError("No canonical cost center is available for reimbursement")
                    record = ReimbursementClaim(
                        tenant_id=tenant_id,
                        reimbursement_id=request_id,
                        requester_employee_id=str(request.get("requesterId") or "unknown"),
                        cost_center_id=cost_center.cost_center_id,
                        amount=Decimal(str(request.get("amount") or "0")).quantize(Decimal("0.01")),
                        currency=str(request.get("currency") or "CNY"),
                        category=str(request.get("category") or "other"),
                        description=str(request.get("description") or ""),
                        receipt_count=int(request.get("receiptCount") or 0),
                        status=status,
                        policy_snapshot=policy_snapshot,
                    )
                    session.add(record)
                else:
                    record.status = status
                    record.policy_snapshot = policy_snapshot
            await session.commit()
            persisted = record is not None

    final_status = "rejected" if rejected else "approved"
    request["status"] = final_status
    evidence_graph = add_approval_evidence(
        get_state_val(state, "evidence_graph", {}) or {},
        subject=request_id,
        decision="reject" if rejected else "approve",
        reviewer_id=str(get_state_val(state, "reviewer_id", "") or ""),
        trace_id=str(get_state_val(state, "trace_id", "") or ""),
    )
    plan_graph = mark_plan_steps(
        get_state_val(state, "plan_graph", {}) or {},
        {"approve": "blocked" if rejected else "completed"},
        graph_status="blocked" if rejected else "completed",
    )
    completion_verification = verify_generic_completion(
        scenario_id=scenario_id,
        task_spec=get_state_val(state, "task_spec", {}) or {},
        plan_graph=plan_graph,
        evidence_graph=evidence_graph,
        persisted=persisted,
    )
    if completion_verification.status.value != "pass":
        plan_graph = {**plan_graph, "status": "blocked"}
    procedural_state = {**dict(state), "plan_graph": plan_graph}
    if not rejected and completion_verification.status.value == "pass":
        try:
            await store_successful_plan(
                procedural_state,
                session_factory=resolve_session_factory(AsyncSessionLocal),
            )
        except Exception as exc:
            logger.warning("procedural_memory_store_failed", error=str(exc))
    return {
        "business_request": request,
        "is_completed": True,
        "current_step": f"{scenario_id}_{final_status}",
        "reply_text": f"{request_id} 已{('拒绝' if rejected else '批准')}并写入企业业务数据层。",
        "plan_graph": plan_graph,
        "evidence_graph": evidence_graph,
        "verification_result": completion_verification.model_dump(mode="json"),
        "error_message": (
            "Post-execution verification failed: "
            + "; ".join(issue.message for issue in completion_verification.issues)
            if completion_verification.status.value != "pass"
            else ""
        ),
        "ui_events": [
            {
                "type": "thinking_stream",
                "data": {
                    "steps": [
                        {
                            "step": "canonical_persistence",
                            "label": "业务数据落库",
                            "status": "done",
                            "detail": f"{scenario_id} 状态已更新为 {final_status}",
                        }
                    ]
                },
            }
        ],
    }
