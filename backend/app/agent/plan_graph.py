"""Bounded plan graph and specialist capability catalog."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator


class PlanStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class ConditionSource(str, Enum):
    STATE = "state"
    EVIDENCE = "evidence"
    STEP_OUTPUT = "step_output"


class ConditionOperator(str, Enum):
    EXISTS = "exists"
    TRUTHY = "truthy"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    GTE = "gte"
    LTE = "lte"


class PlanCondition(BaseModel):
    condition_id: str
    source: ConditionSource
    path: str
    operator: ConditionOperator = ConditionOperator.EXISTS
    expected: Any = None
    failure_code: str = "PLAN_PRECONDITION_FAILED"
    description: str = ""


class CompensationPlan(BaseModel):
    strategy: str = "tool"
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    trigger_on: list[str] = Field(default_factory=lambda: ["step_failed", "postcondition_failed"])
    expected_evidence: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    step_id: str
    specialist: str
    capability: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    preconditions: list[PlanCondition] = Field(default_factory=list)
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)
    postconditions: list[PlanCondition] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    side_effect: str = "read"
    approval_required: bool = False
    compensation: CompensationPlan | None = None
    idempotency_key_template: str | None = None
    timeout_seconds: float = 10.0
    retry_limit: int = 1
    status: PlanStatus = PlanStatus.PLANNED

    @field_validator("preconditions", "postconditions", mode="before")
    @classmethod
    def normalize_conditions(cls, value):
        aliases = {
            "order_verified": {
                "source": "evidence",
                "path": "order.identity",
                "operator": "exists",
            },
            "risk_evaluated": {
                "source": "evidence",
                "path": "risk.score",
                "operator": "exists",
            },
            "verification_passed": {
                "source": "state",
                "path": "verification_result.status",
                "operator": "equals",
                "expected": "pass",
            },
        }
        normalized = []
        for index, item in enumerate(value or []):
            if isinstance(item, str):
                base = aliases.get(
                    item,
                    {"source": "state", "path": item, "operator": "truthy"},
                )
                normalized.append(
                    {
                        "condition_id": item,
                        "failure_code": "PLAN_PRECONDITION_FAILED",
                        **base,
                    }
                )
            else:
                normalized.append(item)
        return normalized

    @field_validator("compensation", mode="before")
    @classmethod
    def normalize_compensation(cls, value):
        if isinstance(value, str):
            return {"strategy": "tool", "tool": value}
        return value


class PlanGraph(BaseModel):
    task_id: str
    scenario_id: str
    revision: int = 1
    max_replans: int = 1
    replan_count: int = 0
    status: PlanStatus = PlanStatus.PLANNED
    steps: list[PlanStep]
    precedent_ids: list[int] = Field(default_factory=list)
    generation_source: str = "deterministic_base"


class SpecialistSpec(BaseModel):
    specialist_id: str
    responsibility: str
    allowed_tools: list[str]
    allowed_side_effects: list[str]
    can_authorize_writes: bool = False


SPECIALIST_CATALOG: dict[str, SpecialistSpec] = {
    "supervisor": SpecialistSpec(
        specialist_id="supervisor",
        responsibility="Understand goals and coordinate bounded plans.",
        allowed_tools=[],
        allowed_side_effects=["none"],
    ),
    "operations_specialist": SpecialistSpec(
        specialist_id="operations_specialist",
        responsibility="Read canonical order and workflow state.",
        allowed_tools=["lookup_order", "erp_get_order", "erp_query_doctype"],
        allowed_side_effects=["read"],
    ),
    "risk_specialist": SpecialistSpec(
        specialist_id="risk_specialist",
        responsibility="Evaluate risk, history, and policy precedents.",
        allowed_tools=["check_risk_level"],
        allowed_side_effects=["read", "decision"],
    ),
    "policy_specialist": SpecialistSpec(
        specialist_id="policy_specialist",
        responsibility="Retrieve grounded policy evidence and evaluate deterministic rules.",
        allowed_tools=["search_refund_policy"],
        allowed_side_effects=["read", "decision"],
    ),
    "finance_specialist": SpecialistSpec(
        specialist_id="finance_specialist",
        responsibility="Prepare and reconcile governed financial execution.",
        allowed_tools=[
            "erp_get_order",
            "erp_query_doctype",
        ],
        allowed_side_effects=["read", "decision"],
    ),
    "inventory_specialist": SpecialistSpec(
        specialist_id="inventory_specialist",
        responsibility="Validate return receipt, inspection, batch, and inventory consistency.",
        allowed_tools=["validate_return", "inspect_return_inventory"],
        allowed_side_effects=["read", "decision"],
    ),
    "access_specialist": SpecialistSpec(
        specialist_id="access_specialist",
        responsibility="Evaluate least privilege and segregation-of-duties constraints.",
        allowed_tools=[],
        allowed_side_effects=["read", "decision"],
    ),
    "expense_specialist": SpecialistSpec(
        specialist_id="expense_specialist",
        responsibility="Validate and create governed reimbursement requests.",
        allowed_tools=[],
        allowed_side_effects=["read", "decision"],
    ),
    "verifier": SpecialistSpec(
        specialist_id="verifier",
        responsibility="Validate evidence, preconditions, and success criteria independently.",
        allowed_tools=[],
        allowed_side_effects=["decision"],
    ),
    "executor": SpecialistSpec(
        specialist_id="executor",
        responsibility="Execute an already authorized deterministic write plan.",
        allowed_tools=[
            "execute_refund",
            "erp_create_credit_memo",
            "erp_clear_open_item",
            "erp_reverse_document",
            "restore_return_inventory",
            "reverse_inventory_movement",
            "create_permission_request",
            "create_reimbursement_request",
            "send_notification",
        ],
        allowed_side_effects=["write", "external"],
    ),
}


def build_plan_graph(
    task_spec: Mapping[str, Any],
    *,
    precedents: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    scenario_id = str(task_spec.get("scenario_id") or "unknown")
    builders = {
        "refund": _refund_steps,
        "permission_request": _permission_steps,
        "reimbursement": _reimbursement_steps,
    }
    deterministic_steps = builders.get(scenario_id, _default_steps)()
    steps = deterministic_steps
    precedent_ids: list[int] = []
    generation_source = "deterministic_base"
    for precedent in precedents or []:
        attributes = dict(precedent.get("attributes") or {})
        raw_steps = attributes.get("steps") or []
        try:
            candidate_steps = [PlanStep(**dict(item)) for item in raw_steps]
        except (TypeError, ValueError):
            continue
        if {item.step_id for item in candidate_steps} != {
            item.step_id for item in deterministic_steps
        }:
            continue
        candidate = PlanGraph(
            task_id=str(task_spec.get("task_id") or "unknown"),
            scenario_id=scenario_id,
            max_replans=int((task_spec.get("budget") or {}).get("max_replans") or 1),
            steps=candidate_steps,
            precedent_ids=[int(precedent["id"])],
            generation_source="verified_precedent",
        )
        if not validate_plan_graph(candidate.model_dump(mode="json"), task_spec):
            steps = candidate_steps
            precedent_ids = [int(precedent["id"])]
            generation_source = "verified_precedent"
            break
    graph = PlanGraph(
        task_id=str(task_spec.get("task_id") or "unknown"),
        scenario_id=scenario_id,
        max_replans=int((task_spec.get("budget") or {}).get("max_replans") or 1),
        steps=steps,
        precedent_ids=precedent_ids,
        generation_source=generation_source,
    )
    errors = validate_plan_graph(graph.model_dump(mode="json"), task_spec)
    if errors:
        raise ValueError("Invalid generated PlanGraph: " + "; ".join(errors))
    return graph.model_dump(mode="json")


def validate_plan_graph(
    plan: Mapping[str, Any],
    task_spec: Mapping[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    steps = [dict(item) for item in plan.get("steps") or []]
    ids = [str(item.get("step_id") or "") for item in steps]
    if not steps:
        return ["plan has no steps"]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("step ids must be non-empty and unique")
    known = set(ids)
    by_id = {str(item.get("step_id") or ""): item for item in steps}
    for step in steps:
        step_id = str(step.get("step_id") or "")
        specialist_id = str(step.get("specialist") or "")
        specialist = SPECIALIST_CATALOG.get(specialist_id)
        if specialist is None:
            errors.append(f"{step_id}: unknown specialist {specialist_id}")
            continue
        dependencies = set(step.get("dependencies") or [])
        if not dependencies <= known:
            errors.append(f"{step_id}: unknown dependencies {sorted(dependencies - known)}")
        tool = step.get("tool")
        if tool and tool not in specialist.allowed_tools:
            errors.append(f"{step_id}: tool {tool} is outside {specialist_id} capability")
        side_effect = str(step.get("side_effect") or "read")
        if side_effect not in specialist.allowed_side_effects:
            errors.append(f"{step_id}: side effect {side_effect} is not allowed")
        if side_effect in {"write", "external"} and not step.get("approval_required"):
            errors.append(f"{step_id}: side-effecting step must require approval evidence")
        if side_effect in {"write", "external"} and not step.get("compensation"):
            errors.append(f"{step_id}: side-effecting step must declare compensation")
        if side_effect in {"write", "external"} and not step.get("idempotency_key_template"):
            errors.append(f"{step_id}: side-effecting step must declare an idempotency key")
        if float(step.get("timeout_seconds") or 0) <= 0:
            errors.append(f"{step_id}: timeout_seconds must be greater than zero")
        if int(step.get("retry_limit") or 0) < 0:
            errors.append(f"{step_id}: retry_limit cannot be negative")
        tool = str(step.get("tool") or "")
        if tool:
            try:
                from app.agent.tool_gateway import TOOL_SPECS

                tool_spec = TOOL_SPECS.get(tool)
            except Exception:
                tool_spec = None
            if tool_spec is None:
                errors.append(f"{step_id}: tool {tool} is not registered in Tool Gateway")
            else:
                required_args = set((tool_spec.input_schema or {}).get("required") or [])
                provided_args = set((step.get("arguments") or {}).keys())
                if not required_args <= provided_args:
                    errors.append(
                        f"{step_id}: missing tool arguments {sorted(required_args - provided_args)}"
                    )
    dependencies_by_step = {
        str(step.get("step_id") or ""): {
            str(item) for item in step.get("dependencies") or []
        }
        for step in steps
    }
    if _has_dependency_cycle(dependencies_by_step):
        errors.append("plan dependencies must form an acyclic graph")
    else:
        for step in steps:
            if str(step.get("side_effect") or "read") not in {"write", "external"}:
                continue
            ancestors = _dependency_ancestors(
                str(step.get("step_id") or ""), dependencies_by_step
            )
            if not any(
                str(by_id.get(ancestor, {}).get("side_effect") or "read")
                in {"read", "decision"}
                for ancestor in ancestors
            ):
                errors.append(
                    f"{step.get('step_id')}: side-effecting step must depend on a read/decision step"
                )
    if task_spec:
        max_steps = int((task_spec.get("budget") or {}).get("max_steps") or 12)
        if len(steps) > max_steps:
            errors.append("plan exceeds task step budget")
        produced_evidence = {
            str(predicate)
            for step in steps
            for predicate in step.get("expected_evidence") or []
        }
        required_evidence = {
            str(predicate)
            for criterion in task_spec.get("success_criteria") or []
            if criterion.get("mandatory", True)
            for predicate in criterion.get("required_evidence") or []
        }
        missing_evidence = sorted(required_evidence - produced_evidence)
        if missing_evidence:
            errors.append(
                f"plan does not produce success evidence {missing_evidence}"
            )
    return errors


def evaluate_plan_conditions(
    conditions: list[Mapping[str, Any]] | None,
    *,
    state: Mapping[str, Any],
    step_output: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    predicates = {
        str(item.get("predicate"))
        for item in (state.get("evidence_graph") or {}).get("claims") or []
    }
    merged_state = {**dict(state), **dict(step_output or {})}
    for raw in conditions or []:
        condition = PlanCondition(**dict(raw))
        if condition.source == ConditionSource.EVIDENCE:
            actual = condition.path in predicates
        elif condition.source == ConditionSource.STEP_OUTPUT:
            actual = _resolve_path(step_output or {}, condition.path)
        else:
            actual = _resolve_path(merged_state, condition.path)
        if not _condition_matches(condition.operator, actual, condition.expected):
            failures.append(
                {
                    "condition_id": condition.condition_id,
                    "code": condition.failure_code,
                    "message": condition.description
                    or f"Condition {condition.condition_id} was not satisfied",
                }
            )
    return failures


def _condition_matches(operator: ConditionOperator, actual: Any, expected: Any) -> bool:
    if operator == ConditionOperator.EXISTS:
        return bool(actual) if isinstance(actual, bool) else actual not in (None, "")
    if operator == ConditionOperator.TRUTHY:
        return bool(actual)
    if operator == ConditionOperator.EQUALS:
        return actual == expected
    if operator == ConditionOperator.NOT_EQUALS:
        return actual != expected
    if operator == ConditionOperator.IN:
        return actual in (expected or [])
    try:
        if operator == ConditionOperator.GTE:
            return actual >= expected
        if operator == ConditionOperator.LTE:
            return actual <= expected
    except TypeError:
        return False
    return False


def _resolve_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in str(path).split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _dependency_ancestors(
    step_id: str,
    dependencies: Mapping[str, set[str]],
) -> set[str]:
    result: set[str] = set()
    pending = list(dependencies.get(step_id, set()))
    while pending:
        dependency = pending.pop()
        if dependency in result:
            continue
        result.add(dependency)
        pending.extend(dependencies.get(dependency, set()))
    return result


def _has_dependency_cycle(dependencies: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> bool:
        if step_id in visiting:
            return True
        if step_id in visited:
            return False
        visiting.add(step_id)
        for dependency in dependencies.get(step_id, set()):
            if dependency in dependencies and visit(dependency):
                return True
        visiting.remove(step_id)
        visited.add(step_id)
        return False

    return any(visit(step_id) for step_id in dependencies)


def mark_plan_steps(
    plan: Mapping[str, Any] | None,
    statuses: Mapping[str, str],
    *,
    graph_status: str | None = None,
) -> dict[str, Any]:
    updated = dict(plan or {})
    updated["steps"] = [
        {**dict(step), "status": statuses.get(str(step.get("step_id")), step.get("status", "planned"))}
        for step in updated.get("steps") or []
    ]
    if graph_status:
        updated["status"] = graph_status
    return updated


def replan_plan_graph(
    plan: Mapping[str, Any],
    *,
    failed_step_ids: list[str],
    reason_codes: list[str],
) -> dict[str, Any]:
    """Create a new bounded revision and reset failed steps plus descendants."""
    updated = dict(plan)
    max_replans = int(updated.get("max_replans") or 1)
    replan_count = int(updated.get("replan_count") or 0)
    if replan_count >= max_replans:
        raise ValueError("Plan replan budget exhausted")
    dependencies = {
        str(step.get("step_id") or ""): {
            str(item) for item in step.get("dependencies") or []
        }
        for step in updated.get("steps") or []
    }
    reset = set(failed_step_ids)
    changed = True
    while changed:
        changed = False
        for step_id, step_dependencies in dependencies.items():
            if step_id not in reset and step_dependencies & reset:
                reset.add(step_id)
                changed = True
    updated["steps"] = [
        {
            **dict(step),
            "status": (
                "planned"
                if str(step.get("step_id") or "") in reset
                else step.get("status", "planned")
            ),
        }
        for step in updated.get("steps") or []
    ]
    updated["revision"] = int(updated.get("revision") or 1) + 1
    updated["replan_count"] = replan_count + 1
    updated["status"] = "running"
    updated["generation_source"] = "bounded_replan"
    updated["replan_reason_codes"] = list(reason_codes)
    return updated


def specialist_catalog_report() -> dict[str, Any]:
    return {
        "specialists": [item.model_dump(mode="json") for item in SPECIALIST_CATALOG.values()],
        "invariant": "Specialists may propose evidence and decisions; only governed executors perform writes.",
    }


def _refund_steps() -> list[PlanStep]:
    return [
        PlanStep(
            step_id="understand",
            specialist="supervisor",
            capability="task_understanding",
            side_effect="none",
        ),
        PlanStep(
            step_id="load_order",
            specialist="operations_specialist",
            capability="order_lookup",
            tool="lookup_order",
            arguments={"order_id": "$state.order_id"},
            dependencies=["understand"],
            expected_output_schema={"required": ["order_id", "order_amount", "currency"]},
            postconditions=[
                {
                    "condition_id": "order_loaded",
                    "source": "state",
                    "path": "order_id",
                    "operator": "truthy",
                    "failure_code": "TOOL_RESULT_INCONSISTENT",
                }
            ],
            expected_evidence=["order.identity", "order.amount", "order.currency"],
        ),
        PlanStep(
            step_id="validate_return",
            specialist="inventory_specialist",
            capability="return_validation",
            tool="validate_return",
            arguments={"order_id": "$state.order_id"},
            dependencies=["load_order"],
            expected_evidence=[
                "return.authorization",
                "return.received",
                "return.inspection",
            ],
            side_effect="decision",
        ),
        PlanStep(
            step_id="inspect_inventory",
            specialist="inventory_specialist",
            capability="inventory_consistency",
            tool="inspect_return_inventory",
            arguments={"order_id": "$state.order_id"},
            dependencies=["validate_return"],
            expected_evidence=["inventory.state"],
            side_effect="decision",
        ),
        PlanStep(
            step_id="assess_risk",
            specialist="risk_specialist",
            capability="risk_assessment",
            tool="check_risk_level",
            arguments={
                "order_id": "$state.order_id",
                "amount": "$state.order_amount",
                "user_id": "$state.user_id",
            },
            dependencies=["load_order", "validate_return", "inspect_inventory"],
            expected_evidence=["risk.score", "policy.decision"],
            side_effect="decision",
        ),
        PlanStep(
            step_id="verify",
            specialist="verifier",
            capability="evidence_verification",
            dependencies=["assess_risk"],
            preconditions=["order_verified", "risk_evaluated"],
            side_effect="decision",
        ),
        PlanStep(
            step_id="approve",
            specialist="verifier",
            capability="approval_validation",
            dependencies=["verify"],
            expected_evidence=["approval.decision"],
            side_effect="decision",
        ),
        PlanStep(
            step_id="execute_finance",
            specialist="executor",
            capability="refund_finance_saga",
            tool="execute_refund",
            arguments={
                "order_id": "$state.order_id",
                "ticket_id": "$state.ticket_id",
                "amount": "$state.order_amount",
            },
            dependencies=["approve"],
            preconditions=["verification_passed"],
            postconditions=[
                {
                    "condition_id": "finance_execution_succeeded",
                    "source": "state",
                    "path": "refund_success",
                    "operator": "truthy",
                    "failure_code": "SUCCESS_CRITERIA_NOT_MET",
                }
            ],
            expected_evidence=["finance.saga_status"],
            side_effect="write",
            approval_required=True,
            compensation={
                "strategy": "tool",
                "tool": "erp_reverse_document",
                "arguments": {
                    "source_document_id": "$state.credit_memo_id",
                    "reason_code": "AGENT_COMPENSATION",
                    "amount": "$state.order_amount",
                },
            },
            idempotency_key_template="refund:{tenant_id}:{order_id}:{amount}",
        ),
        PlanStep(
            step_id="restore_inventory",
            specialist="executor",
            capability="restore_return_inventory",
            tool="restore_return_inventory",
            arguments={
                "order_id": "$state.order_id",
                "rma_id": "$state.return_authorization_id",
            },
            dependencies=["execute_finance", "inspect_inventory"],
            expected_evidence=["inventory.movement"],
            postconditions=[
                {
                    "condition_id": "inventory_restoration_succeeded",
                    "source": "state",
                    "path": "inventory_restoration.success",
                    "operator": "truthy",
                    "failure_code": "SUCCESS_CRITERIA_NOT_MET",
                }
            ],
            side_effect="write",
            approval_required=True,
            compensation={
                "strategy": "tool",
                "tool": "reverse_inventory_movement",
                "arguments": {"movement_id": "$state.inventory_movement_id"},
            },
            idempotency_key_template="inventory-return:{tenant_id}:{order_id}:{rma_id}",
        ),
        PlanStep(
            step_id="reconcile",
            specialist="finance_specialist",
            capability="reconcile_cross_system_result",
            tool="erp_query_doctype",
            arguments={"doctype": "document_flow", "filter": "$state.order_id"},
            dependencies=["execute_finance", "restore_inventory"],
            expected_evidence=["finance.reconciliation"],
            side_effect="read",
        ),
        PlanStep(
            step_id="notify",
            specialist="executor",
            capability="notify_stakeholders",
            tool="send_notification",
            arguments={
                "order_id": "$state.order_id",
                "refund_id": "$state.refund_id",
            },
            dependencies=["reconcile"],
            expected_evidence=["notification.delivery"],
            side_effect="external",
            approval_required=True,
            compensation={"strategy": "manual_review"},
            idempotency_key_template="notification:{tenant_id}:{refund_id}",
        ),
        PlanStep(
            step_id="final_reconcile",
            specialist="verifier",
            capability="verify_success_criteria",
            dependencies=["notify"],
            expected_evidence=["task.final_reconciliation"],
            side_effect="decision",
        ),
    ]


def _permission_steps() -> list[PlanStep]:
    return [
        PlanStep(step_id="understand", specialist="supervisor", capability="task_understanding", side_effect="none"),
        PlanStep(step_id="least_privilege", specialist="access_specialist", capability="least_privilege_analysis", dependencies=["understand"], expected_evidence=["request.system", "request.permission_level", "policy.decision"], side_effect="decision"),
        PlanStep(
            step_id="create_request",
            specialist="executor",
            capability="create_access_request",
            tool="create_permission_request",
            arguments={
                "requester_id": "$state.user_id",
                "system": "$state.scenario_slots.system",
                "permission_level": "$state.scenario_slots.permission_level",
            },
            dependencies=["least_privilege"],
            expected_evidence=["request.id"],
            side_effect="write",
            approval_required=True,
            compensation={"strategy": "manual_review"},
            idempotency_key_template="access-request:{tenant_id}:{requester_id}:{system}:{permission_level}",
        ),
        PlanStep(step_id="approve", specialist="verifier", capability="approval_validation", dependencies=["create_request"], expected_evidence=["approval.decision"], side_effect="decision"),
        PlanStep(step_id="notify", specialist="executor", capability="notify_requester", tool="send_notification", arguments={"order_id": "$state.business_request.requestId", "refund_id": "$state.business_request.status"}, dependencies=["approve"], expected_evidence=["notification.delivery"], side_effect="external", approval_required=True, compensation={"strategy": "manual_review"}, idempotency_key_template="access-notification:{tenant_id}:{request_id}"),
    ]


def _reimbursement_steps() -> list[PlanStep]:
    return [
        PlanStep(step_id="understand", specialist="supervisor", capability="task_understanding", side_effect="none"),
        PlanStep(step_id="validate_expense", specialist="expense_specialist", capability="expense_validation", dependencies=["understand"], expected_evidence=["request.amount", "request.category", "policy.decision"], side_effect="decision"),
        PlanStep(
            step_id="create_request",
            specialist="executor",
            capability="create_expense_request",
            tool="create_reimbursement_request",
            arguments={
                "requester_id": "$state.user_id",
                "amount": "$state.scenario_slots.amount",
                "category": "$state.scenario_slots.category",
            },
            dependencies=["validate_expense"],
            expected_evidence=["request.id"],
            side_effect="write",
            approval_required=True,
            compensation={"strategy": "manual_review"},
            idempotency_key_template="expense-request:{tenant_id}:{requester_id}:{amount}:{category}",
        ),
        PlanStep(step_id="approve", specialist="verifier", capability="approval_validation", dependencies=["create_request"], expected_evidence=["approval.decision"], side_effect="decision"),
        PlanStep(step_id="notify", specialist="executor", capability="notify_requester", tool="send_notification", arguments={"order_id": "$state.business_request.requestId", "refund_id": "$state.business_request.status"}, dependencies=["approve"], expected_evidence=["notification.delivery"], side_effect="external", approval_required=True, compensation={"strategy": "manual_review"}, idempotency_key_template="expense-notification:{tenant_id}:{request_id}"),
    ]


def _default_steps() -> list[PlanStep]:
    return [PlanStep(step_id="understand", specialist="supervisor", capability="task_understanding", side_effect="none")]
