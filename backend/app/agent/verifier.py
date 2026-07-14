"""Independent deterministic/optional-LLM verifier for high-risk execution."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field

from app.agent.dependencies import get_agent_dependencies
from app.agent.evidence_graph import (
    collect_refund_evidence,
    detect_evidence_conflicts,
    evidence_ids_for_predicates,
    evidence_predicates,
    fresh_evidence_predicates,
)
from app.agent.evidence_store import persist_evidence_bundle
from app.agent.approval_tasks import ensure_approval_task, serialize_approval_task
from app.agent.information_flow import declassify_for_verified_execution
from app.agent.plan_graph import mark_plan_steps, replan_plan_graph, validate_plan_graph
from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.gateway import LLMCallContext

logger = get_logger(__name__)


class VerificationStatus(str, Enum):
    PASS = "pass"
    REPLAN = "replan"
    BLOCK = "block"


class VerificationIssue(BaseModel):
    code: str
    message: str
    recoverable: bool = False
    missing_evidence: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    status: VerificationStatus
    issues: list[VerificationIssue] = Field(default_factory=list)
    checked_predicates: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    critic_used: bool = False
    critic_summary: str = ""


class CriticVerdict(BaseModel):
    supported: bool = True
    missing_evidence: list[str] = Field(default_factory=list)
    unsafe_assumptions: list[str] = Field(default_factory=list)
    summary: str = ""


async def execution_verifier_node(state: AgentState) -> dict[str, Any]:
    evidence_graph = collect_refund_evidence(state)
    result = verify_refund_execution(state, evidence_graph)
    if result.status == VerificationStatus.PASS and get_settings().agent_verifier_llm_enabled:
        critic = await _run_critic(state, evidence_graph)
        if critic is not None:
            result.critic_used = True
            result.critic_summary = critic.summary
            if not critic.supported or critic.missing_evidence or critic.unsafe_assumptions:
                result.status = VerificationStatus.BLOCK
                result.issues.append(
                    VerificationIssue(
                        code="critic.unsupported_execution",
                        message="; ".join(critic.unsafe_assumptions) or "LLM critic found unsupported execution assumptions.",
                        missing_evidence=critic.missing_evidence,
                    )
                )

    current_plan = get_state_val(state, "plan_graph", {}) or {}
    if result.status == VerificationStatus.REPLAN:
        missing = {
            predicate
            for issue in result.issues
            for predicate in issue.missing_evidence
        }
        failed_step = (
            "load_order"
            if any(item.startswith("order.") for item in missing)
            else "validate_return"
            if any(item.startswith("return.") for item in missing)
            else "inspect_inventory"
        )
        try:
            plan_graph = replan_plan_graph(
                current_plan,
                failed_step_ids=[failed_step],
                reason_codes=[issue.code for issue in result.issues],
            )
            if get_settings().agent_dynamic_plan_enabled:
                from app.agent.dynamic_planner import propose_validated_plan

                replanned, method = await propose_validated_plan(
                    task_spec=get_state_val(state, "task_spec", {}) or {},
                    base_plan=plan_graph,
                    precedents=get_state_val(state, "plan_precedents", []) or [],
                )
                replanned["replan_count"] = plan_graph["replan_count"]
                replanned["replan_reason_codes"] = plan_graph.get(
                    "replan_reason_codes", []
                )
                replanned["generation_source"] = f"bounded_replan:{method}"
                plan_graph = replanned
        except ValueError:
            result.status = VerificationStatus.BLOCK
            result.issues.append(
                VerificationIssue(
                    code="REPLAN_BUDGET_EXHAUSTED",
                    message="受控重规划预算已耗尽，需要人工接管。",
                )
            )
            plan_graph = {**dict(current_plan), "status": "blocked"}
    else:
        plan_status = "running" if result.status == VerificationStatus.PASS else "blocked"
        plan_graph = mark_plan_steps(
            current_plan,
            {
                "understand": "completed",
                "load_order": "completed" if "order.identity" in result.checked_predicates else "blocked",
                "validate_return": "completed" if "return.received" in result.checked_predicates else "blocked",
                "inspect_inventory": "completed" if "inventory.state" in result.checked_predicates else "blocked",
                "assess_risk": "completed" if "risk.score" in result.checked_predicates else "blocked",
                "verify": "completed" if result.status == VerificationStatus.PASS else "blocked",
                "approve": (
                    "completed"
                    if get_state_val(state, "human_decision", "") == "approve"
                    else "waiting_approval"
                    if get_state_val(state, "requires_human_approval", False)
                    else "planned"
                ),
            },
            graph_status=plan_status,
        )
    replan_count = int(plan_graph.get("replan_count") or 0)
    verification_payload = result.model_dump(mode="json")
    try:
        persistence = await persist_evidence_bundle(
            evidence_graph=evidence_graph,
            task_spec=get_state_val(state, "task_spec", {}) or {},
            verification_result=verification_payload,
            tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
            thread_id=str(get_state_val(state, "thread_id", "") or ""),
            trace_id=str(get_state_val(state, "trace_id", "") or ""),
            decision_type="refund_pre_execution_verification",
            session_factory=get_agent_dependencies().session_factory,
        )
    except Exception as exc:
        logger.error("evidence_persistence_failed", error=str(exc))
        result.status = VerificationStatus.BLOCK
        result.issues.append(
            VerificationIssue(
                code="EVIDENCE_PERSISTENCE_FAILED",
                message="执行证据未能持久化，已阻止后续写操作。",
            )
        )
        verification_payload = result.model_dump(mode="json")
        plan_graph = {**dict(plan_graph), "status": "blocked"}
        persistence = {"error": str(exc)}
    fallback_task = None
    if result.status == VerificationStatus.BLOCK:
        try:
            task_spec = get_state_val(state, "task_spec", {}) or {}
            task_id = str(task_spec.get("task_id") or get_state_val(state, "thread_id", "unknown"))
            fallback = await ensure_approval_task(
                task_key=f"agent-fallback:{task_id}:{plan_graph.get('revision', 1)}",
                request_id=task_id,
                scenario_id=str(task_spec.get("scenario_id") or "refund"),
                approval_type="agent_exception_review",
                stage_id="verifier_fallback",
                stage_name="Agent 异常人工接管",
                requester_id=str(task_spec.get("requester_id") or "unknown"),
                requester_role=str(get_state_val(state, "user_role", "USER") or "USER"),
                assigned_roles=["MANAGER"],
                thread_id=str(get_state_val(state, "thread_id", "") or ""),
                business_payload={
                    "taskSpec": task_spec,
                    "verification": verification_payload,
                    "planRevision": plan_graph.get("revision"),
                    "evidenceIds": result.cited_evidence_ids,
                },
                sla_minutes=30,
                priority="high",
                tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
                session_factory=get_agent_dependencies().session_factory,
            )
            fallback_task = serialize_approval_task(fallback)
        except Exception as exc:
            logger.error("verifier_fallback_task_failed", error=str(exc))
    data_provenance = dict(get_state_val(state, "data_provenance", {}) or {})
    information_flow_events: list[dict[str, Any]] = []
    if result.status == VerificationStatus.PASS:
        verified_fields = {
            "order_id": get_state_val(state, "order_id"),
            "ticket_id": get_state_val(state, "ticket_id"),
            "amount": get_state_val(state, "order_amount"),
            "currency": get_state_val(state, "currency"),
            "refund_request_id": get_state_val(state, "refund_request_id"),
            "open_item_id": get_state_val(state, "open_item_id"),
            "rma_id": get_state_val(state, "return_authorization_id"),
            "requester_id": get_state_val(state, "user_id"),
        }
        declassified = declassify_for_verified_execution(
            fields=verified_fields,
            cited_evidence_ids=result.cited_evidence_ids,
            verifier="refund_execution_verifier",
            human_approved=get_state_val(state, "human_decision", "") == "approve",
        )
        data_provenance.update(declassified)
        information_flow_events.append(
            {
                "event": "verified_declassification",
                "fields": sorted(declassified),
                "evidence_ids": list(result.cited_evidence_ids),
                "human_approved": get_state_val(state, "human_decision", "") == "approve",
            }
        )
    return {
        "evidence_graph": evidence_graph,
        "evidence_persistence": persistence,
        "verification_result": verification_payload,
        "human_fallback_task": fallback_task,
        "plan_graph": plan_graph,
        "replan_count": replan_count,
        "data_provenance": data_provenance,
        "information_flow_events": information_flow_events,
        "current_step": (
            "verification_passed"
            if result.status == VerificationStatus.PASS
            else "verification_replan"
            if result.status == VerificationStatus.REPLAN
            else "verification_blocked"
        ),
        "error_message": (
            "执行前验证未通过：" + "；".join(issue.message for issue in result.issues)
            if result.status == VerificationStatus.BLOCK
            else ""
        ),
        "ui_events": [
            {
                "type": "thinking_stream",
                "data": {
                    "steps": [
                        {
                            "step": "verify_evidence",
                            "label": "执行前证据验证",
                            "status": (
                                "done"
                                if result.status == VerificationStatus.PASS
                                else "running"
                                if result.status == VerificationStatus.REPLAN
                                else "error"
                            ),
                            "detail": (
                                f"验证通过，共 {len(evidence_graph.get('claims', []))} 条可追溯证据"
                                if result.status == VerificationStatus.PASS
                                else "证据不完整，正在执行一次受限重规划"
                                if result.status == VerificationStatus.REPLAN
                                else "；".join(issue.message for issue in result.issues)
                            ),
                        }
                    ]
                },
            }
        ],
    }


def verify_refund_execution(
    state: Mapping[str, Any],
    evidence_graph: Mapping[str, Any],
) -> VerificationResult:
    predicates = fresh_evidence_predicates(evidence_graph)
    required = {
        "order.identity",
        "order.amount",
        "order.currency",
        "return.authorization",
        "return.received",
        "return.inspection",
        "inventory.state",
        "risk.score",
        "policy.decision",
    }
    issues: list[VerificationIssue] = []
    conflicts = detect_evidence_conflicts(evidence_graph)
    if conflicts:
        issues.append(
            VerificationIssue(
                code="TOOL_RESULT_INCONSISTENT",
                message="检测到同一业务实体存在相互冲突的新鲜证据。",
                missing_evidence=[
                    evidence_id
                    for conflict in conflicts
                    for evidence_id in conflict["evidence_ids"]
                ],
            )
        )
    missing = sorted(required - predicates)
    max_replans = int((get_state_val(state, "plan_graph", {}) or {}).get("max_replans", 1))
    replan_count = int(get_state_val(state, "replan_count", 0) or 0)
    if missing:
        recoverable = bool(
            {
                "order.identity",
                "order.amount",
                "order.currency",
                "return.authorization",
                "return.received",
                "return.inspection",
                "inventory.state",
            }
            & set(missing)
        ) and replan_count < max_replans
        issues.append(
            VerificationIssue(
                code="EVIDENCE_MISSING",
                message=f"缺少执行证据：{', '.join(missing)}",
                recoverable=recoverable,
                missing_evidence=missing,
            )
        )
    amount = get_state_val(state, "order_amount")
    try:
        amount_valid = amount is not None and float(amount) > 0
    except (TypeError, ValueError):
        amount_valid = False
    if not amount_valid:
        issues.append(VerificationIssue(code="TOOL_RESULT_INCONSISTENT", message="退款金额必须大于 0。"))
    currency = str(get_state_val(state, "currency", "") or "").upper()
    if len(currency) != 3:
        issues.append(VerificationIssue(code="TOOL_RESULT_INCONSISTENT", message="币种必须为三位代码。"))
    decision = str(get_state_val(state, "human_decision", "") or "")
    if decision and decision != "approve":
        issues.append(
            VerificationIssue(
                code="POLICY_CONFLICT",
                message="退款审批已被拒绝，禁止进入写操作。",
            )
        )
    if not issues:
        status = VerificationStatus.PASS
    elif any(issue.recoverable for issue in issues) and all(issue.recoverable for issue in issues):
        status = VerificationStatus.REPLAN
    else:
        status = VerificationStatus.BLOCK
    return VerificationResult(
        status=status,
        issues=issues,
        checked_predicates=sorted(predicates),
        cited_evidence_ids=evidence_ids_for_predicates(evidence_graph, predicates),
    )


def verify_generic_preflight(
    *,
    scenario_id: str,
    task_spec: Mapping[str, Any],
    plan_graph: Mapping[str, Any],
    evidence_graph: Mapping[str, Any],
) -> VerificationResult:
    issues: list[VerificationIssue] = []
    if not task_spec or task_spec.get("scenario_id") != scenario_id:
        issues.append(
            VerificationIssue(
                code="task_spec.invalid",
                message="TaskSpec is missing or belongs to another scenario.",
            )
        )
    plan_errors = validate_plan_graph(plan_graph)
    if plan_errors:
        issues.append(
            VerificationIssue(
                code="plan.invalid",
                message="; ".join(plan_errors),
            )
        )
    required_by_scenario = {
        "permission_request": {"request.system", "request.permission_level", "policy.decision"},
        "reimbursement": {"request.amount", "request.category", "policy.decision"},
    }
    predicates = evidence_predicates(evidence_graph)
    missing = sorted(required_by_scenario.get(scenario_id, set()) - predicates)
    if missing:
        issues.append(
            VerificationIssue(
                code="evidence.missing",
                message=f"Missing request evidence: {', '.join(missing)}",
                missing_evidence=missing,
            )
        )
    return VerificationResult(
        status=VerificationStatus.BLOCK if issues else VerificationStatus.PASS,
        issues=issues,
        checked_predicates=sorted(predicates),
    )


def verify_generic_post_execution(
    *,
    scenario_id: str,
    task_spec: Mapping[str, Any],
    plan_graph: Mapping[str, Any],
    evidence_graph: Mapping[str, Any],
    request: Mapping[str, Any],
) -> VerificationResult:
    preflight = verify_generic_preflight(
        scenario_id=scenario_id,
        task_spec=task_spec,
        plan_graph=plan_graph,
        evidence_graph=evidence_graph,
    )
    issues = list(preflight.issues)
    predicates = evidence_predicates(evidence_graph)
    if not request.get("requestId") or "request.id" not in predicates:
        issues.append(
            VerificationIssue(
                code="request.persistence_evidence_missing",
                message="Tool execution did not produce a traceable request identifier.",
                missing_evidence=["request.id"],
            )
        )
    return VerificationResult(
        status=VerificationStatus.BLOCK if issues else VerificationStatus.PASS,
        issues=issues,
        checked_predicates=sorted(predicates),
    )


def verify_generic_completion(
    *,
    scenario_id: str,
    task_spec: Mapping[str, Any],
    plan_graph: Mapping[str, Any],
    evidence_graph: Mapping[str, Any],
    persisted: bool,
) -> VerificationResult:
    issues: list[VerificationIssue] = []
    predicates = evidence_predicates(evidence_graph)
    required = {"request.id", "policy.decision", "approval.decision"}
    missing = sorted(required - predicates)
    if missing:
        issues.append(
            VerificationIssue(
                code="completion.evidence_missing",
                message=f"Completion evidence is missing: {', '.join(missing)}",
                missing_evidence=missing,
            )
        )
    if not persisted:
        issues.append(
            VerificationIssue(
                code="completion.persistence_unverified",
                message=f"{scenario_id} final state was not read back from the canonical store.",
            )
        )
    return VerificationResult(
        status=VerificationStatus.BLOCK if issues else VerificationStatus.PASS,
        issues=issues,
        checked_predicates=sorted(predicates),
    )


def route_after_verification(state: AgentState) -> str:
    status = str((get_state_val(state, "verification_result", {}) or {}).get("status") or "block")
    if status == VerificationStatus.PASS.value:
        return "execute_refund"
    if status == VerificationStatus.REPLAN.value:
        return "lookup_order"
    return "summarize_session"


async def _run_critic(
    state: Mapping[str, Any], evidence_graph: Mapping[str, Any]
) -> CriticVerdict | None:
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        response = await get_agent_dependencies().llm.runnable(
            "execution_critic",
            context=LLMCallContext(
                thread_id=str(get_state_val(state, "thread_id", "unknown")),
                trace_id=str(get_state_val(state, "trace_id", "") or "") or None,
                tenant_id=str(get_state_val(state, "tenant_id", "") or "") or None,
            ),
            schema=CriticVerdict,
            temperature=0.0,
            timeout_seconds=5.0,
        ).ainvoke(
            [
                SystemMessage(content="你是独立执行审查器。只能根据给定证据判断计划是否有支持，不得补充事实。"),
                HumanMessage(content=str({"plan": get_state_val(state, "plan_graph", {}), "evidence": evidence_graph})),
            ]
        )
        if isinstance(response, CriticVerdict):
            return response
        if isinstance(response, dict):
            return CriticVerdict(**response)
    except Exception as exc:
        logger.warning("execution_critic_failed", error=str(exc))
    return None
