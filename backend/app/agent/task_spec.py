"""Canonical task contract shared by routing, planning, verification, and eval."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Mapping

from pydantic import BaseModel, Field

from app.agent.scenario_registry import ScenarioConfig
from app.agent.utils import get_state_val


class TaskRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConstraintSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ConstraintViolationAction(str, Enum):
    RECORD = "record"
    REPLAN = "replan"
    HUMAN_REVIEW = "human_review"
    BLOCK = "block"


class BusinessConstraint(BaseModel):
    constraint_id: str
    description: str
    rule_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    source: str = "scenario_policy"
    severity: ConstraintSeverity = ConstraintSeverity.BLOCKING
    on_violation: ConstraintViolationAction = ConstraintViolationAction.BLOCK


class TaskEntity(BaseModel):
    entity_type: str
    entity_id: str
    source: str = "user_input"
    attributes: dict[str, Any] = Field(default_factory=dict)


class SuccessCriterion(BaseModel):
    criterion_id: str
    description: str
    required_evidence: list[str] = Field(default_factory=list)
    mandatory: bool = True


class TaskBudget(BaseModel):
    max_steps: int = 12
    max_replans: int = 1
    deadline_ms: int = 30000
    max_llm_calls: int = 4
    max_cost_usd: float = 0.05
    max_tool_calls: int = 16
    max_failures: int = 2


class TaskSpec(BaseModel):
    schema_version: str = "2.0"
    task_id: str
    thread_id: str
    tenant_id: str
    requester_id: str
    scenario_id: str
    goal: str
    entities: list[TaskEntity] = Field(default_factory=list)
    constraints: list[BusinessConstraint] = Field(default_factory=list)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    risk_level: TaskRisk = TaskRisk.MEDIUM
    deadline: str | None = None
    budget: TaskBudget = Field(default_factory=TaskBudget)
    missing_information: list[str] = Field(default_factory=list)
    source_message_hash: str
    created_at: str


_SUCCESS_CRITERIA: dict[str, list[SuccessCriterion]] = {
    "refund": [
        SuccessCriterion(
            criterion_id="order_verified",
            description="订单身份、金额和币种已由可信数据源确认",
            required_evidence=["order.identity", "order.amount", "order.currency"],
        ),
        SuccessCriterion(
            criterion_id="return_received",
            description="退货授权、仓库收货和质检结果支持本次退款",
            required_evidence=[
                "return.authorization",
                "return.received",
                "return.inspection",
            ],
        ),
        SuccessCriterion(
            criterion_id="inventory_restored",
            description="可重新入库商品的库存、批次和移动凭证已恢复并核对",
            required_evidence=["inventory.state", "inventory.movement"],
        ),
        SuccessCriterion(
            criterion_id="risk_evaluated",
            description="退款风险与历史事实已经评估",
            required_evidence=["risk.score", "policy.decision"],
        ),
        SuccessCriterion(
            criterion_id="approval_satisfied",
            description="需要人工审批时存在有效审批证据",
            required_evidence=["approval.decision"],
        ),
        SuccessCriterion(
            criterion_id="finance_completed",
            description="贷项凭证和清账完成，或失败后补偿成功",
            required_evidence=["finance.saga_status", "finance.reconciliation"],
        ),
        SuccessCriterion(
            criterion_id="notification_delivered",
            description="客户和业务干系人已收到幂等通知",
            required_evidence=["notification.delivery"],
        ),
        SuccessCriterion(
            criterion_id="cross_system_consistent",
            description="订单、退货、库存、财务和通知的最终状态一致",
            required_evidence=["task.final_reconciliation"],
        ),
    ],
    "permission_request": [
        SuccessCriterion(
            criterion_id="least_privilege_checked",
            description="目标系统和最小权限范围明确",
            required_evidence=["request.system", "request.permission_level"],
        ),
        SuccessCriterion(
            criterion_id="access_request_persisted",
            description="权限申请已持久化并进入正确审批链",
            required_evidence=["request.id", "policy.decision"],
        ),
        SuccessCriterion(
            criterion_id="access_approval_completed",
            description="审批决定已持久化并通知申请人",
            required_evidence=["approval.decision", "notification.delivery"],
        ),
    ],
    "reimbursement": [
        SuccessCriterion(
            criterion_id="expense_evidence_checked",
            description="金额、类别和费用说明完整",
            required_evidence=["request.amount", "request.category"],
        ),
        SuccessCriterion(
            criterion_id="expense_request_persisted",
            description="报销申请已持久化并进入正确审批链",
            required_evidence=["request.id", "policy.decision"],
        ),
        SuccessCriterion(
            criterion_id="expense_approval_completed",
            description="财务审批结果已持久化并通知申请人",
            required_evidence=["approval.decision", "notification.delivery"],
        ),
    ],
}


_CONSTRAINTS: dict[str, list[BusinessConstraint]] = {
    "refund": [
        BusinessConstraint(
            constraint_id="refund_amount_consistency",
            description="退款、订单、退货和应收未清项金额及币种必须一致",
            rule_id="REFUND_AMOUNT_CURRENCY_MATCH",
            parameters={"tolerance": "0.00"},
        ),
        BusinessConstraint(
            constraint_id="physical_return_received",
            description="需要实物退回时，退货单必须已收货并完成质检",
            rule_id="RETURN_RECEIVED_AND_INSPECTED",
        ),
        BusinessConstraint(
            constraint_id="no_duplicate_refund",
            description="同一订单和业务原因不得重复退款",
            rule_id="REFUND_IDEMPOTENCY",
        ),
        BusinessConstraint(
            constraint_id="financial_write_authorized",
            description="贷项凭证、清账和库存写入必须具备策略与审批证据",
            rule_id="GOVERNED_WRITE_AUTHORIZATION",
            on_violation=ConstraintViolationAction.HUMAN_REVIEW,
        ),
    ],
    "permission_request": [
        BusinessConstraint(
            constraint_id="least_privilege",
            description="申请权限必须满足最小权限原则",
            rule_id="IAM_LEAST_PRIVILEGE",
        ),
        BusinessConstraint(
            constraint_id="segregation_of_duties",
            description="权限组合不得产生职责分离冲突",
            rule_id="IAM_SOD_CONFLICT",
            on_violation=ConstraintViolationAction.HUMAN_REVIEW,
        ),
    ],
    "reimbursement": [
        BusinessConstraint(
            constraint_id="expense_evidence_complete",
            description="费用金额、类别、说明和票据必须完整一致",
            rule_id="EXPENSE_EVIDENCE_COMPLETE",
        ),
        BusinessConstraint(
            constraint_id="expense_policy_limit",
            description="报销必须符合类别额度、币种和审批矩阵",
            rule_id="EXPENSE_POLICY_LIMIT",
            on_violation=ConstraintViolationAction.HUMAN_REVIEW,
        ),
    ],
}


def build_task_spec(state: Mapping[str, Any], scenario: ScenarioConfig) -> dict[str, Any]:
    thread_id = str(get_state_val(state, "thread_id", "unknown") or "unknown")
    source_message = _latest_user_message(state)
    goal = _normalized_goal(scenario.id, state)
    entities = _entities_from_state(state)
    missing = _missing_information(scenario.id, state)
    risk = TaskRisk.HIGH if scenario.id in {"refund", "permission_request"} else TaskRisk.MEDIUM
    task = TaskSpec(
        task_id=f"task:{thread_id}:{scenario.id}",
        thread_id=thread_id,
        tenant_id=str(get_state_val(state, "tenant_id", "default") or "default"),
        requester_id=str(get_state_val(state, "user_id", "anonymous") or "anonymous"),
        scenario_id=scenario.id,
        goal=goal,
        entities=entities,
        constraints=[
            *_CONSTRAINTS.get(scenario.id, []),
            BusinessConstraint(
                constraint_id="tool_allowlist",
                description="只能使用场景白名单中的工具，且所有调用必须经过 Tool Gateway",
                rule_id="TOOL_ALLOWLIST",
                parameters={"tools": list(scenario.tools)},
            ),
            *[
                BusinessConstraint(
                    constraint_id=f"policy_{name}",
                    description=f"执行必须满足策略 {name}",
                    rule_id=name,
                )
                for name in scenario.policies
            ],
        ],
        success_criteria=_SUCCESS_CRITERIA.get(
            scenario.id,
            [
                SuccessCriterion(
                    criterion_id="task_completed",
                    description="场景声明的业务目标已完成",
                )
            ],
        ),
        risk_level=risk,
        missing_information=missing,
        source_message_hash=hashlib.sha256(source_message.encode("utf-8")).hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return task.model_dump(mode="json")


def enrich_task_spec(
    task_spec: Mapping[str, Any] | None,
    *,
    slots: Mapping[str, Any],
) -> dict[str, Any]:
    if not task_spec:
        return {}
    enriched = dict(task_spec)
    entities = [dict(item) for item in enriched.get("entities") or []]
    known = {(item.get("entity_type"), str(item.get("entity_id"))) for item in entities}
    for key, value in slots.items():
        if value in (None, "", 0):
            continue
        identity = (f"slot:{key}", str(value))
        if identity not in known:
            entities.append(
                {
                    "entity_type": f"slot:{key}",
                    "entity_id": str(value),
                    "source": "slot_extraction",
                    "attributes": {"value": value},
                }
            )
    enriched["entities"] = entities
    enriched["missing_information"] = _missing_information_from_entities(
        str(enriched.get("scenario_id") or ""),
        entities,
    )
    return enriched


def enrich_task_spec_from_state(
    task_spec: Mapping[str, Any] | None,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Add canonical ERP entities discovered after the initial task contract."""
    if not task_spec:
        return {}
    enriched = dict(task_spec)
    entities = [dict(item) for item in enriched.get("entities") or []]
    known = {(str(item.get("entity_type")), str(item.get("entity_id"))) for item in entities}

    def add(entity_type: str, entity_id: Any, source: str, attributes: Mapping[str, Any] | None = None) -> None:
        if entity_id in (None, ""):
            return
        identity = (entity_type, str(entity_id))
        if identity in known:
            return
        entities.append(
            TaskEntity(
                entity_type=entity_type,
                entity_id=str(entity_id),
                source=source,
                attributes=dict(attributes or {}),
            ).model_dump(mode="json")
        )
        known.add(identity)

    order_id = get_state_val(state, "order_id", "")
    add("order", order_id, "canonical_order")
    add("open_item", get_state_val(state, "open_item_id", ""), "canonical_finance")
    detail = dict(get_state_val(state, "order_detail", {}) or {})
    context = dict(detail.get("erpContext") or {})
    collections = {
        "return_authorization": ("returnAuthorizations", "rma_id", "return_service"),
        "return_inspection": ("returnInspections", "inspection_id", "warehouse_quality"),
        "inventory_movement": ("inventoryMovements", "movement_id", "inventory_service"),
        "inventory_batch": ("inventoryBatches", "batch_id", "inventory_service"),
        "open_item": ("openItems", "open_item_id", "finance_service"),
    }
    for entity_type, (collection, id_field, source) in collections.items():
        for item in context.get(collection) or []:
            add(entity_type, item.get(id_field), source, item)
    for line in context.get("lines") or []:
        add("product", line.get("product_id") or line.get("sku"), "order_service", line)

    enriched["entities"] = entities
    enriched["missing_information"] = _missing_information_from_entities(
        str(enriched.get("scenario_id") or ""), entities
    )
    return enriched


def _latest_user_message(state: Mapping[str, Any]) -> str:
    for message in reversed(list(get_state_val(state, "messages", []) or [])):
        if getattr(message, "type", None) == "human":
            return str(getattr(message, "content", "") or "")
        if isinstance(message, Mapping) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _entities_from_state(state: Mapping[str, Any]) -> list[TaskEntity]:
    mappings = {
        "order": get_state_val(state, "order_id", ""),
        "ticket": get_state_val(state, "ticket_id", ""),
        "connector": get_state_val(state, "connector_id", ""),
    }
    return [
        TaskEntity(entity_type=kind, entity_id=str(value))
        for kind, value in mappings.items()
        if value
    ]


def _missing_information(scenario_id: str, state: Mapping[str, Any]) -> list[str]:
    if scenario_id == "refund" and not get_state_val(state, "order_id", ""):
        return ["order_id"]
    return []


def _missing_information_from_entities(
    scenario_id: str,
    entities: list[Mapping[str, Any]],
) -> list[str]:
    present = {str(item.get("entity_type") or "") for item in entities}
    if scenario_id == "refund":
        required = {
            "order": "order_id",
            "return_authorization": "return_authorization",
            "return_inspection": "return_inspection",
            "open_item": "open_item",
        }
        return [label for entity_type, label in required.items() if entity_type not in present]
    return []


def _normalized_goal(scenario_id: str, state: Mapping[str, Any]) -> str:
    if scenario_id == "refund":
        order_id = str(get_state_val(state, "order_id", "") or "unknown")
        return f"Complete a policy-compliant, fully reconciled refund for order {order_id}."
    if scenario_id == "permission_request":
        return "Create and govern a least-privilege enterprise access request."
    if scenario_id == "reimbursement":
        return "Validate, approve, and persist a policy-compliant reimbursement request."
    return "Complete the routed enterprise task under policy and audit controls."
